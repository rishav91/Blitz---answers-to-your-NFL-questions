"""reflection_node — grounding + coverage check on the drafted answer (FR-4.x, NFR-1)."""

from pydantic import BaseModel, Field

from graph.llm import get_chat_model
from graph.state import MAX_REFLECTION_RETRIES, GraphState

REFLECTION_PROMPT = """Check this drafted answer against the context it was generated from.

Question: {question}

Context (the only source the answer is allowed to draw from):
{context}

Drafted answer:
{draft_answer}

Judge two things:
1. grounded: every specific number/fact the answer states is either present in the \
context, or the answer explicitly says the information isn't available. Mark this \
false ONLY if the answer states a number/fact that doesn't appear in the context.
2. covers_question: the context contains the specific game the question needs, and \
the drafted answer correctly identifies it — not whether the chunk explicitly spells \
out every word of the question. If the answer reasons through a season/calendar-year \
conversion (the context's `season` is the year the NFL season started; a season's \
playoffs, including the Super Bowl, are played in Jan/Feb of the FOLLOWING calendar \
year) and arrives at one specific, correctly-grounded game matching the teams and \
timeframe asked about, that counts as covered even if the chunk's season number isn't \
the literal calendar year in the question. Mark this false only when the question \
doesn't pin down which game (e.g. doesn't say regular season vs. playoffs, and these \
teams could plausibly have met more than once in the stated scope) AND the context \
either has no candidate addressing that ambiguity or has multiple equally-plausible \
candidates that the answer didn't distinguish between.

Give a short reason for your judgment either way."""


class ReflectionJudgment(BaseModel):
    grounded: bool
    covers_question: bool
    reason: str = Field(description="One or two sentences explaining the judgment")


def reflection_node(state: GraphState) -> dict:
    model = get_chat_model().with_structured_output(ReflectionJudgment)
    judgment = model.invoke(
        REFLECTION_PROMPT.format(
            question=state["question"],
            context=state["context"],
            draft_answer=state["draft_answer"],
        )
    )

    if judgment.grounded and judgment.covers_question:
        return {"last_failure": None, "final_answer": state["draft_answer"]}

    retry_count = state.get("retry_count", 0)
    if retry_count >= MAX_REFLECTION_RETRIES:
        caveat = (
            "\n\n(Note: this answer could not be fully verified after retrying — "
            f"treat with caution: {judgment.reason})"
        )
        return {"last_failure": None, "final_answer": state["draft_answer"] + caveat}

    failure_kind = "grounding" if not judgment.grounded else "coverage"
    return {"retry_count": retry_count + 1, "last_failure": failure_kind, "failure_reason": judgment.reason}


def route_from_reflection(state: GraphState) -> str:
    return {
        "grounding": "generation_node",
        "coverage": "retrieval_node",
    }.get(state.get("last_failure"), "response_node")
