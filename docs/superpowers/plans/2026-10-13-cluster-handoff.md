# Cluster handoff implementation plan

> For agentic workers: execute backend tasks inline; independent frontend and offline benchmark work use superpowers:dispatching-parallel-agents. Track checks below.

**Goal:** Make pair decisions and exact-group exceptions consistent, explain proposed groups before publication, and verify safety against supplied benchmarks.

**Architecture:** Reuse immutable workflow operations/artifacts and revision fencing. One shared snapshot/compute path powers async preview and publication. Exact group approvals are fingerprinted receipts; pair reasons are additive storage.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, SQLite tests, Python, Next.js/React, Vitest.

**Spec:** ../specs/2026-10-13-cluster-handoff-design.md

## Global constraints

- Never create a Docker Compose file; use existing docker-compose.yml only.
- Preserve hard constraints, Deny, review vetoes, input/assessment/profile freshness and worker ownership fencing.
- Reasons are nonblank for soft-conflict Accept and group approval, max 500 characters.
- Automatic-merge thresholds require the user's later decision; no automatic rollout.
- Benchmark source files remain unchanged; labels never enter the matcher.

## Review focus

- Reasonless historical soft Accept must not silently gain exception authority.
- A new outside member or changed internal decision must invalidate exact-group approval.
- Stale preview and forged/cross-run preview receipts must not publish.
- Worker retry, cancellation or expired lease must not partially publish.
- First generation progress and partial coverage must give actionable navigation.

## Task 1: Consistent soft review and exact-group approval

Files: workflow_contracts.py, workflow_models.py, workflow_review.py, workflow_bulk.py, workflow_amendments.py, pipeline/cluster_guards.py, new workflow_group_approvals.py, migration 0030, API routes, tests/test_workflow_group_approvals.py.

Interfaces: optional reason in pair/bulk requests; eligibility.reason_required; snapshot.review_reasons; group_approval fingerprint/validation; POST group-approvals returns a completed OperationRead.

- [x] RED: Accept soft conflict with reason produces group, reasonless acceptance fails; hard and Deny still block.
- [x] GREEN: Persist reasons/audit; override soft Group D only on explicit reasoned acceptance.
- [x] RED: missing direct evidence requires exact-group receipt; changed member/decision or hard conflict blocks.
- [x] GREEN: fingerprint receipts, validate connectivity and all constraints, apply only exact approved groups.
- [x] Verify targeted tests and additive migration.

## Task 2: Shared async preview/publication and performance

Files: workflow_finalize.py, new workflow_cluster_preview.py, workflow_jobs.py, workflow_service.py, workflow_contracts.py, API routes, tests/test_workflow_cluster_preview.py and existing workflow tests.

Interfaces: POST cluster-preview -> CLUSTER_PREVIEW operation; bounded result defined in spec; optional preview_operation_id and preview_token on finalize; decision_snapshot(db, run, allow_partial), compute_publication(snapshot, run_id, revision).

- [x] RED: preview changes no decisions/groups; reports actual publication counts and pending causes.
- [x] GREEN: shared immutable snapshot and guarded compute, async preview handler/worker.
- [x] RED: stale/forged preview rejected; finalize handler does not hydrate records; retry snapshot remains immutable.
- [x] GREEN: queue-only finalize, sealed snapshot in worker, preview fencing, cached pair/member indexes and batch insertion.
- [x] Verify full workflow tests, migrations and quality/coverage distinctions.

## Task 3: Frontend review, previews and exception UX

Owner: independent frontend task. Files: frontend contracts/API, review queue, row/reason dialog, completion/cluster-preview dialog, conflicts, cluster workspace and tests.

- [x] RED/GREEN: initial no_merge, reasoned soft Accept and queued/bulk reason persistence.
- [x] RED/GREEN: preview before finalize, stale/error/pending locks, exact group approval with reason.
- [x] Verify frontend full test command, typecheck and build.

## Task 4: Offline benchmark evidence and final review

Owner: independent benchmark task. Files: scripts/verify_cluster_benchmark.py, docs/benchmark/cluster-handoff-20261013*.

- [x] Verify hashes and run declared complete TC6 scenario components with real P119 decoding/standardization.
- [x] Report forbidden co-clusters and positive controls separately from decision replay assumptions.
- [x] Run full backend suite and lint, frontend suite/typecheck/build; review final patch and fix material regressions.
- [x] Present benchmark evidence and ask only the remaining automatic-policy business decision.

## Execution ledger

- Baseline: clean branch Nam_Backup; 62 targeted backend tests and 20 frontend tests passed. Existing Compose found. Frontend npm.cmd required by PowerShell policy; dependencies already installed.
- User explicitly chose reasoned soft Accept, exact-group exception review and full P0–P2; no further implementation authorization needed. Changes stay in the shared IDE checkout for review.

- Final verification: 1083 backend tests passed, 2 skipped; 199 frontend tests passed; TypeScript/build and changed-file Ruff passed. Repository-wide Ruff has 164 findings outside modified files. Independent review corrections proved RED?GREEN and re-reviewed clean.
- User chose experimental synthetic threshold calibration only; public Drive README matched local. Four missing TC6/TC8 CSVs recovered to readonly overlay cache with manifest hashes. Seven thresholds evaluated over all 2100 scenario components/68000 pairs; production threshold remains unset.
- Results and limitations: docs/benchmark/cluster-handoff-20261013-validation.md and docs/benchmark/cluster-calibration-20261013.md. No Compose changes or live database migration/deployment.
