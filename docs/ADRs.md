# ADRs — NFL Stats Agent

Each ADR: context → decision → alternatives → consequences. Reference by ID
(e.g. "enforces `ADR-006`"). IDs are stable once assigned — append, don't
renumber.

## Index

| ID | Title | Status |
|---|---|---|
| [ADR-001](#adr-001) | LangGraph for orchestration | Accepted |
| [ADR-002](#adr-002) | No backend — Streamlit imports the compiled graph in-process | Accepted |
| [ADR-003](#adr-003) | Hybrid retrieval: metadata filter + semantic query split | Accepted |
| [ADR-004](#adr-004) | Agentic RAG's sufficiency loop and Reflection's retry loop stay separate | Accepted |
| [ADR-005](#adr-005) | No `compare_teams` tool — the model calls `calculate_team_stats` twice | Accepted |
| [ADR-006](#adr-006) | No LLM hard-coupling — `init_chat_model` over a vendor SDK or a proxy | Accepted |
| [ADR-007](#adr-007) | `games` → RAG, `pbp` → tools, `pbp` never embedded | Accepted |

---

<a id="adr-001"></a>
## ADR-001 — LangGraph for orchestration

**Context:** The graph needs cycles (the reflection retry loop, the agentic
retrieval loop) and a way to pause mid-execution and resume later
(`interrupt()` for HITL). It also needs explicit, inspectable conditional
edges so the five patterns stay visibly distinct rather than disappearing
into prompt-engineering.

**Decision:** Use LangGraph as the orchestration layer.

**Alternatives:**
- *LangChain LCEL alone* — composable, but its chain primitive is a DAG; it
  has no native cycle support, so the reflection and agentic-retrieval loops
  would need to be hand-rolled outside the framework, defeating the point of
  using a framework for them.
- *AutoGen / CrewAI* — built for multi-agent collaboration. This project is
  explicitly single-agent with multiple internal patterns, not multiple
  cooperating agents; adopting a multi-agent framework would be the wrong
  abstraction level and would obscure the single-agent patterns being learned.

**Consequences:**
- `+` Native cycles and conditional edges make the loops in `ARCHITECTURE.md`
  explicit, observable nodes instead of hidden control flow.
- `+` `interrupt()` + a checkpointer (`MemorySaver`) gives HITL pause/resume
  without hand-rolling state serialization.
- `−` Another framework's abstraction to learn, on top of the five patterns
  themselves — accepted because the cycles are unavoidable regardless of
  framework, and LangGraph's are explicit rather than implicit.

---

<a id="adr-002"></a>
## ADR-002 — No backend — Streamlit imports the compiled graph in-process

**Context:** `interrupt()`/resume requires checkpointer state to survive
between the pause and the user's confirmation. A typical web-app split (UI
↔ HTTP API ↔ orchestration) would mean serializing that checkpoint state
across requests.

**Decision:** Streamlit imports the compiled LangGraph object directly and
calls `.stream()` in-process; no FastAPI/Uvicorn layer.

**Alternatives:**
- *FastAPI backend + Streamlit (or any) frontend* — the conventional split.
  Rejected because it requires serializing LangGraph checkpoints and
  resuming `interrupt()` across HTTP requests, a hard problem with no
  relationship to any of the five patterns being practiced.

**Consequences:**
- `+` `MemorySaver` (in-process) is sufficient — no Redis/Postgres-backed
  checkpointer needed to survive a request boundary that doesn't exist.
- `+` Removes an entire layer (API contracts, serialization, deployment) that
  this project has no pedagogical reason to build.
- `−` Doesn't generalize to a real multi-user product — this is explicitly
  accepted; see [PRD.md §Excluded](PRD.md#goals--non-goals). A future
  productionization would need to revisit this and solve the serialization
  problem this ADR sidesteps.

---

<a id="adr-003"></a>
## ADR-003 — Hybrid retrieval: metadata filter + semantic query split

**Context:** Embedding similarity is unreliable for discrete coordinates —
"Week 10" vs. "Week 9" can be closer in embedding space than two genuinely
different games. But collapsing every field into the filter (season, week,
*and* teams) would let the filter alone resolve to one row, leaving the
embedding search with nothing real to do — which defeats the purpose of
exercising retrieval quality at all (the governing principle, applied).

**Decision:** Push only genuinely discrete, unambiguous fields into the
Chroma `where` filter — `season`, `game_type`, and `week` when the question
states it outright. Leave team names and any game the question doesn't fully
pin down to the semantic query.

**Alternatives:**
- *Pure vector search* — rejected; exact-match fields like week/season are
  the wrong job for embedding similarity and would cause spurious wrong-game
  retrievals.
- *Filter on every extractable field* — rejected; if the filter alone
  narrows to one row, the semantic query is decorative, and the project loses
  its only real test of retrieval quality (see [UC-5](PRD.md#core-use-cases),
  the deliberately-ambiguous Chiefs/Eagles query).

**Consequences:**
- `+` Exact fields get exact treatment; ambiguous language gets semantic
  treatment — each tool used where it's actually good.
- `+` Produces a genuine retrieval-quality test case (UC-5) instead of an
  always-correct-by-construction lookup.
- `−` Requires a small parser step to decide what's "genuinely unambiguous"
  before querying — one more piece of logic than a single naive query call.

---

<a id="adr-004"></a>
## ADR-004 — Agentic RAG's sufficiency loop and Reflection's retry loop stay separate

**Context:** Both `assess_sufficiency` (inside `agentic_retrieval_node`) and
`reflection_node`'s retry routing can route back to a retrieval step, which
makes them look like the same mechanism. They ask different questions at
different times: sufficiency runs *before* generation ("do I have enough
context to even try"), reflection runs *after* ("did the answer I generated
actually hold up").

**Decision:** Keep them as two distinct nodes/loops, never merged into one
generic "retry retrieval until good" loop.

**Alternatives:**
- *One merged retry loop* — simpler graph, fewer nodes. Rejected: per the
  governing principle, this is the easiest way to lose Agentic RAG as a
  distinct, observable pattern — it would stop being "multi-hop retrieval
  where hop 2 depends on hop 1's result" and become indistinguishable from
  reflection's coverage-failure retry.

**Consequences:**
- `+` Each loop stays legible as its own pattern when reading the graph.
- `+` The two loops can have independent stopping conditions (`NFR-2` for
  sufficiency, `NFR-1` for reflection) tuned for what they're actually
  checking.
- `−` More nodes and more conditional edges than a single generic retry
  mechanism would need — accepted because the point of the project is to
  observe these as separate things.

---

<a id="adr-005"></a>
## ADR-005 — No `compare_teams` tool — the model calls `calculate_team_stats` twice

**Context:** A question like "compare the Chiefs' and Eagles' turnover
differential" could be served by a single bespoke `compare_teams(team_a,
team_b, metric)` tool, or by letting the model call the existing
single-team tool twice and synthesize the result itself.

**Decision:** No `compare_teams` tool. `generation_node` calls
`calculate_team_stats` once per team and combines the results in its answer.

**Alternatives:**
- *Add a `compare_teams` tool* — would work, and arguably produces a
  marginally cleaner single tool call. Rejected because it hides the more
  interesting behavior this project exists to exercise: the model *deciding*
  to invoke a tool more than once in a turn, rather than every multi-entity
  question getting its own pre-built wrapper.

**Consequences:**
- `+` A more honest demonstration of tool-calling — the model's own planning
  decides how many calls are needed, not a function signature that already
  encodes "exactly two teams."
- `+` One fewer tool to maintain; `calculate_team_stats` already generalizes
  to N teams if a future question asked for three-way comparison.
- `−` Slightly more synthesis burden on `generation_node`'s prompt (it must
  combine two tool results coherently) than a tool that returns an
  already-combined comparison would require.

---

<a id="adr-006"></a>
## ADR-006 — No LLM hard-coupling — `init_chat_model` over a vendor SDK or a proxy

**Context:** The original stack design locked the LLM directly to "Claude
API `claude-sonnet-4-6`", reasoning from Claude's native tool-use format.
That direct coupling was reconsidered: the project should not be hard-wired
to one model or vendor family, so that the underlying model can be swapped
without touching node code.

**Decision:** Access the generation model through LangChain's
`init_chat_model`, with the provider and model name set by config/env var,
not imported as a hardcoded vendor SDK call in node code. Claude Sonnet 4.6
remains the *default* configured model; embeddings stay pinned to OpenAI
`text-embedding-3-small` (a separate, narrower concern — see [PRD.md
§Excluded](PRD.md#goals--non-goals)).

**Alternatives:**
- *Call the `anthropic` SDK directly* (the original plan) —
  simplest, and `claude-sonnet-4-6`'s tool-use format is reliable. Rejected
  because it hard-codes a single vendor into every node that calls the model,
  which is exactly the coupling this decision exists to avoid.
- *Route through OpenRouter* (single API key, OpenAI-compatible endpoint,
  swap models via a model-string config change) — genuinely viable, and was
  the first option raised. Rejected in favor of LangChain's native
  abstraction to avoid adding a third-party proxy (and its per-token markup
  and uptime dependency) on top of the model provider itself; LangChain's
  `init_chat_model` gets the same "swap via config" outcome by holding
  per-provider keys directly instead of one proxy key.

**Consequences:**
- `+` Swapping the underlying model (e.g. to GPT-4.x) is a config change, not
  a code change, across every node that calls the model.
- `+` No dependency on a third-party proxy's uptime, pricing, or model
  catalog — only on the providers actually configured.
- `−` Requires holding a separate API key per provider you want to support,
  instead of OpenRouter's single key — more secrets to manage if more than
  one provider is ever actually configured.
- `−` Tool-calling reliability and reflection-scoring quality are not
  guaranteed identical across providers/models — swapping away from the
  default should be re-verified against the Test Queries (see
  [ARCHITECTURE.md §Failure modes](ARCHITECTURE.md#failure-modes--degradation)).

---

<a id="adr-007"></a>
## ADR-007 — `games` → RAG, `pbp` → tools, `pbp` never embedded

**Context:** The dataset has two slices with very different shapes: `games`
(~800 rows, one per game — facts) and `pbp` (~50k rows, one per play —
requires aggregation to mean anything). It would be possible to embed
chunked play-by-play summaries too, or to let a framework like LlamaIndex
auto-route between "ask the documents" and "ask the data."

**Decision:** `games` is chunked and embedded into ChromaDB; `pbp` is loaded
as an in-memory DataFrame and is *only* ever touched by tools, never embedded.

**Alternatives:**
- *Also embed `pbp` summaries* — rejected; anything requiring arithmetic
  over individual plays (yards, turnovers, third-down %) is a tool's job by
  definition (the tool-vs-retrieval boundary in [PRD.md §Scope, applied](PRD.md#scope--governing-rule-applied));
  embedding pre-aggregated play summaries would quietly let retrieval
  shortcut a computation that's supposed to exercise tool calling.
- *LlamaIndex-style automatic retrieval/tool routing* — rejected per the
  project's stack decision: it abstracts away exactly the retrieval-vs-tool
  boundary decision this project exists to practice making explicitly.

**Consequences:**
- `+` The tool-vs-retrieval boundary is structural (which store the data
  lives in), not just a prompting convention that could erode over time.
- `+` `pbp` never needs re-embedding if its schema or season coverage
  changes — it's read fresh into memory per process start.
- `−` Any future question needing both a specific game's narrative *and* an
  aggregate stat must be served by two separate calls (one retrieval, one
  tool) rather than one unified lookup — accepted, since that two-call shape
  is itself the thing being demonstrated.
