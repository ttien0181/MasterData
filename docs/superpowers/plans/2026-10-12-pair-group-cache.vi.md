# Pair Group Cache Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline. Implementation is authorized by the user's approval of the preceding proposal.

**Goal:** Switching evidence Groups restores the cached page and reading context without clearing the table; decisions refresh in the background safely.

**Architecture:** Stable, normalized per-filter/page TanStack keys replace global decision revision keys. Cached pages carry their observed revision; revision changes invalidate without removing data. Unknown cross-group effects invalidate all pair pages conservatively, fetching only observed pages. During bulk operations refreshes are limited to once per five seconds, with immediate refresh after completion.

**Tech Stack:** Existing React 19, Next 16, TanStack Query 5, Vitest, Playwright, existing Docker Compose.

**Spec:** Approved proposal in this conversation: separate Group cache/state, bounded prefetch, 10 pairs/page, staleTime 2 minutes, inactive cache 30 minutes, background refresh.

## Global Constraints

- Work in the current feature checkout: ongoing uncommitted implementation must be preserved and deployed in place. No commits or worktree migration.
- Never create a Compose file. Rebuild only frontend in existing project p119-session-qa using docker-compose.yml and .env.qa.
- Keep cannot-link Deny, manual eligibility, and pending/deferred-only bulk policy unchanged.
- Keep clearing selection on filter/Group change. Keep existing server revision checks and auth cache eviction.
- Cache remains in memory, isolated by user and run. No raw records in persistent browser storage.

## Review Focus

- Returning to a previously visited Group restores page/filter/search/scroll without another fresh read.
- Uncached or failed Group reads never show rows from another Group.
- Revision changes during an in-flight read, bulk progress, or a tab being unmounted do not lose updates or allow writes on old rows.
- Filtered pending totals shrink; pagination clamps to a valid page without stale filter counts being mistaken for current totals.
- Prefetch remains bounded, deduplicated, abortable on permission loss, and compatible with normal pagination.

### Task 1: Cache and per-Group reading context

**Files:** Create frontend/src/components/PairGroupCache.test.tsx, frontend/src/components/usePairReviewCache.ts; modify DecisionWorkspace.tsx and useTabView.ts/test.

- [x] Write failing integration tests: A page 3 -> D -> A restores page/search/Subgroup; same query is fetched once; group isolation while loading/failing.
- [x] Verify RED with portable Node and Vitest.
- [x] Implement normalized offset-only keys, per-Group UI state and scoped scroll/detail restoration. Keep 10 pairs/page.
- [x] Add revision metadata, conservative lazy invalidation, five-second bulk refresh throttle and immediate completion refresh. Disable writes on placeholder/stale/error pages.
- [x] Verify GREEN and existing pair-review/cache tests; migrate mocks from cursor pagination to the existing offset API where required.

### Task 2: Bounded prefetch and visual refresh

**Files:** usePairReviewCache.ts, PairReviewStats.tsx, DecisionWorkspace.tsx/module.css; PairGroupCache.test.tsx.

- [x] Write failing tests for first-page/next-page prefetch, no zero-count Group prefetch, and visible cached rows during revision refresh/completion.
- [x] Implement serial prefetch (up to three other nonempty Groups plus one next page) after primary reads, and Group hover/focus prefetch. Do not prefetch during active operations.
- [x] Add fixed loading rows for uncached reads, small background update status, and explicit refresh control.
- [x] Verify GREEN, full frontend suite and TypeScript.

### Task 3: Production validation

**Files:** frontend/e2e/pair-group-cache.spec.ts; docs/session-workflow/PAIR_GROUP_CACHE_20261012.vi.md.

- [x] Add browser coverage for real query/UI stores, cached Group return, scoped scroll, delayed response isolation and revision background refresh.
- [x] Build and recreate only frontend with the existing Compose file; verify health.
- [x] Run new browser tests and existing four-tab/workflow tests on the deployed frontend.
- [x] Review the changed runtime files independently and resolve material findings with regression tests. Record evidence and implementation decisions.
