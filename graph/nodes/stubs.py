"""Placeholder node for the predictive branch.

The real `hitl_node` (Phase 3) lands in a later phase per ROADMAP.md;
`router_node` already classifies all three intents correctly, but only the
factual and analytical paths are wired to real work.
"""

from graph.observability import traced_node
from graph.state import GraphState


@traced_node("predictive_stub_node")
def predictive_stub_node(state: GraphState) -> dict:
    return {
        "final_answer": (
            "This looks like a predictive question — that path isn't built yet, "
            "it ships in Phase 3 (with a human-in-the-loop confirmation step)."
        )
    }
