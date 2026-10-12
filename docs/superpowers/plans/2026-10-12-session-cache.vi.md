# Session cache implementation plan

> For agentic workers: use superpowers:executing-plans for inline implementation. User explicitly approved TanStack Query + UI state persistence across the four existing session tabs.

**Goal:** Revisited tabs immediately reuse previously loaded pages and restore UI state while changes on the server remain revision guarded.

**Architecture:** One memory-only QueryClient and UI store per authenticated user in AppSession, shared across routes. Query keys identify run/resource/page/filter and the relevant revision. Workflow status is queried once centrally, polled only during active jobs and revalidated in the background; mutations refresh workflow and affected resources. Unsaved Golden choices retain their original revision across tab remounts.

**Tech stack:** Existing Next.js/React, TanStack Query 5.104.2, Vitest and Playwright.

**Design source:** User-approved design in this conversation: cached reads, independent UI state, targeted updates, 30-minute inactive cache lifetime, memory only, logout/access loss eviction. Scope is the existing four tabs; existing Compose only.

## Tasks

- [x] RED: Add integration tests for returning to the same data page/view without another request, pair filters/pages, Golden draft persistence and stale-revision rejection.
- [x] Implement shared user-scoped query/UI provider and migrate four tabs plus central workflow reads; preserve disabled-read gates and explicit retry behavior.
- [x] Preserve per-tab scroll/details and per-cluster draft/base state. Keep explicit discard behavior when choosing another group. Update existing test wrappers to use the real provider.
- [x] Add cache isolation, changed-revision and authorization loss coverage. Run full frontend suite and TypeScript.
- [x] Perform read-only code review, build production frontend, update only existing frontend service and verify navigation/request counts and save behavior in browser. Reuse the existing TC4 10k session; no further benchmark TC, upload or provider calls needed.
- [x] Document final policy, limits and evidence.

## Decisions

Ruling: Work in the current checkout — the approved change builds on substantial uncommitted workflow code and the user's running stack; a clean worktree would omit required source. No reset, broad formatting, commit or push.

Ruling: Keep existing uncached useWorkflowRead for utilities/legacy callers; use a dedicated cached-read hook for the four tabs so cache keys are explicit.

Ruling: Revisions form cache identity for mutable resources. Previously loaded pages can be retained while changing page within the same resource/filter/revision; data from a different run/filter/revision must not be mistaken for the requested page. Raw data is stable after PREPARE; preparing reads are short-lived and refreshed when PREPARE completes.

Completed: 90 Vitest tests, TypeScript, 9 production-browser tests, existing TC4 10k live browser checks and healthy deployed frontend. See `docs/session-workflow/SESSION_CACHE_20261012.vi.md` for policy and evidence. Existing dependency security issues were patched using Next16.3.8/source-map-js1.2.2; npm audit is clean.
