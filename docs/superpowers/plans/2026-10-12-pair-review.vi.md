# Pair review repair implementation plan

> Execute inline using superpowers:executing-plans. User requested implementation and confirmed the three business rules in this conversation; retain the current dirty checkout and existing Compose.

**Goal:** Understandable Group/Subgroup review, ten pairs per page, accurate global statistics, and working Accept/Deny for selected pairs, whole run, Group and Subgroup.

**Architecture:** Extend the existing workflow readers and commands; keep the engine, authentication, revision fences, queue and TanStack cache. Add an explicit `deny` command for durable cannot-links without rewriting historical REJECTED decisions. Bulk defaults to manual eligibility, processes only UNDECIDED/DEFERRED, uses bounded durable batches and reports applied/skipped/blocked. Preview reads verify revisions and scope before confirmation. Accept is reviewed intent; final clustering still enforces guards.

**Tech stack:** Existing FastAPI/SQLAlchemy/PostgreSQL, Next/React/CSS modules, TanStack Query, pytest/Vitest/Playwright.

**Approved decisions:** Deny prevents two records sharing a cluster through indirect links; Accept uses manual eligibility and preserves hard constraints; bulk preserves all existing accepted/rejected/denied decisions. Scope includes unseen pages. No scoring/Group D threshold changes. Memory UI/cache behavior from the previous task remains.

## Evidence

Source and live run `3449137c-8f4d-41cc-9152-fa12768aa355` agree: 79,364 pairs, no AUTO-eligible pair; bulk Accept completed with 0 applied / 79,364 blocked. Group A34, D418, NULL78,912. NULL includes 52,282 blocked and 26,630 insufficient; do not fabricate grouping. B represents normalization, not a primary decision cohort. One accepted insufficient pair is currently filtered out at cluster construction; manual acceptance must not silently disappear. REJECTED is a workflow execution veto (including transitive closure in that snapshot), but is not a durable ResolutionNegativeEdge. Older redesign documents describe another candidate/runtime; current source governs implementation.

## Tasks

- [x] Backend RED/GREEN: ten-pair reader with source records, NULL/PENDING filters, summary and preview; tests for actual scopes and counts, owner/permission/revision rejection.
- [x] Backend RED/GREEN: manual bulk vs auto-only, explicit Deny persistence/replay/transitive enforcement, preservation of prior decisions, bounded progress/retry/fencing; accepted insufficient pairs reach guarded clustering.
- [x] Frontend RED/GREEN: Vietnamese Group/Subgroup descriptions and actual counts, progress/statistics, ten-pair pagination, side-by-side source comparison, explicit scope selector/preview/confirmation, retained cache and state, meaningful disabled reasons/results.
- [x] Regression checks: full frontend, TypeScript, relevant and full backend, independent read-only code review; fix reproduced issues.
- [x] Build/update only services in existing `docker-compose.yml`; verify live TC4 read-only and synthetic fixture mutations. No additional benchmark TC or paid AI request.
- [x] Report actual policy, deployment and verification evidence.

## Review focus

Scope change while preview open; stale revision or double click; selected IDs outside run; Deny replay/duplicate negative edges; old resolved decisions; partial bulk retry after committed batch; lease loss before commit; null groups and no-result subgroup; vertical overflow/mobile/dialog focus; no hidden change to grouping or baseline score.
