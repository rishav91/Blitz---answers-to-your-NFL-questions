"""router_node — classifies intent: factual / analytical / predictive (FR-0.1)."""

from typing import Literal

from pydantic import BaseModel, Field

from graph.llm import get_chat_model
from graph.observability import get_requests_counter, traced_node
from graph.state import GraphState

ROUTER_PROMPT = """Classify this NFL question into exactly one category:

- factual: a single-hop lookup about one specific game — its final score, week, \
venue, surface, roof, or a single stat from that one game (e.g. a team's total \
yards or passer rating in a named game). Example: "What was the final score when \
the 49ers played the Cowboys in the 2023 playoffs?"
- analytical: either (a) a multi-hop lookup where a second game depends on the \
result of a first ("the team that beat X in week Y — how far did they go in the \
playoffs"), or (b) computing or comparing stats aggregated across multiple games \
or between teams — points per game, yards per game, turnover differential over a \
season, third-down %, red-zone efficiency, or standings.
- predictive: asks for a speculation/prediction about a hypothetical or future \
matchup ("who do you think wins if X and Y played again").

Question: {question}"""


class RouterDecision(BaseModel):
    intent: Literal["factual", "analytical", "predictive"] = Field(
        description="Which of the three branches this question belongs to"
    )


@traced_node("router_node")
def router_node(state: GraphState) -> dict:
    model = get_chat_model().with_structured_output(RouterDecision)
    decision = model.invoke(ROUTER_PROMPT.format(question=state["question"]))
    get_requests_counter().add(1, {"intent": decision.intent})
    return {"intent": decision.intent}


def route_from_intent(state: GraphState) -> str:
    return {
        "factual": "retrieval_node",
        "analytical": "agentic_retrieval_node",
        "predictive": "predictive_stub_node",
    }[state["intent"]]
