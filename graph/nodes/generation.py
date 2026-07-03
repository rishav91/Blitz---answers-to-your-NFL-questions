"""generation_node — answers from the retrieved chunk on the factual path (FR-1.x,
tool-free per PRD.md §Scope); coordinates tool calls on the analytical path (FR-3.x,
ADR-005: no compare_teams — the model decides how many calculate_team_stats calls
a comparison needs)."""

from langchain_core.messages import HumanMessage, ToolMessage

from graph.llm import get_chat_model
from graph.observability import traced_node
from graph.state import GraphState
from graph.tools import calculate_team_stats, get_standings

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

ANALYTICAL_GENERATION_PROMPT = """Answer the question using the context below and/or the \
calculate_team_stats and get_standings tools — call whichever tools you need, in whatever \
order the question requires. For a comparison between teams, call calculate_team_stats once \
per team and combine the results yourself; there is no single tool that compares two teams \
at once. Never state a number that didn't come from the context or a tool result.

Note: the context's/tools' `season` is the year the NFL season started, not the calendar \
year of every game in it — a season's playoffs, including the Super Bowl, are played in \
January/February of the FOLLOWING calendar year.

Context:
{context}

Question: {question}"""

# Fixed correction template (AI-ARCHITECTURE.md §Instruction/data separation) — the only
# free-form text fed back in is the reflection reason, never an instruction itself.
CORRECTION_TEMPLATE = (
    "You cited a number not present in the source — rewrite using only the text below. "
    "(Reason flagged: {reason})"
)

TOOLS = [calculate_team_stats, get_standings]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

# Safety bound on the tool-calling loop, not a designed NFR — generous enough for a
# multi-team comparison (ADR-005: one call per team) without letting a stuck model
# loop indefinitely.
MAX_TOOL_HOPS = 6


@traced_node("generation_node")
def generation_node(state: GraphState) -> dict:
    model = get_chat_model()
    if state.get("intent") == "analytical":
        return _generate_with_tools(state, model)

    prompt = GENERATION_PROMPT.format(context=state["context"], question=state["question"])
    if state.get("last_failure") == "grounding":
        correction = CORRECTION_TEMPLATE.format(reason=state.get("failure_reason", ""))
        prompt = f"{correction}\n\n{prompt}"

    response = model.invoke(prompt)
    return {"draft_answer": response.content}


def _generate_with_tools(state: GraphState, model) -> dict:
    prompt = ANALYTICAL_GENERATION_PROMPT.format(context=state["context"], question=state["question"])
    if state.get("last_failure") == "grounding":
        correction = CORRECTION_TEMPLATE.format(reason=state.get("failure_reason", ""))
        prompt = f"{correction}\n\n{prompt}"

    model_with_tools = model.bind_tools(TOOLS)
    messages = [HumanMessage(prompt)]
    tool_results = []

    for _ in range(MAX_TOOL_HOPS):
        response = model_with_tools.invoke(messages)
        messages.append(response)
        if not response.tool_calls:
            return {"draft_answer": response.content, "context": _with_tool_results(state["context"], tool_results)}

        for call in response.tool_calls:
            tool = TOOLS_BY_NAME[call["name"]]
            result = tool.invoke(call["args"])
            tool_results.append(f"{call['name']}({call['args']}) -> {result}")
            messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))

    # Hop budget exhausted without a final answer — reflection_node's coverage
    # check is the backstop, same pattern as agentic_retrieval_node's NFR-2 cap.
    return {
        "draft_answer": "I wasn't able to finish the analysis within the tool-call budget.",
        "context": _with_tool_results(state["context"], tool_results),
    }


def _with_tool_results(context: str, tool_results: list[str]) -> str:
    if not tool_results:
        return context
    return f"{context}\n\nTool results:\n" + "\n".join(tool_results)
