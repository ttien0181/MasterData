# Pair review to record groups

User-approved scope: P0–P2, 2026-10-13. Soft conflicts may be accepted with a reason. Missing direct evidence may be approved for an exact connected group with a reason and audit history. Hard constraints and Deny remain mandatory. Automatic-merge policy needs a separate decision after benchmark evaluation.

## Contracts

Pair and bulk review requests accept an optional reason (maximum 500 characters). Accepting an effective Group D pair requires a nonblank reason, persisted on the current pair and in the audit. Historical accepted soft conflicts without a reason require a reason before they can enter a new group. Other accepted pairs remain compatible.

Group approvals are completed workflow operations, bound to a fingerprint of the exact member records, effective internal pairs, decisions, reasons, assessment and execution profile. Their record IDs are unique, 2–20 members. Approval requires connectivity through accepted links and checks all member pairs for hard constraints, cannot-links, review vetoes, blocked decisions, source uniqueness and size. Only missing direct evidence is exempted. Approval is not permission for an automatic merge, and changing a member or relevant evidence invalidates it.

Cluster preview is an asynchronous CLUSTER_PREVIEW operation. It prepares an immutable decision snapshot and invokes the same guarded engine used by publication. Results contain bounded group/conflict/pending-region summaries and an immutable preview token. Publication accepts a preview operation/token and rejects stale decisions, assessment, input/profile or execution identity. The HTTP publication handler only validates and queues; large reads and hashing run in the worker.

Partial publication continues excluding whole unresolved candidate regions. Expose causal pending pair IDs and counts, and label the action as creating eligible groups. Completeness is record coverage, not a quality metric. Existing stale-generation and Golden protections stay enforced.

## Implementation and verification boundaries

Reuse workflow operations, artifacts, run locks and ownership fencing. Add only an additive nullable pair-review reason migration. Store group approvals as operations rather than another table. Index candidate/proposal lookups once in manual clustering and batch publication rows. Do not change matching thresholds or automatic eligibility before the user reviews benchmark evidence.

Benchmark files are read-only. Verify their manifests; use only raw/input_p119 observations as matcher input. Truth and scenario oracles are offline evaluation data. Synthetic PRIOR/L0 data cannot establish production automatic-merge thresholds. TC6/1M's reported checksum discrepancy must remain visible.

Never create a Docker Compose file. The existing docker-compose.yml is the only Compose configuration. No live DB mutation, container rebuild or secret exposure is required for local verification.
