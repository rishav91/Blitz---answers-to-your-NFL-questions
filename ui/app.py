"""Streamlit chat UI — imports the compiled graph in-process, no backend (ADR-002).

Step-by-step `st.status` visibility and UI polish are a Phase 3 deliverable
(ROADMAP.md); this is the minimal wiring proving the no-backend +
checkpointer setup works at all.
"""

import json
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from graph.build import graph
from graph.observability import get_tracer, setup_observability

setup_observability()

GRAFANA_URL = "http://localhost:3001"  # observability/docker-compose.yml maps Grafana to host 3001


def grafana_trace_link(trace_id: str) -> str:
    """Deep link into Grafana Explore (schemaVersion=1 'panes' format, Grafana
    10+) pre-loaded with a TraceQL lookup for this trace ID against Tempo."""
    panes = {
        "blitz": {
            "datasource": "tempo",
            "queries": [
                {"refId": "A", "datasource": {"type": "tempo", "uid": "tempo"}, "queryType": "traceql", "query": trace_id}
            ],
            "range": {"from": "now-6h", "to": "now"},
        }
    }
    return f"{GRAFANA_URL}/explore?schemaVersion=1&panes={quote(json.dumps(panes))}&orgId=1"


def render_reasoning_trail(result: dict, trace_id: str) -> None:
    with st.expander("🔍 Reasoning trail"):
        st.markdown(f"- **Intent:** {result.get('intent', '—')}")
        st.markdown(
            f"- **Filters used:** season={result.get('season')}, "
            f"game_type={result.get('game_type')}, week={result.get('week')}"
        )
        if result.get("semantic_query"):
            st.markdown(f"- **Semantic query:** {result['semantic_query']}")
        st.markdown(f"- **Reflection retries:** {result.get('retry_count', 0)}")
        if result.get("failure_reason"):
            st.markdown(f"- **Last flagged issue:** {result['failure_reason']}")
        if trace_id:
            st.markdown(f"- [Open this trace in Grafana]({grafana_trace_link(trace_id)})")


st.set_page_config(page_title="NFL Stats Agent", page_icon="🏈")
st.title("🏈 NFL Stats Agent")
st.caption("Factual lookups over 2021-2023 NFL schedules (Phase 1: RAG + reflection only).")

if "history" not in st.session_state:
    st.session_state.history = []

for entry in st.session_state.history:
    with st.chat_message(entry["role"]):
        st.markdown(entry["content"])
        if entry.get("result") is not None:
            render_reasoning_trail(entry["result"], entry.get("trace_id"))

question = st.chat_input("Ask about an NFL game, 2021-2023...")
if question:
    st.session_state.history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # Each question is its own thread — Phase 1 doesn't carry context across
    # turns; thread_id only starts mattering for interrupt()/resume in Phase 3.
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            tracer = get_tracer()
            with tracer.start_as_current_span(
                "chat_request",
                attributes={"question": question, "thread_id": config["configurable"]["thread_id"]},
            ) as root_span:
                result = graph.invoke({"question": question}, config)
                trace_id = format(root_span.get_span_context().trace_id, "032x")
        answer = result.get("final_answer") or "Something went wrong — no answer produced."
        st.markdown(answer)
        render_reasoning_trail(result, trace_id)
    st.session_state.history.append(
        {"role": "assistant", "content": answer, "result": result, "trace_id": trace_id}
    )
