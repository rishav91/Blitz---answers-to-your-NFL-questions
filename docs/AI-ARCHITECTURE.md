# AI Architecture — NFL Stats Agent

## Where AI earns its place — adapted for a learning project

The standard "earns its place" test asks whether an LLM is the *only* path
to value locked in unstructured language, with no deterministic/classical-ML
alternative reaching the bar. That test assumes a production system trying
to minimize LLM usage. **This project inverts the incentive**: its purpose
is to exercise five LLM-mediated patterns deliberately, so "could this be
done without an LLM" is not, by itself, a reason to cut it.

The bar this project actually applies — the governing principle, restated
for AI calls specifically — is narrower than "could a model do this," but
broader than commercial ROI: **every LLM call must occupy a genuinely
distinct architectural role; none may be redundant with another node's
mechanism.** And the standard rule about *output type* still holds even in
a learning project: an LLM call's output
is a classification, a retrieval query, a tool-call decision, a synthesis,
or a critique — never a number standing in for arithmetic. Numbers come from
Chroma metadata exact-match or from deterministic aggregation in
`calculate_team_stats`/`get_standings`; the model narrates, decides, or
checks, but does not compute.

| Pattern | What the LLM outputs | Could it be done without an LLM? | Why it's still an LLM call here |
|---|---|---|---|
| Routing (`FR-0.x`) | A classification: factual / analytical / predictive | Often yes — keyword match handles most cases | A simple keyword match or a short classification prompt both satisfy this requirement — the choice between them is treated as a design decision, not a default to maximize LLM usage |
| RAG (`FR-1.x`) | A drafted answer from one retrieved chunk | No — synthesizing fluent prose from a chunk is exactly the unstructured-language job | Core to the pattern being practiced |
| Agentic RAG (`FR-2.x`) | A sufficiency judgment + a refined query | Partially — a rule-based "did we get N results" check could substitute, but judging *semantic* sufficiency needs language understanding | Is the pattern being practiced |
| Tool calling (`FR-3.x`) | A decision to call a tool, with which arguments, how many times | No — deciding *that* and *how many* `calculate_team_stats` calls a comparison question needs is the pattern itself | Is the pattern being practiced |
| Reflection (`FR-4.x`) | A grounding/coverage judgment + a reason string | No — checking whether cited numbers appear in source text is feasible as exact-string matching for the *grounding* half, but coverage ("does the context answer every part") needs language understanding | Is the pattern being practiced; could be partially hybridized (see below) |
| HITL (`FR-5.x`) | A reasoning sketch shown before the interrupt | No — the spoken rationale is the point; the underlying numbers are tool/retrieval output, not the LLM's | Is the pattern being practiced |

**Note on Reflection's grounding check:** the *grounding* half (do cited
numbers appear in the source) is mechanically closer to a string/regex
match than a judgment call — a non-LLM check could plausibly do this more
reliably than prompting a model to "check." The design keeps a single
reflection prompt covering both grounding and coverage for the initial
implementation, but flags this as worth revisiting if grounding checks
prove unreliable in practice (*Assumption*, see
[REQUIREMENTS.md §Open Assumptions](REQUIREMENTS.md#open-assumptions)).

## Pipeline / funnel

`router_node` is the funnel: it decides up front how much retrieval/tool
work a question needs, so e.g. a factual single-hop question never enters
the agentic-retrieval loop or invokes a tool. This keeps LLM/tool-call volume
proportional to question complexity rather than running every path's full
machinery on every question.

## RAG / retrieval & grounding

- **Source:** `games` schedules (2021–2023), chunked one-game-per-chunk,
  embedded with `text-embedding-3-small`, stored in ChromaDB.
- **Scoping:** hybrid — `season`/`game_type`/`week` (when explicit) as a
  Chroma `where` filter; team names and ambiguous phrasing as the semantic
  query (`ADR-003`).
- **Grounding:** `reflection_node` checks that any number in the drafted
  answer appears in the retrieved chunk (factual path) or chunk + tool
  result (analytical path) — see `FR-4.x`.
- **Citation:** chunks carry `season`, `game_type`, `week`, `home_team`,
  `away_team` as metadata, so a grounded answer can be traced back to the
  exact chunk that produced it.

## Deterministic vs. ML vs. LLM split

| Computation | Mechanism |
|---|---|
| Exact game lookup by season/week/type | Deterministic — Chroma metadata `where` filter |
| Semantic disambiguation of underspecified games | Embedding similarity (`text-embedding-3-small`) — ML, not LLM |
| Points/yards/turnover/3rd-down/red-zone metrics | Deterministic — pandas aggregation over `pbp` in `calculate_team_stats` |
| Conference standings | Deterministic — pandas aggregation over `games` in `get_standings` |
| Intent classification | LLM (or keyword match, see table above) |
| Sufficiency judgment, tool-call planning, answer synthesis, reflection scoring, HITL reasoning sketch, speculative prediction | LLM |

## Safety

- **Input trust:** single local trusted user typing questions about public
  NFL data — not an adversarial or multi-tenant input surface. No
  prompt-injection defense is built; this is accepted
  because of the local, single-user scope (see [PRD.md
  §Excluded](PRD.md#goals--non-goals)) and would need revisiting before any
  exposure beyond a local session.
- **Ingestion trust:** `nfl_data_py` is a trusted batch source pulled once at
  setup time, not live untrusted user content — no sanitization pipeline
  needed beyond the existing field-presence checks in `ingest.py`.
- **Instruction/data separation:** retrieved chunks and tool results are
  passed to the model as context, not as instructions — `reflection_node`'s
  correction instructions are the only place the system feeds the model
  text generated by an earlier model call, and that text is a fixed
  template ("you cited a number not present in the source — rewrite using
  only the text below"), not free-form.

## Governance & telemetry

- **Evaluation:** the Test Queries / use-cases table (UC-1..UC-7,
  [REQUIREMENTS.md](REQUIREMENTS.md)) is the eval set — manually run and
  checked against public NFL data, once per pattern at minimum.
- **Cost tracking:** none built; not needed at this scale (~800 chunks,
  single session, bounded retries). Token cost is naturally bounded by
  `NFR-1` (max 2 reflection retries) and the at-most-two-calls shape of the
  comparison use case (`ADR-005`).
- **Per-tenant budgets:** not applicable — one user, no tenancy.
- **Auditability:** LangGraph's checkpointer retains the full state history
  for a thread, which is sufficient to inspect what a given run retrieved,
  called, or generated during development — no separate audit log is built.
