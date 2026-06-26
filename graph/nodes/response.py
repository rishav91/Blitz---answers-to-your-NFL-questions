"""response_node — terminal formatting/return to UI."""

from graph.observability import traced_node
from graph.state import GraphState


@traced_node("response_node")
def response_node(state: GraphState) -> dict:
    return {"final_answer": state.get("final_answer", "")}
