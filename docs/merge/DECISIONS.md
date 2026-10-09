# Person decision semantics v2

2026-10-08. P-119 implementation, informed by OneMaster evaluator_v2.py,
preprocessing.py and contracts_v2.py. No OneMaster runtime imports/source copies
or new dependencies; reference repository remains read-only.

## Contract boundaries

`pipeline/decision_semantics.py` defines five separate contracts:

| Contract | Meaning |
| --- | --- |
| PairEvidence | Observed field comparisons and rule weight, not a probability |
| HardConstraints | Reasons forbidding merge and explicit cannot-link membership |
| PairEvaluation | Algorithm conclusion MATCH/NON_MATCH/UNDETERMINED and routing |
| MergeEligibility | Manual/automatic permission, respecting constraint and review veto |
| ReviewDecision | Stored reviewer intent, independent of algorithm conclusion |

`REJECTED` means rejecting the proposed merge. It vetoes execution, including
transitive auto closure, but never creates a negative edge or a NON_MATCH
identity conclusion. Proposal/case BLOCKED, REJECTED and DEFERRED also do not
imply cannot-link. Explicit persisted `user_decision=NON_MATCH` is retained as
historical adjudication. Active `ResolutionNegativeEdge` rows (including split
constraints and previously recorded steward negatives) remain authoritative
cannot-links; their historical provenance is not rewritten. A cannot-link
forbids union but alone does not infer a NON_MATCH identity conclusion.

Existing serving `NOT_MERGEABLE` remains a compatibility permission label.
The adapter never upgrades it to a proven negative identity conclusion. Public
enum values, IDs, API bodies, counters and export columns remain unchanged.
Typed results are internal; no DB migration or historical backfill occurs.

## Activation and compatibility

`DECISION_SEMANTICS_V2_ENABLED=false` is the default, following AGENT_RULES rule 9.
`true` enables the corrected person constraints, evidence annotations and manual
re-evaluation described below. Both paths use the same evaluator with the
existing P-119 floor, group/subgroup and YAML policy adapter. Rule policy
thresholds/units are unchanged; OneMaster probability profiles are not imported.
Group B continues to be represented by normalization tags/signatures rather
than becoming a new primary decision group. Active Group D manual approval and
legacy four-eyes flow retain their respective existing contracts.

The setting is pinned in SCORE identity, together with the decision module's
source hash. Changing it during scoring/DECIDE fails. Existing artifacts with a
different setting/source identity require a fresh artifact location. Deploy
only after representative shadow evaluation; rollback is setting false with
fresh artifacts. This does not undo decisions already saved by reviewers.

Retry preservation, explicit negative-edge loading and removal of the misleading
new legacy rejection reason `USER_CANNOT_LINK` apply in both modes. Historical
reason strings remain untouched; new rejections use `USER_PROPOSAL_REJECTED`.

## Evidence and constraints when enabled

- Different phone/email values, even independently verified, permit changed or
  multiple contacts. They remain DF with `CHANGE_OR_MULTI_CONTACT`, not identity
  conflicts. Verification requires literal boolean true or exact status
  `verified`; a truthy string is not verification.
- Equal contacts consider existing full-snapshot frequency risk and quality,
  explicit shared/shared_risk metadata, copied origin IDs and historical/closed
  contact entries. Risk is SH and cannot supply a contact auto-merge anchor.
  Another matching entry cannot hide a risky common contact. Existing P-119
  frequency boundary (>5 scalar occurrences) is retained.
- DOB CF retains its D subgroup for compatibility. Unverified CF is annotated
  `UNVERIFIED_DIFFERENCE`; verified, usable DOB conflicts forbid merge but never
  automatically conclude NON_MATCH. Existing partial-date/swap handling remains.
- Government IDs require VALID quality, verification, explicit type and explicit
  namespace. Equal numeric strings in different/unknown namespaces are NC and
  cannot become an ID anchor. The supported person identifier contracts are
  `(VN, CCCD_12)` and `(VN, CMND_9)`. Other domains require explicit
  `government_id_identity_unique=true`; different IDs additionally require
  `government_id_single_valued=true` on both endpoints to forbid merge. Type or
  namespace is never synthesized from a raw numeric match; VALID syntax alone
  is not verified identity. Multiple-valued IDs remain DF.
- Metadata belongs to normalized input (`_<field>_verified` or
  `<field>_verification_status`, government_id_namespace, contact risk flags or
  contact-entry metadata). Current CSV intake does not promote arbitrary extra
  columns into trusted verification metadata. No trust/backfill inference from
  original/extra fields is made by this task.
- Person policy is forbidden for ORGANIZATION and other declared domains.
  UNKNOWN retains P-119 compatibility; it is not a new organization contract.

## Shared evaluator and ordering

`matching.decide_rule_result` (including score_pair and ScoringEngine callers)
and all manual approval entry points use `evaluate_prepared`. Manual review
re-evaluates current normalized records and active negative edges for every
internal pair, including non-candidate pairs after proposal expansion. It can
override soft auto policy, but not hard constraints or a saved rejection.
Cluster guards likewise inspect cross-member person constraints.

SCORE performs pair-local comparison/preprocessing in bounded endpoint batches,
with a full selected-snapshot contact census. There is no union during pair
preparation. CLUSTER runs after complete DECIDE and sorts by weight descending,
pair ID ascending. Execution vetoes are separate from cannot-links. Tests
permute batch completion order and verify identical proposals/edge order and
the transitive rejection veto.

## Retry and persistence

Both pipeline sizes and StageRunner direct materialization use
`resolution_persistence.persist_snapshot`, in an ORM transaction with the run
lock used by active pair review. Existing pairs are mapped by canonical member
IDs and never replaced, even when the newly computed pair ID differs. Their
algorithm fields, reviewer decisions and revisions survive. Existing proposals,
golden overrides, cases, reviewer metadata, audit, previews, exports and history
are not deleted or updated by replay. New auto proposals cannot overlap saved
proposals (including rejected/history) or saved reviewer-owned members.

This intentionally conservative insert-only retry is not a recomputation or
generation-publication protocol. It may reserve more members than a future
reconciliation flow would. Existing algorithm decisions are not silently
reclassified when the flag changes. Reconciliation must separately preserve
review state/history and invalidate affected previews. The former independent
DuckDB/PostgreSQL serving INSERTs are removed; large persistence now shares the
same preservation rules and transaction boundary as small persistence.

## Evidence limits and dependencies

Tests use disposable SQLite, local Parquet/DuckDB and synthetic records; no
production DB, R2, LLM or frontend mutation. PostgreSQL row locks/concurrency,
10k+ serving throughput/RSS and production shadow accuracy are not certified.
ORM publication can have different throughput from the removed SQL INSERT
path. Manual re-evaluation currently reads the run snapshot/census and is not
a bounded-memory claim. SCORE batches are bounded; global decision/cluster
state remains materialized as before.

Pending: representative shadow evaluation, authoritative verification/namespace
input contract, explicit reconciliation protocol and an organization domain
contract before extending scope. Default remains false until those rollout
requirements are met. Test commands/results are recorded in [STATUS](STATUS.md).

Reference working-tree SHA-256 (license not confirmed; no copied source):

- evaluator_v2.py: `DC278BFF05873B52DC6F4AE2B789223B9E00F3935285EAC47812426C87DFF15F`
- preprocessing.py: `B7CF6DA321FFDC9BAE882EB9E601B65752B1B9C6DA712570246F64E29D54BE43`
- contracts_v2.py: `D27123DD3D5F7843C558A6680CF9FB8A2A2F4ABDD26F4A510ED10FB65497A711`
