# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single LangGraph agent over public NFL data (`nfl_data_py`), built as a learning exercise to wire five AI patterns — RAG, Agentic RAG, Tool calling, Planning + Reflection, Human-in-the-Loop — into one coherent graph with a Streamlit chat UI. Not a product: no deployment, no external users.

**Governing principle — Pattern-Boundary Integrity** (docs/README.md): every decision must preserve a genuine, observable seam between the five patterns. Reject any shortcut that lets one pattern's mechanism stand in for another's, or lets two nodes collapse into one, even if it would produce a good-enough answer. Every contested design question is answered by this principle first.

## Commands

```bash
pip install -r requirements.txt      # nfl_data_py is pinned alongside its real runtime deps
                                     # (appdirs, fastparquet) and installed with --no-deps if its
                                     # stale pandas<2.0/numpy<2.0 pins conflict — see requirements.txt
cp .env.example .env                 # OPENAI_API_KEY always required (embeddings); plus the key
                                     # for whichever chat provider CHAT_MODEL_PROVIDER selects

python data/ingest.py                # one-time: embeds 2021-2023 schedules into ./chroma_db
                                     # (upserts by game_id, safe to re-run)

streamlit run ui/app.py              # the app — imports the compiled graph in-process, no backend

docker compose -f observability/docker-compose.yml up -d   # optional dev-time observability
                                                           # (Grafana on host port 3001)
```

Run everything from the repo root — `CHROMA_PATH = "./chroma_db"` is relative. There is no test suite, linter config, or build step yet.

## Architecture

The docs in `docs/` are the project's spine and are actively maintained — read `docs/README.md` first for orientation, `docs/ARCHITECTURE.md` for the graph, `docs/ADRs.md` before revisiting any settled decision (ADR-001…008), `docs/ROADMAP.md` for what's stubbed vs. built. Requirements are referenced in code comments as `FR-x.y`/`NFR-x` and defined in `docs/REQUIREMENTS.md`.

### The graph (graph/build.py)

```
START → router_node → (by intent)
  factual    → retrieval_node → generation_node → reflection_node → response_node → END
  analytical → agentic_retrieval_node → generation_node (tools bound) → reflection_node → response_node
  predictive → predictive_stub_node → response_node        (real path lands in Phase 3)
```

`reflection_node` judges the draft on two axes and routes retries differently (`route_from_reflection`):
- **grounding failure** (answer states facts not in the context) → back to `generation_node` with a fixed correction template
- **coverage failure** (context lacks/doesn't pin down the right game) → back to the path's retrieval step, which **drops the metadata filter entirely** and broadens to `n_results=5` — `retrieval_node` on the factual path, `agentic_retrieval_node` on the analytical path (intent-aware retargeting, `ADR-004`)

Both edges share one retry budget: `MAX_REFLECTION_RETRIES = 2` total (NFR-1, in `graph/state.py`). On exhaustion the draft ships with a caution caveat rather than failing.

All nodes read/write the `GraphState` TypedDict (`graph/state.py`); each field is owned by one node — keep that mapping intact when adding fields.

### Key implementation decisions (with the ADR that locks them)

- **LLM access** (`graph/llm.py`, ADR-006): always `init_chat_model` with provider/model from env (`CHAT_MODEL`, `CHAT_MODEL_PROVIDER`). Never import a vendor SDK directly for chat. Embeddings are the deliberate exception — pinned to OpenAI `text-embedding-3-small`, not abstracted.
- **Hybrid retrieval** (ADR-003): an LLM extracts structured filters (season/game_type/week) plus a semantic query; `graph/store.py` combines a Chroma metadata `where` filter with nearest-neighbor search. Note: this chromadb version rejects implicit-AND multi-key filters — multi-clause filters must be wrapped in `{"$and": [...]}` (`build_where` handles this).
- **Data split** (ADR-007): `games` schedules → embedded RAG corpus; `pbp` play-by-play → in-memory DataFrame for tools only, never embedded.
- **Tool calling** (`graph/tools.py`, ADR-005): `calculate_team_stats`/`get_standings` are bound in `generation_node` only when `intent=="analytical"`; no `compare_teams` tool — the model decides how many `calculate_team_stats` calls a comparison needs (one per team). Tool results get appended into `context` so `reflection_node`'s grounding/coverage check applies uniformly across retrieved chunks and tool output.
- **No backend** (ADR-002): Streamlit imports the compiled graph directly. The `MemorySaver` checkpointer is in-process and required for the Phase 3 `interrupt()`/resume HITL flow — don't serialize state over HTTP.
- **Season semantics**: `season` metadata is the year the season *started*; playoffs/Super Bowl are played in Jan/Feb of the following calendar year. Both the generation and reflection prompts explicitly handle this conversion — preserve it when editing prompts.
- **Structured LLM output**: every classifying/extracting/judging LLM call uses `with_structured_output` on a Pydantic model (see router, retrieval, reflection). Free-form model text feeding back into prompts is restricted to the reflection *reason* slotted into fixed templates (instruction/data separation, docs/AI-ARCHITECTURE.md).

### Observability (graph/observability.py, ADR-008)

Every graph node is wrapped with `@traced_node("name")` — it opens a `node.<name>` span, records `GraphState` fields as `blitz.state.*` attributes, times the call into a histogram, and logs entry/exit. **New nodes must use this decorator.** LLM calls are auto-traced via `LangChainInstrumentor` — no per-call instrumentation needed. `setup_observability()` is idempotent (Streamlit re-runs modules); counters/meters use lazy singleton getters. The UI deep-links each answer's trace into Grafana (hardcoded to host port 3001).

## Current state (per docs/ROADMAP.md)

Phases 0–2 are built: ingestion, the factual RAG + reflection path, the analytical agentic-RAG + tool-calling path, Streamlit UI, observability stack. Phase 3 (HITL `interrupt()` for the predictive branch, UI polish) is still stubbed in `graph/nodes/stubs.py`. `router_node` already classifies all three intents.

**Workflow:** docs/ROADMAP.md tracks progress with checkboxes — a "Status at a glance" list plus per-phase **Sub-phases** broken into commit-sized units. Work one sub-phase at a time; each completed sub-phase ends in its own git commit, and its checkbox gets ticked with the commit hash appended (`— <hash>`, matching the existing style). Keep the roadmap current as sub-phases land.
