"""Placeholder nodes for the analytical/predictive branches.

The real `agentic_retrieval_node` + tools (Phase 2) and `hitl_node` (Phase 3)
land in later phases per ROADMAP.md; `router_node` already classifies all
three intents correctly, but only the factual path is wired to real work.
"""

from graph.state import GraphState


def analytical_stub_node(state: GraphState) -> dict:
    return {
        "final_answer": (
            "This looks like an analytical question (a multi-hop lookup or a stat "
            "comparison) — that path isn't built yet, it ships in Phase 2."
        )
    }


def predictive_stub_node(state: GraphState) -> dict:
    return {
        "final_answer": (
            "This looks like a predictive question — that path isn't built yet, "
            "it ships in Phase 3 (with a human-in-the-loop confirmation step)."
        )
    }
