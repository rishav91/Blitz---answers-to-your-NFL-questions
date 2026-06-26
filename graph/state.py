"""Shared LangGraph state — see ARCHITECTURE.md for the node/field mapping."""

from typing import Literal, Optional

from typing_extensions import TypedDict

Intent = Literal["factual", "analytical", "predictive"]
FailureKind = Literal["grounding", "coverage"]

# NFR-1: reflection's grounding-failure and coverage-failure edges share one
# retry budget, max 2 retries total across both.
MAX_REFLECTION_RETRIES = 2


class GraphState(TypedDict, total=False):
    question: str
    intent: Optional[Intent]

    # retrieval_node's hybrid split (FR-1.1)
    season: Optional[int]
    game_type: Optional[Literal["REG", "POST"]]
    week: Optional[int]
    semantic_query: Optional[str]
    context: str

    # generation_node
    draft_answer: str

    # reflection_node (FR-4.x, NFR-1)
    retry_count: int
    last_failure: Optional[FailureKind]
    failure_reason: Optional[str]

    # response_node
    final_answer: str
