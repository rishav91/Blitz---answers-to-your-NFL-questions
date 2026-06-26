"""Compiles the Phase 1 graph: router -> retrieval -> generation -> reflection
(with both shared-budget retry edges) -> response. Analytical/predictive
branches are stubbed; see ROADMAP.md Phase 2/3."""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from graph.nodes.generation import generation_node
from graph.nodes.reflection import reflection_node, route_from_reflection
from graph.nodes.response import response_node
from graph.nodes.retrieval import retrieval_node
from graph.nodes.router import route_from_intent, router_node
from graph.nodes.stubs import analytical_stub_node, predictive_stub_node
from graph.state import GraphState


def build_graph():
    builder = StateGraph(GraphState)

    builder.add_node("router_node", router_node)
    builder.add_node("retrieval_node", retrieval_node)
    builder.add_node("generation_node", generation_node)
    builder.add_node("reflection_node", reflection_node)
    builder.add_node("response_node", response_node)
    builder.add_node("analytical_stub_node", analytical_stub_node)
    builder.add_node("predictive_stub_node", predictive_stub_node)

    builder.add_edge(START, "router_node")
    builder.add_conditional_edges(
        "router_node",
        route_from_intent,
        ["retrieval_node", "analytical_stub_node", "predictive_stub_node"],
    )
    builder.add_edge("retrieval_node", "generation_node")
    builder.add_edge("generation_node", "reflection_node")
    builder.add_conditional_edges(
        "reflection_node",
        route_from_reflection,
        ["generation_node", "retrieval_node", "response_node"],
    )
    builder.add_edge("analytical_stub_node", "response_node")
    builder.add_edge("predictive_stub_node", "response_node")
    builder.add_edge("response_node", END)

    return builder.compile(checkpointer=MemorySaver())


graph = build_graph()
