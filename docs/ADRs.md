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
| [ADR-008](#adr-008) | OpenTelemetry + Tempo/Prometheus/Loki/Grafana for dev-time observability | Accepted |
| [ADR-009](#adr-009) | Production revisit of ADR-002 — FastAPI backend + Postgres checkpointer | Accepted |
| [ADR-010](#adr-010) | Bedrock for chat only, not embeddings, in production | Accepted |
| [ADR-011](#adr-011) | Aurora PostgreSQL + pgvector over a dedicated vector service, in production | Accepted |
| [ADR-012](#adr-012) | EKS over ECS Fargate for production compute | Accepted |
| [ADR-013](#adr-013) | ALB-native Cognito auth over API Gateway, at Tier 1 | Accepted |
| [ADR-014](#adr-014) | Scheduled batch ingestion over managed/streaming alternatives, in production | Accepted |
| [ADR-015](#adr-015) | Hybrid-keyed semantic response cache (Redis/MemoryDB) in front of generation | Accepted |
| [ADR-016](#adr-016) | Session-scoped `thread_id` + conversational memory in `GraphState` | Accepted |

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

---

<a id="adr-008"></a>
## ADR-008 — OpenTelemetry + Tempo/Prometheus/Loki/Grafana for dev-time observability

**Context:** Beyond a terminal spinner, there was no way to see what
`router_node` classified, what filters `retrieval_node` built, why
`reflection_node` triggered a retry, or how long any of that took. This is
explicitly scoped as dev-time transparency into each node's behavior, not
the production-scale monitoring (alerting, SLOs, multi-tenant cost control)
[PRD.md §Goals & non-goals](PRD.md#goals--non-goals) already excludes — see
that entry's updated wording.

**Decision:** Instrument the graph with OpenTelemetry — one root trace per
`graph.invoke()` call, one child span per node visited (`graph/observability.py`'s
`traced_node` decorator), one grandchild span per LLM call (auto-captured via
`openinference-instrumentation-langchain`, with no changes to `graph/llm.py`).
Traces, metrics, and logs all ship over OTLP to a single collector
(`observability/docker-compose.yml`), which fans out to Tempo, Prometheus,
and Loki, unified in one Grafana UI with trace-to-logs/trace-to-metrics
correlation. `ui/app.py` also surfaces a per-answer "Reasoning trail"
expander (intent, filters used, retry count, last failure) plus a deep link
into the matching Grafana trace.

**Alternatives:**
- *Arize Phoenix* — OSS, `pip install`-only, zero Docker, auto-instruments
  LangChain/LangGraph the same way. The lightest option and the best fit for
  this project's no-backend stance (`ADR-002`), but narrower than a real
  metrics+logs pillar — it's a trace viewer with some derived stats, not a
  Prometheus/Grafana-style dashboard or a Loki-style log store. Rejected
  because the user explicitly wanted the standard, transferable three-pillars
  tooling over the lightest-weight option.
- *Langfuse (self-hosted)* — richer single tool (traces, scoring, cost
  tracking) via a LangChain `CallbackHandler`, but LLM-specific rather than
  the general-purpose observability stack, and still needs ~4 containers
  (Postgres/ClickHouse/Redis/server). Rejected for the same reason as Phoenix.
- *LangSmith* — the framework-native option (env-var only setup), but a
  proprietary SaaS product, not open source. Rejected outright per the
  project's OSS preference.

**Consequences:**
- `+` Each user question is one fully correlated trace — node spans, nested
  LLM-call spans (prompt/completion/tokens), structured logs, and counters
  all clickable from one Grafana trace view.
- `+` Standard, vendor-neutral tooling (OpenTelemetry + Grafana stack) rather
  than an LLM-specific observability product — more transferable outside
  this project.
- `−` ~5 Docker containers (collector, Tempo, Prometheus, Loki, Grafana)
  running locally — meaningfully more infrastructure than `ADR-002`'s
  original "no backend" framing. Accepted because this is observability
  tooling sitting beside the app for the same single local user, not a
  reintroduction of the HTTP-API-backend problem `ADR-002` actually avoided
  (no app code talks HTTP to it except one-way OTLP export; nothing about
  request/response serialization or `interrupt()`/resume crosses a network
  boundary).
- `−` Token-usage/cost is visible per-trace (OpenInference span attributes)
  but not duplicated as its own Prometheus series in this pass — a fast
  follow if a "tokens over time" dashboard panel is wanted later.
- `−` Span nesting here assumes Phase 1's graph executes sequentially in one
  thread; if Phase 2/3 introduce concurrent tool calls or parallel branches,
  OTel context propagation across threads needs rechecking then.

---

The ADRs below (`ADR-009` onward) belong to [PRODUCTION.md](PRODUCTION.md) —
production deployment decisions, additive to the learning-exercise scope
these first eight ADRs describe. See that doc's own scope note.

---

<a id="adr-009"></a>
## ADR-009 — Production revisit of ADR-002: FastAPI backend + Postgres checkpointer

**Context:** [ADR-002](#adr-002) chose in-process Streamlit-imports-the-graph
specifically to avoid serializing `interrupt()`/resume state across an HTTP
boundary, for a single local user. Production has 1,000+ DAU — multiple
concurrent users and, for availability, multiple running instances of
whatever serves them. `MemorySaver` is per-process memory; it cannot be
shared across instances or survive a pod restart.

**Decision:** Wrap the compiled graph in a FastAPI service; replace
`MemorySaver` with LangGraph's `PostgresSaver` against Aurora PostgreSQL.
The HTTP-boundary/serialization problem ADR-002 originally sidestepped is
now solved directly, because it can no longer be avoided.

**Alternatives:**
- *Keep Streamlit-imports-the-graph, scaled out with a shared external
  checkpoint store* — fewer new components (no FastAPI layer), but couples
  the UI framework's process lifecycle to the orchestration layer in
  production, and Streamlit's own session model isn't designed to be one of
  N stateless replicas behind a load balancer. Rejected: it solves the
  same durability problem this ADR solves, while adding UI-framework
  constraints to the backend for no benefit.
- *Keep `MemorySaver`, single instance, no HA* — simplest, but a single
  point of failure and a hard ceiling on concurrent users; incompatible
  with the target DAU.

**Consequences:**
- `+` Multiple stateless API replicas become possible; any replica can
  resume any user's `interrupt()`'d session because state lives in Aurora,
  not in a process.
- `+` The frontend becomes swappable (Streamlit-as-client today, anything
  else later) without touching the graph or its persistence.
- `−` A real database and its operational overhead (backups, failover,
  connection pooling) now sit in the critical path of every request,
  something the original no-backend design explicitly avoided needing.

---

<a id="adr-010"></a>
## ADR-010 — Bedrock for chat only, not embeddings, in production

**Context:** [ADR-006](#adr-006) already made the generation model a
config-driven choice (`init_chat_model`) and pinned embeddings to OpenAI as
a deliberate, narrower exception. Moving to AWS raises the question of
whether Bedrock should replace one or both.

**Decision:** Use Bedrock (Claude, via `bedrock_converse`) for the chat
model in production — a config change, not a code change, per ADR-006's own
design. Keep OpenAI `text-embedding-3-small` for embeddings, unchanged.

**Alternatives:**
- *Move embeddings to Bedrock too (e.g. Titan or Cohere embeddings)* —
  single-vendor AWS stack, one fewer external dependency (no OpenAI egress
  needed at all). Rejected for production v1: it requires re-embedding the
  entire corpus and revisiting a decision (ADR-006's embedding pin) that
  isn't broken, purely for vendor consolidation with no functional benefit;
  worth reconsidering later on its own merits, not bundled into this
  deployment.
- *Bedrock Knowledge Bases for retrieval too* — see [ADR-011](#adr-011);
  rejected there for the same production pass.

**Consequences:**
- `+` No new embedding-quality risk introduced at launch — the corpus's
  retrieval behavior (already exercised by UC-2/UC-5 in the base suite)
  carries over unchanged.
- `+` Query-time traffic has no OpenAI dependency at all — only the weekly
  ingestion job calls OpenAI, which bounds the blast radius of an OpenAI
  outage to "this week's data refresh is late," not "answers stop working"
  (see [PRODUCTION.md §Failure modes](PRODUCTION.md#failure-modes--production-additions)).
- `−` The stack isn't single-vendor: production still needs an OpenAI API
  key and egress path (NAT gateway) alongside AWS, purely for the
  ingestion job.

---

<a id="adr-011"></a>
## ADR-011 — Aurora PostgreSQL + pgvector over a dedicated vector service, in production

**Context:** Chroma's local `PersistentClient` doesn't survive multiple
replicas or provide HA. Production needs a real, managed vector store, and
`ADR-009` already puts Aurora PostgreSQL in the stack for the LangGraph
checkpointer. The `games` corpus is small (~800 rows today, growing by
~17-18 games/week in-season) and unlikely to reach a size where a
purpose-built vector engine outperforms `pgvector` in any way that matters.

**Decision:** Enable `pgvector` on the same Aurora cluster already required
for checkpoints; `retrieval_node`/`agentic_retrieval_node`'s hybrid
filter+semantic-search logic (`ADR-003`) is unchanged, only its backing
store's query syntax changes.

**Alternatives:**
- *OpenSearch Serverless (vector engine)* — purpose-built, scales
  independently of the relational store. Rejected for now: it's a second
  managed service, a second set of IAM/networking/observability surface,
  and a second bill, for a corpus this small — the operational cost isn't
  justified by anything in either target tier. Explicitly the right call to
  revisit if corpus size or concurrent vector-query load ever genuinely
  contends with the checkpoint workload on the same cluster.
- *Bedrock Knowledge Bases* — fully managed retrieval, no vector store to
  operate at all. Rejected on the base suite's own terms: it would hand
  [ADR-003](#adr-003)'s hybrid filter/semantic split — the retrieval-quality
  decision this project exists to practice — to the platform's own
  (opaque) retrieval logic, which is a bigger loss than any ops savings for
  a corpus this size.

**Consequences:**
- `+` One database to operate, back up, and monitor instead of two —
  fewer moving parts than either alternative.
- `+` `ADR-003`'s retrieval logic (the metadata filter / semantic query
  split) survives the production migration completely unchanged; only the
  storage layer underneath it moves.
- `−` Couples the checkpoint store's and the vector store's scaling
  profiles onto one cluster — if one ever needs to scale independently of
  the other, this ADR is the one to revisit (see
  [PRODUCTION.md §Scale delta](PRODUCTION.md#scale-delta-100k-questionsday)).

---

<a id="adr-012"></a>
## ADR-012 — EKS over ECS Fargate for production compute

**Context:** Either EKS or ECS Fargate can run the containerized backend
API and the ingestion CronJob at the target scale — neither tier's traffic
(§Target scale in [PRODUCTION.md](PRODUCTION.md#target-scale--traffic-shape))
comes close to needing Kubernetes-specific scheduling features. The
deciding factor here isn't the workload, it's the team: a platform team
already runs (or plans to run) other services on Kubernetes.

**Decision:** Deploy this workload onto that existing/planned EKS cluster
rather than standing up ECS Fargate as a second compute paradigm for one
service.

**Alternatives:**
- *ECS Fargate* — genuinely simpler for this workload in isolation (no
  cluster to operate, no node group sizing); would be the recommended
  default for a team without existing Kubernetes investment. Rejected here
  specifically because the team's stated posture is "have/plan a platform
  team, comfortable with Kubernetes" — introducing ECS alongside EKS would
  mean maintaining two compute platforms instead of one, which costs more
  in practice than Kubernetes' extra baseline complexity.
- *Lambda* — per-request billing fits the low/bursty traffic shape well in
  principle, but LangGraph's agentic loops (multi-hop retrieval, multi-call
  tool use, reflection retries) can run long and unpredictably relative to
  Lambda's timeout model, and cold starts on a graph this heavy would hurt
  p95 latency. Rejected on fit, independent of the team-posture question.

**Consequences:**
- `+` One compute platform, one set of platform-team runbooks/tooling,
  for this service and whatever else the team already runs.
- `+` Room to add Kubernetes-native primitives later (e.g. KEDA for
  event-driven autoscaling if `ADR-014`'s scope ever grows) without a
  platform migration.
- `−` More baseline operational surface (cluster upgrades, node group
  management, CNI/IAM integration) than ECS Fargate would have needed for
  this workload alone — accepted because that surface is amortized across
  the team's other services, not paid for by this project in isolation.

---

<a id="adr-013"></a>
## ADR-013 — ALB-native Cognito auth over API Gateway, at Tier 1

**Context:** Production has real user accounts for the first time (unlike
the base project's single-local-user, no-auth stance). Something must
authenticate requests before they reach the backend. Both API Gateway
(with a Cognito authorizer) and the ALB's own `authenticate-cognito`
listener action can do this.

**Decision:** Use the ALB's native Cognito authentication action directly
in front of the backend service; no API Gateway at Tier 1.

**Alternatives:**
- *API Gateway + Cognito authorizer* — adds per-client usage plans, request
  quotas, and API-key-based throttling that ALB alone doesn't offer.
  Rejected for now: neither target tier has a stated need for per-client
  rate limiting yet (Tier 1's whole system sees single-digit req/s); adding
  it preemptively is exactly the kind of unjustified complexity this doc's
  governing principle rejects.

**Consequences:**
- `+` One fewer HTTP-routing layer between the user and the backend —
  simpler request path, one less service to operate.
- `+` Sufficient for the actual Tier 1 requirement: reject unauthenticated
  requests before they reach a pod.
- `−` No per-client quotas/usage plans if a single account starts abusing
  the API — mitigated only by Cognito-level throttling and, if needed, a
  reactive account suspension, not a proactive rate limit. Explicitly the
  trigger to add API Gateway at Tier 2 (see
  [PRODUCTION.md §Scale delta](PRODUCTION.md#scale-delta-100k-questionsday)).

---

<a id="adr-014"></a>
## ADR-014 — Scheduled batch ingestion over managed/streaming alternatives, in production

**Context:** Production needs the corpus to track the live NFL season
(new games appearing weekly), unlike the base project's fixed 2021-2023
historical ingest. `nfl_data_py`'s upstream source itself only refreshes on
roughly a weekly cadence (games complete in batches over a week, not
continuously), which bounds how much sophistication the ingestion trigger
actually needs.

**Decision:** An EventBridge Scheduler rule triggers an EKS CronJob that
reruns the existing `ingest.py` logic (unchanged upsert-by-`game_id`
idempotency) weekly, timed after the week's Monday Night Football game
completes.

**Alternatives:**
- *Bedrock Knowledge Bases' managed data-source sync* — would have handled
  scheduling internally, but is already rejected in [ADR-011](#adr-011) for
  the retrieval-boundary reason stated there; adopting it just for its
  sync scheduler while rejecting it for retrieval would be an inconsistent,
  partial adoption.
- *Near-real-time/streaming ingestion (e.g. per-play or per-game-completion
  events)* — would let the corpus update within minutes of a game ending
  instead of up to a week later. Rejected: no use case in this project asks
  about a game before the following week (the questions are always
  retrospective analysis, per [PRD.md](PRD.md)'s use cases), so the
  freshness this would buy has no consumer; it would be solving a latency
  problem nobody has.

**Consequences:**
- `+` Reuses `ingest.py`'s existing logic and idempotency guarantee
  entirely as-is — no new ingestion code, only a new trigger and a new
  target store (`ADR-011`).
- `+` This is the one place event-driven design genuinely earns its place
  in this architecture (see
  [PRODUCTION.md §Event-driven design](PRODUCTION.md#event-driven-design)) —
  a real, justified instance rather than a default reach for the pattern.
- `−` A missed or failed scheduled run means the corpus is stale until the
  next week's run or a manual retrigger — acceptable because the failure
  mode is "last week's data, still correct" not "wrong data," and it's
  alerted on (see [PRODUCTION.md §Failure modes](PRODUCTION.md#failure-modes--production-additions)).

---

<a id="adr-015"></a>
## ADR-015 — Hybrid-keyed semantic response cache (Redis/MemoryDB) in front of generation

**Context:** Bedrock cost and p95 latency both scale with LLM call volume,
and NFL question traffic has genuine repeat overlap across the whole user
base (many users asking about the same recent game or stat), unlike a
typically-personalized product where caching across users buys little.
Naive semantic caching — matching purely on question-embedding similarity —
reintroduces the exact problem [ADR-003](#adr-003) already solved for
retrieval: two questions differing only in season or week can embed closer
together than they are semantically distinct, and a cache hit bypasses
`reflection_node` entirely, so there's no safety net to catch a wrong hit
the way there is on a normal generation.

**Decision:** Add `cache_lookup_node` after
`retrieval_node`/`agentic_retrieval_node` (once `season`/`game_type`/`week`/
`semantic_query` are known) and before `generation_node`. Cache key =
**exact match** on `season`/`game_type`/`week`/`intent`, plus embedding
similarity (above a fixed threshold) on the semantic query/context text —
the same discrete-vs-semantic split `ADR-003` already applies to retrieval,
reapplied to the cache key. On a hit, skip straight to `response_node` with
the cached answer. On a miss, run generation/reflection normally, and add
`cache_write_node` after a *passing* `reflection_node` result only (never
on a caveat-exhausted answer) — TTL tied to the weekly ingestion cadence
(`ADR-014`) so an entry always expires before the corpus it was computed
against could have changed. Backing store: Amazon MemoryDB for Redis, using
RediSearch vector similarity for the embedding component.

**Alternatives:**
- *Pure semantic caching (embedding similarity only)* — the standard
  "GPTCache"-style pattern, simplest to implement. Rejected: it's the
  ADR-003 failure mode restated for caching instead of retrieval, with a
  worse blast radius, since a cache hit has no reflection check behind it.
- *Cache the retrieved context, not the final answer* — would preserve a
  fresh generation+reflection pass every time, keeping the safety net
  fully intact. Rejected: the LLM calls, not retrieval, are the expensive
  and slow part, so this leaves most of the actual cost/latency win on the
  table.
- *No cache* — simplest, but forfeits a low-risk win the domain's genuine
  repeat-question overlap makes unusually cheap to capture here.

**Consequences:**
- `+` Meaningful Bedrock cost/latency reduction on repeat/near-repeat
  questions, and the benefit scales *up*, not just proportionally, with
  traffic (see [PRODUCTION.md §Scale delta](PRODUCTION.md#scale-delta-100k-questionsday)).
- `+` Reuses `ADR-003`'s hybrid-key insight instead of re-deriving cache
  correctness from first principles.
- `−` A bad cache entry, if one ever gets written, is served to every
  subsequent similar question until TTL expiry — a larger blast radius
  than one wrong answer to one user. Mitigated (write-gated on a reflection
  pass) but not eliminated.
- `−` A new stateful component with its own availability story; must fail
  open (treat unavailability as a cache miss), not fail closed — see
  [PRODUCTION.md §Failure modes](PRODUCTION.md#failure-modes--production-additions).

---

<a id="adr-016"></a>
## ADR-016 — Session-scoped `thread_id` + conversational memory in `GraphState`

**Context:** Today, `ui/app.py` generates a fresh random `thread_id` per
question (not per session), so the checkpointer never accumulates state
across turns, and `GraphState`'s fields (`question`, `context`,
`draft_answer`, etc., [graph/state.py](../graph/state.py)) are each
overwritten per turn with no memory of prior ones. Every question is
answered as if it's the first one asked. A real chat product needs
follow-ups ("what about their record last week," "and the Eagles?") to
resolve against the previous turn — which needs both a `thread_id` stable
for a session's duration and a state field that actually retains prior
turns rather than discarding them.

**Decision:** (a) mint one `thread_id` per session (session start, e.g.
login), reused for every question in that session instead of regenerated
per question; (b) add an accumulating `history` field to `GraphState`
(last N question/`final_answer` pairs — N tuned against prompt
context-window budget, not fixed here); (c) update `router_node`,
`retrieval_node`/`agentic_retrieval_node`'s query construction, and
`generation_node`'s prompt to consult `history` when resolving an ambiguous
follow-up.

**Alternatives:**
- *Client-side history (frontend resends the full prior conversation each
  request, no server accumulation)* — no new `GraphState` field needed.
  Rejected: doesn't reuse the Postgres-backed checkpointer `ADR-009`
  already requires for HITL, and pushes truncation/summarization logic to
  every client instead of handling it once, server-side.
- *Full-transcript memory, no truncation* — simplest correctness story.
  Rejected: unbounded prompt growth breaks the context-window and
  per-question cost budget over a long session; needs a bound designed in
  from the start, not patched in later under production pressure.

**Consequences:**
- `+` Follow-up questions resolve naturally — the core behavior users
  expect from "chat" that this system doesn't do today.
- `+` The same Postgres-backed checkpointer `ADR-009` already requires for
  HITL now pays for a second feature, once `thread_id` becomes
  session-scoped instead of per-question.
- `−` A genuinely new failure mode: `reflection_node` must now also guard
  against a follow-up incorrectly carrying over the wrong team/game/season
  from `history` (topic-carryover misattribution) rather than resolving the
  current question on its own terms — needs its own verify case beyond the
  base suite's UC-1..7 (see [PRODUCTION.md P9](PRODUCTION.md#deployment-roadmap)).
- `−` Chat history becomes real, retained user data tied to a Cognito
  identity for the first time in this project — the retention/deletion
  policy and session-to-identity scoping flagged in
  [PRODUCTION.md §Security & secrets](PRODUCTION.md#security--secrets) are
  no longer optional once this ships.
- `−` History window size and summarization-vs-truncation strategy is an
  ongoing tuning problem, not a one-time decision.
