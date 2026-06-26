"""Streamlit chat UI — imports the compiled graph in-process, no backend (ADR-002).

Step-by-step `st.status` visibility and UI polish are a Phase 3 deliverable
(ROADMAP.md); this is the minimal wiring proving the no-backend +
checkpointer setup works at all.
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from dotenv import load_dotenv

from graph.build import graph

load_dotenv()

st.set_page_config(page_title="NFL Stats Agent", page_icon="🏈")
st.title("🏈 NFL Stats Agent")
st.caption("Factual lookups over 2021-2023 NFL schedules (Phase 1: RAG + reflection only).")

if "history" not in st.session_state:
    st.session_state.history = []

for role, content in st.session_state.history:
    with st.chat_message(role):
        st.markdown(content)

question = st.chat_input("Ask about an NFL game, 2021-2023...")
if question:
    st.session_state.history.append(("user", question))
    with st.chat_message("user"):
        st.markdown(question)

    # Each question is its own thread — Phase 1 doesn't carry context across
    # turns; thread_id only starts mattering for interrupt()/resume in Phase 3.
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            result = graph.invoke({"question": question}, config)
        answer = result.get("final_answer") or "Something went wrong — no answer produced."
        st.markdown(answer)
    st.session_state.history.append(("assistant", answer))
