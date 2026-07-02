"""agentic_retrieval_node — retrieve -> assess_sufficiency -> refine-and-retry
loop (FR-2.1), capped per NFR-2.

The whole sufficiency loop lives INSIDE this node: it runs before generation
and asks "do I have enough context to even try", which is a different question
at a different time than reflection_node's post-generation "did the answer
hold up" — the two loops must never merge (ADR-004). Hop N+1's query is
produced by an LLM that has read hop N's results, which is what makes this
agentic rather than a fixed pipeline: "the team that beat X in week Y" gets
resolved from hop 1's chunk before hop 2 searches for that team's playoffs.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from graph.llm import get_chat_model
from graph.nodes.retrieval import format_context
from graph.observability import traced_node
from graph.state import MAX_SUFFICIENCY_RETRIES, GraphState
from graph.store import build_where, query_games

N_RESULTS_PER_HOP = 3

FIRST_HOP_PROMPT = """Extract structured filter fields for the FIRST retrieval hop of this \
NFL question, and put everything else (team names, anything identifying the specific games) \
into semantic_query.

The question may need facts you don't have yet (e.g. "the team that beat X — how far did \
they go?"). Target this first hop at the part answerable directly (the X game); later hops \
will handle what depends on its result.

Only set season/game_type/week if the question states them explicitly and unambiguously — \
leave a field unset rather than guess. `season` is the year the NFL season started (a Super \
Bowl played in February 2023 belongs to the 2022 season). `game_type` is "REG" for regular \
season or "POST" for playoffs/Super Bowl.
{retry_hint}
Question: {question}"""

COVERAGE_RETRY_HINT = (
    "\nA previous pass through this loop produced an answer that didn't cover the question: "
    "\"{reason}\" Take a different angle on the first hop rather than repeating the same query.\n"
)

ASSESS_PROMPT = """You are deciding whether the context retrieved so far is sufficient to \
answer an NFL question, BEFORE any answer is drafted.

Question: {question}

Context retrieved so far:
{context}

If every fact the question needs is present, mark sufficient=true. If not, mark it false \
and produce the NEXT retrieval query: resolve what you can from the context first (e.g. if \
the context shows which team won the game in hop 1, the next query should name that team \
explicitly), set season/game_type/week only when unambiguous, and put team names and \
descriptive detail in semantic_query. `season` is the year the season started; a season's \
playoffs are played in Jan/Feb of the following calendar year. Give a short reason either way."""


class FirstHopQuery(BaseModel):
    season: Optional[int] = Field(default=None, description="e.g. 2023; null if not explicit")
    game_type: Optional[Literal["REG", "POST"]] = Field(default=None)
    week: Optional[int] = Field(default=None)
    semantic_query: str = Field(description="Team names and other disambiguating context")


class SufficiencyJudgment(BaseModel):
    sufficient: bool
    reason: str = Field(description="One or two sentences explaining the judgment")
    season: Optional[int] = Field(default=None, description="Next hop's filter; only when not sufficient")
    game_type: Optional[Literal["REG", "POST"]] = Field(default=None)
    week: Optional[int] = Field(default=None)
    semantic_query: Optional[str] = Field(default=None, description="Next hop's semantic query; required when not sufficient")


@traced_node("agentic_retrieval_node")
def agentic_retrieval_node(state: GraphState) -> dict:
    retry_hint = ""
    if state.get("last_failure") == "coverage":
        retry_hint = COVERAGE_RETRY_HINT.format(reason=state.get("failure_reason", ""))

    model = get_chat_model()
    first = model.with_structured_output(FirstHopQuery).invoke(
        FIRST_HOP_PROMPT.format(retry_hint=retry_hint, question=state["question"])
    )

    query = {"season": first.season, "game_type": first.game_type,
             "week": first.week, "semantic_query": first.semantic_query}
    seen: set[str] = set()
    matches: list[dict] = []
    judgment = None
    attempts = 0

    for hop in range(1 + MAX_SUFFICIENCY_RETRIES):
        where = build_where(query["season"], query["game_type"], query["week"])
        attempts += 1
        for match in query_games(query["semantic_query"], where, n_results=N_RESULTS_PER_HOP):
            if match["text"] not in seen:
                seen.add(match["text"])
                matches.append(match)

        judgment = model.with_structured_output(SufficiencyJudgment).invoke(
            ASSESS_PROMPT.format(question=state["question"], context=format_context(matches))
        )
        # Loop exhausted or satisfied: proceed to generation either way —
        # reflection_node's coverage check is the backstop, not a second cap
        # here (ARCHITECTURE.md §Failure modes).
        if judgment.sufficient or hop == MAX_SUFFICIENCY_RETRIES or not judgment.semantic_query:
            break
        query = {"season": judgment.season, "game_type": judgment.game_type,
                 "week": judgment.week, "semantic_query": judgment.semantic_query}

    return {
        "season": query["season"],
        "game_type": query["game_type"],
        "week": query["week"],
        "semantic_query": query["semantic_query"],
        "context": format_context(matches),
        "sufficiency_attempts": attempts,
        "sufficiency_reason": judgment.reason,
    }
