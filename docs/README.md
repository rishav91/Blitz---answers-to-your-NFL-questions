# NFL Stats Agent — Design Docs

## What this is

A single LangGraph agent over public NFL data (`nfl_data_py`) built to **deeply
learn five AI patterns** — RAG, Agentic RAG, Tool/Function calling, Planning +
Reflection, and Human-in-the-Loop — by wiring all five into one coherent graph
rather than five disconnected demos, with a Streamlit chat UI.
This is a learning exercise, not a product: there are no external users, no
deployment target, and no roadmap except an optional write-up.

The most important scope decision: **breadth across five patterns, depth on
each, in a domain where every answer is checkable against public box scores.**
Anything that doesn't serve that — production hardening, comprehensive stats
coverage, a fourth retrieval framework — is explicitly out (see
[PRD §Goals & Non-Goals](PRD.md#goals--non-goals)).

## Governing principle: Pattern-Boundary Integrity

> Every implementation decision must preserve a genuine, observable seam
> between the five patterns. If a shortcut would let one pattern's mechanism
> stand in for another's — or let two nodes collapse into one — reject it,
> even if it would produce a "good enough" answer. The seam *is* the thing
> being learned.

This isn't an invented abstraction — every contested decision in
[ADRs.md](ADRs.md) ultimately resolves the same way: a shortcut that would
let one pattern's mechanism substitute for another's is rejected, even when
it's simpler. See [ADR-001](ADRs.md#adr-001) (cycles must stay explicit),
[ADR-004](ADRs.md#adr-004) (two loops must stay separate), and
[ADR-005](ADRs.md#adr-005) (no bespoke comparison tool) for three
independent applications of the same rule. Every `should we build/merge/skip
X?` question in this suite is answered by this principle first.

| In scope | Excluded |
|---|---|
| RAG, Agentic RAG, Tool calling, Reflection, HITL over `games`/`pbp`; Streamlit UI; single local session | Auth/multi-tenancy, deployment, horizontal scale, comprehensive stats coverage, any rejected framework below |

## Locked stack / key constraints

| Layer | Choice | Notes |
|---|---|---|
| Orchestration | LangGraph | Cycles (reflection loop, agentic retrieval loop), `interrupt()` for HITL |
| LLM access | LangChain `init_chat_model`, provider/model set by config, not hardcoded | No hard coupling to any single LLM or vendor — see [ADR-006](ADRs.md#adr-006). Default model: Claude Sonnet 4.6 (`claude-sonnet-4-6`), swappable to any LangChain-supported tool-calling model |
| Embeddings | OpenAI `text-embedding-3-small` | Pinned, not abstracted — separate concern from generation-model lock-in, see [ADR-006](ADRs.md#adr-006) |
| Vector store | ChromaDB, `PersistentClient` | Local, in-process, no Docker/auth; metadata filters power hybrid retrieval |
| Data loading | `nfl_data_py` | `games` (schedules) → embedded corpus; `pbp` (play-by-play) → in-memory DataFrame for tools only, never embedded |
| State persistence | LangGraph `MemorySaver` | In-process; required for `interrupt()`/resume |
| UI | Streamlit | Imports the compiled graph directly — no backend, no HTTP checkpoint serialization |

**Rejected, with reasons:** see [ADRs](ADRs.md) — `pdfplumber` (no PDFs),
LlamaIndex (abstracts away the retrieval decisions this project exists to
practice), AutoGen/CrewAI (wrong abstraction level — single-agent patterns,
not multi-agent), LangChain LCEL alone (no cycles), FastAPI (Streamlit
imports the compiled graph in-process), OpenRouter (considered for
provider-abstraction, native LangChain abstraction chosen instead).

## Document map

| Doc | Purpose |
|---|---|
| [README.md](README.md) | This file — orientation, spine, reading order |
| [PRD.md](PRD.md) | Why this exists, goals/non-goals, personas, use cases, success criteria, risks |
| [ARCHITECTURE.md](ARCHITECTURE.md) | The graph, its components, key flows, failure modes |
| [ADRs.md](ADRs.md) | The contested decisions: alternatives considered, why they lost |
| [AI-ARCHITECTURE.md](AI-ARCHITECTURE.md) | Where each LLM call earns its place, the deterministic/LLM split, safety posture |
| [REQUIREMENTS.md](REQUIREMENTS.md) | FR-x.y / NFR-x.y, mapped to the Test Queries table, P0 summary |
| [ROADMAP.md](ROADMAP.md) | The phased build plan, with sequencing rationale |
| [PRODUCTION.md](PRODUCTION.md) | A forward-looking, additive extension beyond this suite's learning-exercise scope: how to actually deploy this to AWS for real users (Bedrock, EKS, Aurora/pgvector, Cognito), with a phased rollout plan and a 1k-DAU/5k-questions-day vs. 100k-questions-day scale delta. Doesn't change the graph itself — see its own scope note |

## Reading order

1. This file
2. [PRD.md](PRD.md) — the why
3. [ARCHITECTURE.md](ARCHITECTURE.md) — the how
4. [ADRs.md](ADRs.md) — why not the alternatives
5. [AI-ARCHITECTURE.md](AI-ARCHITECTURE.md) — the AI-specific cross-cutting concerns
6. [REQUIREMENTS.md](REQUIREMENTS.md) — the testable contract
7. [ROADMAP.md](ROADMAP.md) — the build sequence
8. [PRODUCTION.md](PRODUCTION.md) — optional: deploying this for real users, if/when that's the goal

## Conventions

- `FR-x.y` functional requirements, `NFR-x.y` non-functional — both in
  [REQUIREMENTS.md](REQUIREMENTS.md), grouped by pattern (`FR-1.x` = RAG,
  `FR-2.x` = Agentic RAG, `FR-3.x` = Tool calling, `FR-4.x` = Reflection,
  `FR-5.x` = HITL, `FR-0.x` = routing).
- `ADR-00N` — decisions, in [ADRs.md](ADRs.md). Stable once assigned.
- Assumptions (places the brief didn't pin a value) are marked inline as
  *Assumption:* and listed in [REQUIREMENTS.md §Open Assumptions](REQUIREMENTS.md#open-assumptions)
  — treat them as debts to retire during the build, not settled facts.
