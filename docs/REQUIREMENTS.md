# Requirements — NFL Stats Agent

`FR-x.y` = functional, `NFR-x.y` = non-functional. Acceptance criteria draw
directly from [PRD.md §Core Use Cases](PRD.md#core-use-cases) (`UC-1`..`UC-7`)
— there's near-zero invention here since the test cases already exist.

## Functional requirements

### FR-0.x — Routing

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-0.1 | `router_node` classifies a question as factual, analytical, or predictive and routes to the matching branch | P0 | Each of UC-1..UC-7 is routed to the branch its pattern requires (factual: UC-1, UC-4, UC-5, UC-6; analytical: UC-2, UC-3; predictive: UC-7) |

### FR-1.x — RAG

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-1.1 | `retrieval_node` answers a single-hop factual question via a hybrid query: `season`/`game_type`/`week` (when stated) as a Chroma `where` filter, team names/ambiguity as the semantic query | P0 | UC-1 ("49ers vs. Cowboys, 2023 playoffs") returns the correct final score |
| FR-1.2 (ingestion) | `data/ingest.py` chunks `games` one-game-per-chunk with `season`, `game_type`, `week`, `home_team`, `away_team` as Chroma metadata | P0 | Querying Chroma directly with `where={"season": 2023, "game_type": "POST"}` returns only 2023 postseason games |

### FR-2.x — Agentic RAG

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-2.1 | `agentic_retrieval_node` runs retrieve → `assess_sufficiency` → refine-and-retry, where the second retrieval's filter depends on the first retrieval's result | P0 | UC-2 ("team that beat the Chiefs in Week 13, 2023 — how far in the playoffs") triggers a second, different retrieval (the opponent's playoff games) and returns the correct answer |

### FR-3.x — Tool / function calling

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-3.1 | `calculate_team_stats(team, season, metric)` computes points_per_game, yards_per_game, turnover_differential, third_down_pct, or red_zone_efficiency from `pbp` | P0 | UC-3 ("Chiefs' turnover differential vs. Eagles'") produces two tool calls (one per team) and a correct synthesized comparison |
| FR-3.2 | `get_standings(conference, season)` aggregates conference standings from `games` only | P0 | Returns correct W-L ordering for a given conference/season, with no `pbp` involvement |
| FR-3.3 | No `compare_teams` tool exists; multi-team comparisons are served by the model calling `calculate_team_stats` once per team in the same turn (`ADR-005`) | P0 | UC-3 shows exactly two `calculate_team_stats` calls, not one `compare_teams` call |

### FR-4.x — Reflection

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-4.1 | A correctly-grounded answer passes reflection with no retry | P0 | UC-4 ("Eagles' total offensive yards, Week 1 2023") passes on the first attempt |
| FR-4.2 | A coverage failure (context doesn't contain what's needed) routes back to the retrieval step (`retrieval_node` on the factual path, `agentic_retrieval_node` on the analytical path) with a refined query | P0 | UC-5 (ambiguous Chiefs/Eagles query — regular season vs. Super Bowl) triggers exactly this route at least once during testing |
| FR-4.3 | A grounding failure (cited number not present in the source) routes back to `generation_node` with a correction instruction | P0 | UC-6 (Eagles' passer rating, Week 1 2023 — not in the chunk template) either answers "not available" (pass) or, if a number is hallucinated, triggers this route |

### FR-5.x — HITL

| ID | Requirement | Priority | Acceptance criteria |
|---|---|---|---|
| FR-5.1 | On the predictive branch, `hitl_node` shows grounding stats + a reasoning sketch and pauses via `interrupt()` *before* `generation_node` produces any speculative content | P0 | UC-7 ("who wins if the Chiefs and Eagles played again") shows real per-team numbers and waits for confirmation; no speculative text exists before confirmation |
| FR-5.2 | Declining at the HITL gate routes straight to `response_node` with no prediction generated | P1 | Declining UC-7's confirmation produces a response stating no prediction was generated, not a degraded/partial prediction |

## Non-functional requirements

| ID | Requirement | Quantified as |
|---|---|---|
| NFR-1 | Reflection's grounding-failure and coverage-failure edges share one retry budget | Max 2 retries total across both edges |
| NFR-2 | `agentic_retrieval_node`'s `assess_sufficiency` loop is capped | Max 2 retrieval attempts (*Assumption* — the original design left this as "max N" without naming N; set equal to `NFR-1`'s budget for consistency, retire this assumption once tuned during Phase 2 of the build) |
| NFR-3 | ChromaDB persists across restarts without re-embedding | `chromadb.PersistentClient(path="./chroma_db")` |
| NFR-4 | The generation model is selected by config, not hardcoded in node code | `init_chat_model` with provider/model from env/config (`ADR-006`); default `claude-sonnet-4-6` |
| NFR-5 | System handles the full corpus in a single local session | ~800 `games` chunks (Chroma) + ~50k `pbp` rows (in-memory DataFrame), 1 concurrent session — see [ARCHITECTURE.md §Scale](ARCHITECTURE.md#scale--capacity-model) |
| NFR-6 | API cost/budget | No hard cap set — out of scope given the one-time dataset size and low-volume interactive local usage; revisit only if a non-default, more expensive model is configured (`ADR-006`) |

## P0 summary — the MVP

Every `FR-x.x` above is P0 except `FR-5.2` (P1). For a tightly scoped
project, P0 is effectively the whole functional scope: all five patterns
working end-to-end, verified against UC-1..UC-7. There is no P2 — the only
deferred item is the blog write-up ([PRD.md §Goals &
Non-goals](PRD.md#goals--non-goals)), which isn't a system requirement at all.

## Open assumptions

Places where a value wasn't pinned down, and this suite picked a default to
keep moving rather than block on it. Retire these during the build, not by
treating them as settled:

- **`NFR-2`'s cap (N=2):** the original design left the agentic-retrieval
  retry cap as "max N" with no number. Set to 2 here for consistency with
  `NFR-1`; revisit if 2 proves too tight or too loose during Phase 2 of the
  build.
- **Reflection-budget-exhausted behavior:** `NFR-1` specifies the cap but
  not what happens when it's hit and the answer still fails. This suite
  assumes `response_node` returns the best available answer with an
  appended caveat (see [ARCHITECTURE.md §Failure
  modes](ARCHITECTURE.md#failure-modes--degradation)) rather than failing
  the turn outright — confirm this matches the intended UX once built.
- **Grounding check mechanism:** assumed to stay a single LLM reflection
  prompt covering both grounding and coverage. Flagged in
  [AI-ARCHITECTURE.md](AI-ARCHITECTURE.md#where-ai-earns-its-place--adapted-for-a-learning-project)
  as a candidate to hybridize with a deterministic string-match check if the
  LLM grounding check proves unreliable in practice — not done by default.
