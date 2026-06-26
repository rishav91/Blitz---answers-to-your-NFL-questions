"""response_node — terminal formatting/return to UI."""

from graph.state import GraphState


def response_node(state: GraphState) -> dict:
    return {"final_answer": state.get("final_answer", "")}
