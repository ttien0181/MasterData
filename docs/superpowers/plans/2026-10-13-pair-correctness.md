# Pair Correctness Implementation Plan

> **For agentic workers:** Execute inline using superpowers:executing-plans and test-driven-development, with a fresh final reviewer.

**Goal:** Preserve insufficient evidence and make mandatory review consistent across assessment, API, UI and publication.

**Architecture:** Keep descriptive evidence separate from mandatory review; use the shared workflow review contract and a shared publication-region traversal. Preserve historical results and reviewer decisions.

**Tech Stack:** Python/SQLAlchemy/Alembic/FastAPI, React/Next.js/Vitest/Playwright, PostgreSQL using existing docker-compose.yml.

**Spec:** docs/superpowers/specs/2026-10-13-pair-correctness-design.md

## Global Constraints

- Work on current workspace; preserve the pre-existing dirty changes using the external before snapshot.
- Never create a Compose file. Test with a local database, never the external DB from .env.qa.
- No history rewrite, no threshold changes, no production model training or auto-merge enablement.
- User already requested execution; proceed through all tasks without repeated approval requests.
- Leave changes reviewable and uncommitted to avoid mixing the user's pre-existing changes into commits.

## Review Focus

- Explicit DEFERRED on insufficient evidence must remain mandatory and retain its affected endpoints.
- Accepted insufficient pairs still require current hard-constraint checks.
- A weak edge must not hold an otherwise safe disconnected component in partial publication.
- Null/unknown historical conclusions remain pending and baseline/assessment precedence is consistent.
- Existing assessed sessions retain history; new assessment code has a different version identity.

## Task 1: Assessment and schema

Files: pipeline/pair_assessment.py, tests/test_pair_classification_regressions.py, tests/test_workflow_migrations.py; existing migrations/versions/0030_review_reasons.py.

- [x] Write and run regression for no contact + conflicting name: decision/triage INSUFFICIENT_EVIDENCE, group C, NO_VALID_IDENTITY_ANCHOR reason, unchanged input.
- [x] Preserve floor failures before actionable grouping; bump assessment version. Keep evidence and constraints intact.
- [x] Verify schema migrations add review_reason, retain historical rows and match HEAD.

## Task 2: One mandatory-review contract

Files: workflow_review_rules.py, workflow_pair_reads.py, tests/test_workflow_review_routing.py.

- [x] Write failing decision matrix and real SQL/API tests: insufficient UNDECIDED is not required; explicit DEFERRED is required; review/manual-only mergeable and unknown remain required; terminal reviewer decisions resolve.
- [x] Update review_required(decision, baseline) and pending_predicate() consistently; add insufficient count and INSUFFICIENT filter using effective assessment precedence.
- [x] Assert pair_summary, workflow blockers and filtered pair pages agree.

## Task 3: Publication boundaries

Files: workflow_partial.py, workflow_cluster_preview.py, tests/test_workflow_partial_publication.py, tests/test_workflow_cluster_preview.py.

- [x] Reproduce insufficient bridge holding a safe component, and all-insufficient publication without pending records.
- [x] Share candidate adjacency: include merge/review and accepted/deferred overrides; exclude unresolved insufficient and negative/blocked edges.
- [x] Verify previews and actual publication agree, hard constraints still hold, and accepted/explicitly deferred insufficient edges remain effective.

## Task 4: UI and data verification

Files: frontend/src/lib/workflowContracts.ts, components/DecisionWorkspace.tsx, PairReviewRow.tsx, their tests; scripts/verify_pair_correctness.py.

- [x] Write failing UI checks for insufficient summary/filter and automatic temporary separation wording.
- [x] Display API-authoritative mandatory counts and retained insufficient counts, no client-side reinterpretation.
- [x] Fetch supplied public benchmark inputs/truth to isolated cache, verify manifests where available, run before/after with identical raw-only candidate universe.
- [x] Use existing Compose with local PostgreSQL; migration, API/worker, browser round trip and full test suites.
- [x] Record evidence/results and obtain a fresh final code review; fix important findings and rerun relevant checks.
