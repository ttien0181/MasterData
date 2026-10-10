# Safe clustering and golden survivorship

P-119 uses its constrained union-find with an opt-in component guard. Enable
`CLUSTER_SAFETY_V2_ENABLED=true` together with `DECISION_SEMANTICS_V2_ENABLED=true`.
Both default OFF. No frontend or serving schema changes in this task.

## Contract

- Complete all DECIDE batches before clustering. Canonical edges use
  `match_weight DESC NULLS LAST, record_id_l ASC, record_id_r ASC`; tie-breaks do
  not depend on pair hash, batch completion or threads. Reject nonfinite weights,
  reversed/self edges and duplicate identities in the disk spool.
- Every proposed component, including same-root requests, checks all member
  pairs. Explicit cannot-link and reviewer REJECTED veto have separate reasons.
  Neither a veto nor budget/bridge rejection writes NON_MATCH.
- Missing direct A-C candidate evidence is evaluated from the immutable full
  snapshot. A usable government-ID anchor or safe contact plus name evidence is
  required. A-B phone and B-C email alone cannot merge A-C. Shared contacts and
  ID namespaces follow the common decision evaluator.
- `mdm/config/clustering_person_v2.yaml` versions size and guard-cost bounds,
  source/domain uniqueness and source priority. V2 allows same-source duplicates
  unless configured otherwise. Legacy CRM/ERP uniqueness is expressed in the
  adapter policy, preserving rollback behavior.
- Default maximum component size 20; maximum 190 pair visits per union and
  1,000,000 per run. Exhaustion sends the edge to review; cached checks still
  consume visit budget. Metrics separate visits, evaluations and cache hits.
- DECIDE uses an owned SQLite indexed spool and globally sorted cursor when
  semantic-address enrichment is OFF. Guards cache pair checks on disk. Optional
  semantic enrichment retains its existing global list/budget behavior. Records,
  candidate blocks and resulting proposals remain in memory; this is not a
  fully bounded worker. New DECIDE Parquet stores fixed identity/weight/decision
  columns plus full `decision_json`; legacy artifact layout remains when OFF.

Golden v2 ranks source priority, explicit verification, quality, actual aware
source timestamp, then stable source/external/snapshot ID/value. Naive or invalid
timestamps are unknown; no import time or external-ID timestamp is fabricated.
Phone/email collections retain distinct values and every source observation,
including shared-contact risk. Each field has selected provenance and alternatives.
Saved `_golden_override` wins during rerun and manual component expansion;
conflicting saved overrides block the expansion instead of discarding either.
Existing history/export IDs and retry insert-only preservation remain intact.
Person constraints are not a domain contract for organizations.

## Reference provenance

Read-only OneMaster working-tree `src/onemaster/clustering/person_guard.py`,
SHA-256 `C2CB44582B12B59F863836D3A9D2F1E8A0B23AEAFE32F690F142167BD0FC7FC2`.
Adapted concepts to P-119 evidence, review and policy contracts; no external
runtime import or dependency. License/distribution review remains unresolved as
recorded in PROJECT_MAP.md. OneMaster was not edited.

## Validation and rollback

New tests cover bridge chains, cannot-link versus review veto, source/domain
policy, permutations/null weight order, resource bounds, cursor barrier, small /
StageRunner parity, provenance, contacts, override retry/manual merge and OFF
regression. See STATUS.md for executed results. PostgreSQL concurrency/throughput
and production scale are not validated by SQLite tests. Disable the cluster flag
to restore legacy clustering/golden generation; saved golden/reviewer rows remain.
No migration or destructive backfill is needed.
