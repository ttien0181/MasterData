# Engine/API adapter

2026-10-08. No frontend changes. The runtime boundary is the mounted MDM API,
`frontend/src/lib/contracts.ts`, `api.ts`, and `resolutionChoices.ts`; legacy
schemas with `expected_revision` are not the active pair-review contract.

## Sessions 02–09 inspected

The commit history, changes and corresponding runtime contracts were reviewed:

| Session | Commit | Boundary used by this adapter |
| --- | --- | --- |
| 02 | `7d18a29` | Lossless source mapping, PERSON scope, stable record identity/lineage |
| 03 | `86900b0` | Revalidated input/OneMaster compatibility; no external runtime imports |
| 04 | `7f91458` | Same snapshot-wide CandidateEngine for small/large pipelines |
| 05 | `b4dea98` | Sparse retrieval opt-in; not enabled by the engine selection flag |
| 06 | `463afac` | ScoreResult separates rule units, probability, Splink log weight |
| 07 | `f3d6aea` | Shared person evaluator and persisted reviewer decisions, A/B/C/D adapters |
| 08 | `5717d9b` | Deterministic constrained clustering and golden provenance/alternatives |
| 09 | `b547036` | Validated generation staging and atomic serving publication |

These are selective transfers into P-119, not a runtime dependency on the
OneMaster workspace. See [CONTRACTS](CONTRACTS.md), [SCORING](SCORING.md),
[DECISIONS](DECISIONS.md), [CLUSTERING](CLUSTERING.md), [GENERATIONS](GENERATIONS.md).

## Selection and rollout

`MDM_ENGINE_V2_ENABLED=false` is the safe default. Explicitly enabling it selects
`p119_person_v2` for an unmaterialized run: decision semantics, cluster safety
and generation staging are enabled together. Sparse TF-IDF and Splink scoring
remain separately configured; this flag does not train/calibrate a model.

`p119_current` retains the current implementation and separately configured
lower-level flags. It does not restore pre-migration source code. Runs with
existing serving pairs/proposals are grandfathered into that profile, even
when the new rollout flag is enabled. The first selection is stored in
`mdm_resolution_engine_selections`, keyed by run ID. Retries, manual API review,
golden survivorship and direct StageRunner publication use that pinned profile
through a request/execution ContextVar, never by mutating global Settings.
Turning off the flag affects new runs; existing selected runs retain their
semantics and publication gate. No automatic production switch or in-place
reinterpretation of reviewer state is provided.

Migration `0023_engine_selection` adds a private table only. Downgrade retains
the selection rows/table; re-upgrade is idempotent and tested with stored rows.
The previous binary ignores that additive table. Returning to legacy computation
for a selected v2 run requires a separately reviewed migration, not deletion
of saved selection or reviewer data. Deploy schema before runtime; no real DB
migration was performed here.

## Projection contracts

`EngineResultAdapter.pair` is used at the common serving persistence boundary
for legacy, staged and direct runner paths. `rule_score` maps to DTO `score`;
legacy score-only results retain their defined rule units. A payload containing
probability must provide explicit rule_score or `score_semantics=p119_rule_v1`;
conflicting, nonfinite or invalid scores fail explicitly before publication.
Probability is never copied to score. Splink probability/log weight remain in
scoring artifacts, not mislabeled frontend columns.

Field evidence retains field/left/right/similarity/state/provenance and
normalization_tags; internal diagnostic payloads are excluded. Existing pair
ID, revision, Group A/B/C/D and subgroup values remain unchanged. BLOCKED and
DEFERRED remain algorithm decisions and enter review triage; they do not
become NON_MATCH, a user decision or a cannot-link. Adapter writes contain no
reviewer action. Existing insert-only persistence continues protecting saved
pair/proposal/case decisions, overrides, membership and audit/history on retry.

All existing routes remain available: review queue/detail, batch transition,
automatic eligible proposals, manual merge, golden editing, audit, previews,
CSV export, export history, downloads and split/rollback. Active review body
remains `{decision:merge|no_merge, revision}`. Existing api.ts omits JSON content
type for some mutations; the resolution router normalizes text/plain JSON
transport while retaining Pydantic validation, authentication, CSRF, ownership
and revision checks. Multipart/binary upload routers are unaffected.

Golden GET and export project persisted `_golden_override` with
STEWARD_OVERRIDE provenance. Saved overrides take precedence over submitted
source choices for that field, including stale browser drafts. Golden PUT
locks run/proposal with manual review/publication and increments proposal
revision, invalidating old previews. Explicit `{fields:{}}`
clears overrides and rebuilds base survivorship, including overrides materialized
by an earlier manual merge. Retry does not perform that clearing. Merge-event
snapshots include overrides. Immutable historical exports are not rewritten.

## Pagination and response limits

Queue/list DTOs keep `{total,items}`, offset/limit and category filters; route
limit remains 1..100. Tests cover page boundaries and invalid limits. Pair
review compares two records. Group detail rejects over 200 members or 2000
pairs with explicit HTTP 413 and directs the caller to pair review; it never
silently truncates evidence. Internal detail pair query is ordered and limited
to 2001. Diagnostic megabyte evidence is excluded by projection; a normal
detail contract is tested below 64 KiB. These are count guards, not a universal
byte bound for arbitrary source values.

Grouped queue construction still builds the run-wide connectivity graph for
correct groups and total counts. Paginating raw edges before grouping would
change business semantics; this task does not claim bounded RSS for that
legacy graph or full production-scale API performance.

## Verification and limits

`scripts/run_merge_baseline.py --suite adapter` covers strict score/evidence,
groups/subgroups, real v2 pipeline to API DTOs, auto policy, manual review with
global flag off, batch negative review without cannot-link, golden export and
clear, stale preview, pinned retry and migration data preservation, alongside
the existing contract/review/export/regression tests.

`scripts/smoke_frontend_api.mjs` executes the unchanged frontend api.ts with
Node's TypeScript stripping, against a disposable authenticated TestClient
fixture through loopback HTTP. It exercises health, overview, pagination,
detail, review mutation and audit, including actual fetch content type. It
uses Node 24 here and does not validate React rendering, browser cookie/CORS
transport or the Next production bundle. Auth/CSRF/ownership have separate API
tests. The full frontend Vitest command is blocked because vitest/node_modules
are absent. No UI was edited to hide backend errors.

Production PostgreSQL concurrency, real R2, representative recall and scale
remain rollout dependencies. Safe default remains OFF pending that validation.
