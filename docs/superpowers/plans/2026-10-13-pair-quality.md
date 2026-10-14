# Stage 3 pair quality implementation plan

> Execute inline with superpowers:executing-plans and test-driven-development; one final independent reviewer. The user explicitly requested continuation of staged implementation.

**Goal:** Recover supported duplicate candidates for human review and measure retrieval, review quality and guarded shadow automation.

**Architecture:** Separate review-only support from the strict matching floor; independently recheck accepted support in publication. Add bounded blocking keys and raw-only ownership evaluation. No automatic permission comes from benchmark results.

**Tech:** Python/SQLAlchemy/FastAPI, React/TypeScript, existing PostgreSQL Compose, Playwright, PyArrow.

**Spec:** docs/superpowers/specs/2026-10-13-pair-quality-design.md

## Constraints / review focus

- Preserve dirty work through the external stage-3 snapshot, keep changes uncommitted, never create Compose files.
- Synthetic/explored labels never certify production automation; default precision target 99.5% is a readiness target, not a score cutoff.
- Do not lose historical conclusions/decisions or bypass verified conflicts, negative links, source/size guards.
- Malformed/partial/placeholder data, same-name relatives, generic addresses, duplicated/shared/historical contacts and missing bridges require regressions.
- Candidate and support logic must be symmetric/bounded; assess code identity includes every new decision dependency.

## Tasks

- [x] Support policy: failing tests in backend/tests/test_mdm/test_review_recovery.py; implement pipeline/review_recovery.py and pair_assessment.py v4; include module in workflow_assessment.py identity and preserve legacy evidence.
- [x] Blocking coverage: failing candidate tests, implement two bounded composites in pipeline/candidate_engine.py, including a strict numbered-street transform. Keep provenance and measure the enlarged candidate universe.
- [x] Publication/review: failing tests for explicit accepted support, forged marker, unknown bridge, hard conflicts and reason; independently validate support in pipeline/cluster_guards.py. Adapt intentional stage-1 expectations to keep unsupported floor failures insufficient.
- [x] UI: tests for review-support explanations and subgroup labels; update PairBaseline/pairEvidenceSummary/pairReview; preserve existing batch fences and full evidence.
- [x] Evaluation: meaningful tests for all-positive denominator, cross-owner cluster false merges, label coverage and confidence/synthetic gate; create scripts/verify_pair_quality.py using the existing raw loader and genuine guarded shadow route.
- [x] Compare before/after, run focused/full suites and local Docker/browser recovery and denial round trip. Report gains, new workload/false-review counts and remaining misses honestly.
- [x] Independent review, one fix pass with regression checks, final report and ledger.

## Ledger / rulings

- Ruling: Use the current workspace and snapshot rather than a clean worktree â€” prior stages and user edits are not committed â€” a clean branch would omit the system being evaluated.
- Ruling: Treat inspected benchmark cases as exploratory validation â€” prior stages and this probe read their labels â€” no untouched-holdout certification is claimed.
- Ruling: Keep production auto-merge disabled â€” connected-component risk and representative independent labels are not certified by synthetic pair accuracy â€” the readiness/shadow report is concrete and reviewable without introducing unverified merge permissions.

- Ruling: Unsupported stage-1 regression fixtures now use partial/missing DOB and generic address ? full-name/full-DOB or complete-DOB/numbered-street combinations intentionally qualify for manual review in stage 3 ? weak trigram/conflict-only cases remain insufficient.
- Task evidence: support RED 5 failures -> 18 passing; candidate RED 2 failures -> 37 passing with existing candidate tests; publication RED 3 failures -> 73 passing with related routing/classification checks; UI RED 2 failures -> 8 passing with cluster dialog; quality metrics RED 6 failures -> 6 passing, followed by malformed-label/membership regressions.

- Ruling: Do not recover missing-anchor pairs with conflicting valid unverified identifiers ? matching name/DOB alone does not resolve that contradiction ? anchored D pairs keep the existing manual reason policy.
- Ruling: Warm old-assessment groups explicitly in the cache regression ? prefetch may legitimately complete before the assessment change on a loaded machine ? still verify one read per assessment and cache reuse within the new assessment.

- Review fixes: restrict unverified ID support to VN CCCD/CMND with namespace or explicit custom-domain identity uniqueness on both sides (4 failing regressions -> passing); different explicit namespaces no longer veto independent name/DOB support (failing regression -> passing), while historical anchored grouping/reason remains conservative.
- Evaluation fixes: TC5 contains 898 blank raw entity types, which runtime treats as UNKNOWN; align metric scope with that fallback and validate full proposal membership before excluding whole organizational components. Mixed-scope components still fail. Both scope regressions passed after RED.
- Reproducibility: deterministic source file hashes/digest, evaluator hash, explicit assessment profile and cluster policy accompany every benchmark case; test detects a changed candidate limit while ignoring checkout path.
- Coverage after final review: email domain, historical/end-dated/origin contact rejection and secondary-contact order/symmetry regressions; 47 focused recovery/publication/quality tests passed.
- Verification ruling: unchanged Windows manifest replacement failed transiently under the first heavily loaded full run; isolated exact-case rerun passed and the next full run passed 1,159 tests/2 skipped. No storage code changed; final full run follows the review fixes.

- Final evidence: backend1173 passed/2 skipped, frontend211 passed/30files, TypeScript passed, existing Compose build and four real browser checks passed; all8 before/after cases successful with equal raw/source hashes and identical strict shadow outputs. Additional2199 true/27 false review tasks; TC5 gain1999 true/5 false. All automation gates false. Report: docs/benchmark/pair-quality-stage3-20261013.md; validation JSON alongside.
