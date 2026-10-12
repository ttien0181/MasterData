# Pair Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement versioned rule/model/LLM assessments with human decisions and preserved cannot-link.
**Architecture:** Preserve the original DECIDE baseline. Publish immutable assessment artifacts and a materialized effective result per pair; all review and clustering consumers read that result. Reuse Splink and the existing policy-controlled gateway/worker.
**Tech Stack:** Python/FastAPI/SQLAlchemy/Alembic, Splink/DuckDB/PyArrow, Next.js/TanStack Query.
**Spec:** `docs/superpowers/specs/2026-10-12-pair-evaluation-design.vi.md`

## Global Constraints

- No new Compose file; edit/run the existing `docker-compose.yml` only.
- Preserve dirty workspace, historical baselines, decisions, negative edges and Golden history.
- Model/LLM never grants merge permission or creates a decision.
- LLM receives names/addresses and comparison signals, never raw contact/ID/DOB values or gold.
- Benchmark sources max 10k records per selected TC; entity-disjoint splits, no existing test-only labels for fitting.
- Execution: implement in the current authorized workspace; retain the work as reviewable changes, without commits/pushes.

## Review Focus

- String 'false' cannot grant verification; a valid identifier is not necessarily verified.
- A changed assessment between bulk preview and submit cannot silently change the scope.
- Reassessment must preserve previously accepted/denied pairs and transitive cannot-link.
- Provider output cannot invent references or leak sensitive values; stale output is discarded.
- Missing model or incomplete labels cannot produce a fake probability or a passing quality claim.

### Task 1: Verified identity and complete rule taxonomy
**Files:** `pipeline/standardization.py`, `pipeline/matching.py`, new `pipeline/pair_assessment.py`, `tests/test_mdm/test_pair_assessment.py`.
**Interfaces:** `assess_pair(left, right, frequencies, negative_pairs) -> dict`; `assessment_profile(profile) -> dict`.
- [ ] RED: verification metadata survives standardization; unverified ID mismatch is soft in assessment context; verified same-namespace mismatch/cannot-link stays blocked; specific subgroup wins; all results have group/reason.
- [ ] GREEN: strict metadata parsing, assessment-only v2 semantics, deterministic classifier with complete taxonomy; preserve legacy behavior outside the assessment context.
- [ ] Verify: `.venv/Scripts/python.exe -m pytest backend/tests/test_mdm/test_pair_assessment.py backend/tests/test_mdm/test_decision_semantics.py -q`.

### Task 2: Versioned persistence and effective reads
**Files:** `workflow_models.py`, new migration `0029_pair_assessment.py`, new `workflow_assessment_reads.py`, `workflow_review.py`, `workflow_pair_reads.py`, `workflow_service.py`, `workflow_bulk.py`.
**Interfaces:** `effective_result(pair) -> dict`, `effective_column() -> SQL JSON expression`, `active_assessment(db, run_id) -> assessment | None`.
- [ ] RED: baseline fallback, assessed SQL filters/counts, full groups, stable decisions and assessment identity in preview; stale bulk is rejected.
- [ ] GREEN: assessment metadata/history and per-pair materialization; common reads and eligibility, no baseline mutation.
- [ ] Verify: focused assessment integration plus pair review/bulk tests.

### Task 3: Reassessment and cluster contract
**Files:** new `workflow_assessment.py`, `workflow_jobs.py`, `workflow_contracts.py`, `workflow_service.py`, `job_queue.py`, `resolution_worker.py`, `api/mdm_workflow.py`, `workflow_finalize.py`.
**Interfaces:** `start_assessment(db, run_id, request, actor) -> operation`; `run_assessment(operation_id, factory, progress)`; assessment identity included in cluster snapshot.
- [ ] RED: idempotent fenced publish, decisions/history/cannot-link retained, stale operation cannot publish, finalize uses same assessed policy and checks indirect links.
- [ ] GREEN: extend existing queue dispatch and capability checks; publish assessed pairs plus immutable artifact atomically; record accepted-pair conflicts without changing decisions.
- [ ] Verify: workflow reassessment/cluster tests and migration upgrade.

### Task 4: Evaluated soft model
**Files:** new `pipeline/soft_model.py`, new `scripts/train_pair_assessment.py`, new `tests/test_mdm/test_soft_model.py`; model/evaluation artifacts under `artifacts/pair-assessment/`.
**Interfaces:** `load_review_model(path) -> ReviewModel`; `ReviewModel.evaluate(records, pairs) -> bounded results`; optional calibrated probability with explicit synthetic scope.
- [ ] RED: invalid provenance/hash/split/calibration fails closed; absent model yields no probability; model does not alter rule eligibility.
- [ ] GREEN: reuse Splink comparison/inference contracts, fit/train/dev/test separately with bounded authorized datasets; record calibration and frozen routing evidence.
- [ ] Verify: focused tests; run training and frozen-test evaluation, publish measured report with scope and limitations.

### Task 5: Scoped LLM advice
**Files:** `llm/gateway.py`, `llm/policy.py`, `config.py`, new `workflow_pair_advice.py`, new `tests/test_llm/test_pair_assessment.py`.
**Interfaces:** `advice_payload(pair, left, right) -> dict | None`; `generate('PAIR_ASSESSMENT', payload, settings, context, factory)`.
- [ ] RED: opaque refs and no raw contacts/IDs/DOB; eligibility/routing; schema/evidence validation; budget/stale/cache; AI leaves rule results and decisions identical.
- [ ] GREEN: new purpose using existing Gemini/OpenRouter policy/accounting, bounded queued job, three advice enums and grounded reasons; separate advice persistence.
- [ ] Verify: LLM contract/regression tests with mocked transport, then authorized synthetic canary under current limits.

### Task 6: Review UI and cache
**Files:** `frontend/src/lib/workflowContracts.ts`, `workflowApi.ts`, `pairReview.ts`; review workspace/stats/row/cache components and tests.
**Interfaces:** assessment status/identity in summary and pair reads; start reassessment and queue advice endpoints; immutable rule result and separate model/advice display.
- [ ] RED: complete groups, remaining counts, 10 pairs/page, independent rule/model/AI display, no automatic selection/decision, cached group navigation and revision changes.
- [ ] GREEN: add reassessment/AI controls and meaningful states; preserve cached tab/group state and invalidate only affected data.
- [ ] Verify: frontend targeted tests, TypeScript and production build; browser/E2E assessment flow.

### Task 7: Integrated QA and live session
**Files:** bounded verification script/report; existing Compose/env settings only when required.
- [ ] Run backend regressions, migration consistency, frontend tests/build and scoped E2E.
- [ ] Build/restart authorized existing QA services; apply migration to the configured QA DB.
- [ ] Reassess session `3449137c-8f4d-41cc-9152-fa12768aa355`; compare baseline hash, decision/negative-edge counts, assessment grouping and guards.
- [ ] Fresh code review and repair any findings; report actual validation and model/LLM limitations.

## Self-review

All five requested stages and existing-session migration are covered. Rule/model/advice/decision interfaces stay separate. Original baseline fallback, assessment revision fencing and strict model approval preserve compatibility; unsupported labels or provider configuration are reported rather than bypassed.
