# Stage 4 implementation plan and ledger

Spec: docs/superpowers/specs/2026-10-13-pair-automation-design.md

Execute inline, TDD, final fresh independent reviewer. The user's request to continue similarly and explicit scope confirmation authorize this workflow; no repeated approval handoffs or commits. Use external before snapshot recorded in TEMP/p119-stage4-before-path.txt.

- [x] Certificate/cohort policy and guarded component planner: pure RED/GREEN tests, versioned code/profile/source/expiry/precision/coverage gates and bounded plans.
- [x] Provenance schema: migration 0031, HEAD and regression of migrated existing data.
- [x] Queued shadow/apply/stop: ownership, capability, immutable artifacts, replay and publication fences, cumulative budget, no partial component; API/worker RED/GREEN tests.
- [x] UI: typed endpoints, concise panel/confirmation/stop, automatic provenance, preserve manual queue fences; meaningful tests.
- [x] Verify full suites, TypeScript/build, existing Compose and real browser; report stage-3 certificate denial and remaining quality limitations.
- [x] Independent review, one material fix pass, report and completed ledger.

Review focus: an expired or revoked certificate after enqueue; code/profile/input/reviewer changes; undeclared cohort/source or mixed component; absent edge/rejected pair/deferral; synthetic metadata/counts; budget refund via retries/amendment; stop races; provenance mistaken for human review; migration drift; stale browser apply/reload.

Pre-flight: policy -> planner -> shadow artifact -> apply worker -> pair provenance -> UI. All use the same cohort and immutable snapshot fingerprint; apply recomputes the plan and validates the actual current snapshot. Migration precedes any provenance query. Finalize contract remains ACCEPTED, with a separate provenance field and automatic audit.

Ruling: implement dormant production activation with isolated positive fixtures — all available labels are explored synthetic — runtime stays disabled without administrator-configured valid evidence; otherwise false production certification would violate the user's precision target.
Ruling: use the dirty workspace snapshot, not a clean worktree — prior stages and user changes are not committed — losing that context would evaluate the wrong system.

Evidence: policy RED23 missing behavior ->25 passed including owner/data scope; migration RED HEAD0030 ->2 migrations passed; workflow RED6 missing endpoint ->11 API/worker cases passed including stale/cross-run/tampering/code/input/role/revocation/stop/cumulative budget; UI RED missing component and missing provenance ->5 passed; TypeScript passed. Builder RED3 missing behavior ->3 passed; ownership labels count entire disjoint components, not repeated pair samples.
Ruling: bind certificates to server-owned user ID and configured data scope in addition to sources/profile — source names alone are shared across users — broader transfer would risk uncertified populations. Code/profile/owner changes cannot be pooled by the certificate builder.

Final review: no Critical; two Important reproduced with failing tests and fixed in one pass. Stop now cancels queued/running automation, retires its owner under run lock, unlocks manual commands immediately, and blocks apply retries after stop. Qualification now precedes cap; an eligible certified component after an uncertified prefix is selected and applied. Four review-fix regressions RED ->GREEN; 46 backend checks and26 UI checks passed.
Final review re-grade: repair provenance is material audit correctness, not cosmetic — manual repair clears active automatic provenance, preserves prior provenance in its audit, and the UI displays automatic acceptance only for current ACCEPTED. Tested real repair RED ->GREEN. Assessment text no longer says all acceptance is human; integration text regression RED ->GREEN. No review items deferred.
Ruling: stop is persistent per run and does not roll back completed acceptances — a canceled worker loses its lease/attempt permission, and an operator can amend saved decisions — stop cannot reverse a publication which committed before acquiring the shared run lock.

Scope correction: component cohort also binds record count — a two-member precision study must not grant permission to larger connected components with identical signature/source sets — regression test_two_member_validation_cannot_certify_a_larger_component_of_same_pattern RED showed equal cohort keys, now different. Final full suite/build/browser follows this minimal policy correction.

Final completion evidence: backend1218 passed/2 skipped (exit0), frontend218 passed/31files (exit0), TypeScript passed, existing Compose final build/up exit0; PostgreSQL HEAD0031. Four real browser scenarios passed including worker-offline cancel/manual review before restart. CLI blocked synthetic fixture exit2. Eight prior synthetic summaries rejected; strict matching/candidate/recovery/guards bytes unchanged. Production automation remains off with no certificate/pin/scope configured. Validation/report artifacts finalized; no commit/push/reset/Compose changes.
Finishing: keep existing Nam_Backup checkout and uncommitted work, as prior stages; no integration action required or authorized. Review fixes complete, no deferred findings.
