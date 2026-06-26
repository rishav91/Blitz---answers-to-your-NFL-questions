# PRD — NFL Stats Agent

See [README.md](README.md) for the governing principle and locked stack;
this doc doesn't re-litigate either.

## Problem

This is a learning project, not a product with an external user base — so
the "problem" is pedagogical, and naming it precisely matters.

**The gap:** reading about RAG, Agentic RAG, tool calling, reflection, and
HITL is cheap; building all five in one coherent system, where the seams
between them are forced to be real, is not. Most tutorials demonstrate one
pattern in isolation, against synthetic or toy data, where there's no way to
tell a fluent wrong answer from a correct one.

**What that costs:** shallow pattern recognition — being able to name "RAG"
but not having debugged *why* a retrieval came back wrong, or *why* a
reflection loop needs a shared retry budget instead of two independent ones.

**How it's solved today:** single-pattern blog posts and framework quickstarts.
Inadequate because (a) they don't force the boundary decisions that only show
up when patterns have to coexist in one graph, and (b) synthetic data makes
hallucination invisible — there's nothing to check the answer against.

**The wedge:** NFL game data is public and verifiable. Every score, week, and
playoff result the agent cites can be checked against a known fact within
seconds — so unlike most LLM demos, hallucination is *immediately* visible,
not just theoretically possible. One domain, five patterns, and a built-in
answer key.

## Goals & non-goals

**Goals**

- Ship a single runnable LangGraph agent exercising all five patterns, with each pattern occupying a distinct, observable node
  or loop (per the governing principle).
- Every cited number traceable to a retrieved chunk or a tool result —
  correctness is checkable against public NFL data, not just "looks right."
- Finish each phase in [ROADMAP.md](ROADMAP.md) with something runnable,
  not a partial graph that only works end-to-end at the very end.

**Excluded (permanently, for this project)**

| Item | Why excluded |
|---|---|
| Auth, multi-tenancy, multi-user concurrency | Single local learner, single Streamlit session — no second user to isolate from |
| Deployment / hosting | Runs locally via `streamlit run`; no target environment was ever in scope |
| Horizontal scale, production observability/monitoring | Dataset is ~800 game rows + ~50k play rows, one session at a time — there's no load to plan for |
| Comprehensive NFL stats coverage | Only the five named metrics in `calculate_team_stats` plus `get_standings`; this is a pattern-exposure project, not a stats product |
| LlamaIndex, AutoGen/CrewAI, LangChain LCEL-only, FastAPI, `pdfplumber`, hard-coded single-vendor LLM SDK calls | See [ADRs.md](ADRs.md) for the full alternatives-and-reasons treatment |

## Personas

There is no multi-user surface; "personas" here are two roles the same
person plays at different times.

| Persona | Scope | Primary need | Success looks like |
|---|---|---|---|
| Builder (primary) | Full graph, all five patterns, tightly scoped | Hands-on exposure to each pattern's real implementation decisions, not just its name | Graph runs end-to-end; each pattern's mechanism is genuinely distinct (governing principle holds); test queries in [REQUIREMENTS.md](REQUIREMENTS.md) pass |
| Future reader of the write-up (secondary) | Read-only, after the fact | See how each pattern's textbook description maps to one concrete, checkable domain decision | The write-up (out of scope for this suite) can point at real boundary decisions instead of hand-waving |

No RBAC/visibility concerns — there is exactly one local user and no shared
data.

## Core use cases

These use cases double as the project's test-query set; full acceptance
criteria live in [REQUIREMENTS.md](REQUIREMENTS.md) under the matching `FR-x.y`.

1. **UC-1 (RAG):** "What was the final score when the 49ers played the
   Cowboys in the 2023 playoffs?" → single-hop factual lookup, metadata
   filter narrows to one game, semantic query is a sanity check. → `FR-1.1`
2. **UC-2 (Agentic RAG):** "The team that beat the Chiefs in Week 13, 2023 —
   how far did they go in the playoffs that year?" → first retrieval's result
   determines the second retrieval's filter. → `FR-2.1`
3. **UC-3 (Tool calling):** "Calculate the Chiefs' average turnover
   differential per game in the 2023 season, and compare it to the Eagles'."
   → two `calculate_team_stats` calls in one turn, synthesized by the model.
   → `FR-3.1`
4. **UC-4 (Reflection, should pass):** "What was the Eagles' total offensive
   yards in their Week 1, 2023 game?" → answer should ground cleanly with no
   retry. → `FR-4.1`
5. **UC-5 (Reflection, coverage failure):** "What was the final score of the
   Chiefs vs. Eagles game in 2023?" → ambiguous (regular season vs. Super
   Bowl) → should trigger a coverage-failure retry back to retrieval. → `FR-4.2`
6. **UC-6 (Reflection, grounding failure):** "What was the Eagles' passer
   rating in their Week 1, 2023 game?" → not in the chunk template → a
   hallucinated number should trigger a grounding-failure retry back to
   generation. → `FR-4.3`
7. **UC-7 (HITL):** "Based on regular season stats, who do you think wins if
   the Chiefs and Eagles played again?" → real stats surfaced and confirmed
   *before* any speculative answer is generated. → `FR-5.1`

## Scope / governing rule, applied

The governing principle ([README.md](README.md)) cashes out as a hard rule
for this graph: **no node may use another pattern's mechanism as a shortcut.**
Concretely:

| Pattern | Owns | Must never leak into |
|---|---|---|
| RAG (`FR-1.x`) | Single-hop retrieval + generation, no tool calls | Tool calling — the factual path stays tool-free so tool calling is a genuinely different mechanism to learn, not a relabeled retrieval call |
| Agentic RAG (`FR-2.x`) | Multi-hop retrieval where hop 2's query depends on hop 1's result | Tool calling — "run the same computation over every team" is an aggregation, i.e. a tool (`get_standings`), not a second retrieval |
| Tool calling (`FR-3.x`) | Arithmetic/aggregation over `pbp`/`games` | A `compare_teams` tool — comparison is the model calling `calculate_team_stats` twice and synthesizing, not a bespoke wrapper |
| Reflection (`FR-4.x`) | Post-generation grounding/coverage check, *after* an answer exists | Agentic RAG's `assess_sufficiency` — that runs *before* generation and asks a different question |
| HITL (`FR-5.x`) | Gating speculative output *before* `generation_node` runs | Any branch where the answer isn't genuinely speculative — don't gate things that don't need gating |

## Success metrics

No product metrics apply (no users to adopt/activate/retain). Success is
measured per roadmap phase and per pattern:

- **Per phase:** each phase in [ROADMAP.md](ROADMAP.md) ends with a runnable
  graph slice — not a partial state that only works once everything else is
  also done.
- **Per pattern:** the corresponding use case (UC-1..UC-7) produces an answer
  that's verifiably correct against public NFL data, or — for UC-5/UC-6 —
  correctly triggers the retry edge it's designed to test.
- **Reflection budget:** the shared retry budget (`NFR-1`, max 2 across both
  edges) is observed to cap retries during UC-5/UC-6 testing rather than
  looping.
- **Coverage:** all 7 use cases pass manual verification before considering
  the build complete.

## Risks

A couple of these are already designed around elsewhere in this suite (e.g.
the shared retry budget exists specifically to bound the reflection-loop
risk below). The rest are flagged here as judgment calls, not settled facts.

| Risk | Mitigation |
|---|---|
| Reflection loop oscillates between grounding/coverage failures without converging | Shared retry budget, max 2 total (`NFR-1`); on exhaustion, return the best available answer with an explicit caveat rather than looping further (*Assumption* — see [REQUIREMENTS.md §Open Assumptions](REQUIREMENTS.md#open-assumptions)) |
| Swapping the underlying LLM (per [ADR-006](ADRs.md#adr-006)) changes tool-calling reliability or reflection-scoring quality, since not all models follow structured tool-call/JSON instructions equally well | Keep Claude Sonnet 4.6 as the default; treat any other model as opt-in and re-run the Test Queries (UC-1..UC-7) before trusting it |
| `nfl_data_py`'s upstream source is unavailable or has changed shape at ingestion time | Ingestion (`data/ingest.py`) is a one-time setup step, not a runtime dependency — failure here blocks that phase of the build, not a live demo; re-run once the source is back |
| Chroma metadata filter and embedded text disagree on field representation (e.g. `week` as int vs. string) | Verification step in `ingest.py` (query with a `where` filter, confirm expected games return) catches this before the chat graph is ever exercised |
| timebox slips because a pattern takes longer than budgeted | [ROADMAP.md](ROADMAP.md) sequences simplest-pattern-pair first specifically so a slip shows up early, with the cheapest patterns already working |
