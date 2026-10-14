# Pair classification correctness — stage 1

The user authorized implementation of the correctness stage on the current workspace, with Docker/browser validation and the supplied public Google Drive benchmark. Preserve all existing uncommitted work. Never create another Compose file.

## Behavioral contract

- Assessment must preserve `INSUFFICIENT_EVIDENCE`, its reason and triage. Field contradictions remain evidence but cannot manufacture mandatory review after the evidence floor failed. Such pairs belong to C, not the actionable D cohort.
- An undecided insufficient pair is retained, manually inspectable and mergeable subject to existing constraints, but does not require human review or block publication. An explicit human DEFERRED decision remains pending, including on an insufficient pair.
- Mandatory review is identical in Python, SQL filters, summaries, workflow blockers, preview and publication. Accepted/rejected/denied pairs remain resolved. Manual-only MERGEABLE and REVIEW remain mandatory. Unknown historical conclusions remain conservatively pending.
- Insufficient edges must not spread a pending/conflict region to unrelated records. Explicit acceptance or explicit deferral still participates in the affected region. Shared region traversal must serve preview and partial publication.
- Existing assessment history, baseline, human decisions, cannot-links and hard constraints are never silently rewritten. New assessments get a new version; existing sessions can explicitly reassess.
- UI distinguishes retained insufficient pairs from mandatory review, without calling them proven non-matches. Add a bounded filter and summary count for insufficient pairs.
- Verify the existing migration 0030 against a migrated database, including the actual `review_reason` column. No deployment/production DB mutation is authorized by local tests.
- This stage does not lower merge thresholds, enable auto-merge, train a production model or change fuzzy/contact frequency policies.

## Validation

Meaningful regression tests cover floor-failed conflicts, explicit deferral, SQL/Python equivalence, historical assessment fallback, publication singletons, insufficient bridges, retained accepted overrides, schema migration, UI filtering and displayed counts. Run backend/frontend suites, then local PostgreSQL/Docker and browser smoke checks. Benchmark raw inputs never receive truth columns; truth is used only after computation. Report sampled bundles and incomplete coverage honestly.
