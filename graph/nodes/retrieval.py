"""retrieval_node — hybrid metadata filter + semantic query, single hop (FR-1.1)."""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from graph.llm import get_chat_model
from graph.observability import traced_node
from graph.state import GraphState
from graph.store import build_where, query_games

EXTRACTION_PROMPT = """Extract structured filter fields from this NFL question, and put \
everything else (team names, anything identifying the specific game) into semantic_query.

Only set season/game_type/week if the question states them explicitly and \
unambiguously — leave a field unset rather than guess. `season` is the year the \
NFL season started (the Super Bowl played in February 2023 belongs to the 2022 \
season, not 2023). `game_type` is "REG" for regular season or "POST" for \
playoffs/Super Bowl — only set it if the question is unambiguous about regular \
season vs. playoffs.
{broaden_hint}
Question: {question}"""

BROADEN_HINT = (
    "\nA previous attempt didn't clearly cover the question: \"{reason}\" This retry "
    "drops the metadata filter and searches more broadly — make semantic_query describe "
    "the game as fully as possible (teams, any timeframe mentioned) so semantic search "
    "can surface the right candidate among more results.\n"
)


class ExtractedQuery(BaseModel):
    season: Optional[int] = Field(default=None, description="e.g. 2023; null if not explicit")
    game_type: Optional[Literal["REG", "POST"]] = Field(default=None)
    week: Optional[int] = Field(default=None)
    semantic_query: str = Field(description="Team names and other disambiguating context")


def format_context(matches: list[dict]) -> str:
    if not matches:
        return "No matching game found in the corpus."
    blocks = []
    for match in matches:
        meta = match["metadata"]
        tag = f"[season={meta['season']} game_type={meta['game_type']} week={meta['week']}]"
        blocks.append(f"{tag}\n{match['text']}")
    return "\n\n".join(blocks)


@traced_node("retrieval_node")
def retrieval_node(state: GraphState) -> dict:
    is_retry = state.get("last_failure") == "coverage"
    broaden_hint = BROADEN_HINT.format(reason=state.get("failure_reason", "")) if is_retry else ""

    model = get_chat_model().with_structured_output(ExtractedQuery)
    extracted = model.invoke(
        EXTRACTION_PROMPT.format(broaden_hint=broaden_hint, question=state["question"])
    )

    # A coverage failure means the previously assumed filter excluded the right game —
    # drop it entirely rather than trust the model to re-guess a narrower one; semantic
    # search over more candidates does the disambiguation instead.
    if is_retry:
        where, n_results = None, 5
    else:
        where, n_results = build_where(extracted.season, extracted.game_type, extracted.week), 1

    matches = query_games(extracted.semantic_query, where, n_results=n_results)

    return {
        "season": extracted.season,
        "game_type": extracted.game_type,
        "week": extracted.week,
        "semantic_query": extracted.semantic_query,
        "context": format_context(matches),
    }
