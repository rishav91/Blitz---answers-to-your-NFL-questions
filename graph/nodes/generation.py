"""generation_node — answers from the retrieved chunk, no tools on the factual path
(FR-1.x; the factual path stays tool-free per PRD.md §Scope, governing rule)."""

from graph.llm import get_chat_model
from graph.state import GraphState

GENERATION_PROMPT = """Answer the question using ONLY the information in the context below. \
If the specific number or fact the question asks for is not present in the context, say so \
explicitly rather than estimating or guessing — never state a number that isn't in the context.

Note: the context's `season` is the year the NFL season started, not the calendar year of \
every game in it — that season's playoffs, including the Super Bowl, are played in \
January/February of the FOLLOWING calendar year. A chunk whose season is one less than a \
calendar year named in the question can still be the right game (e.g. a Super Bowl played in \
Jan/Feb 2023 is filed under season=2022) — don't refuse to use it for that reason alone.

Context:
{context}

Question: {question}"""

# Fixed correction template (AI-ARCHITECTURE.md §Instruction/data separation) — the only
# free-form text fed back in is the reflection reason, never an instruction itself.
CORRECTION_TEMPLATE = (
    "You cited a number not present in the source — rewrite using only the text below. "
    "(Reason flagged: {reason})"
)


def generation_node(state: GraphState) -> dict:
    model = get_chat_model()
    prompt = GENERATION_PROMPT.format(context=state["context"], question=state["question"])
    if state.get("last_failure") == "grounding":
        correction = CORRECTION_TEMPLATE.format(reason=state.get("failure_reason", ""))
        prompt = f"{correction}\n\n{prompt}"

    response = model.invoke(prompt)
    return {"draft_answer": response.content}
