# Stage 2 implementation plan

**Goal:** Reduce clicks and repeated record reading while retaining quality diagnostics.

**Spec:** docs/superpowers/specs/2026-10-13-review-effort-design.md

**Execution:** Inline, following the user's request to continue implementation; no repeated authorization ceremony. Preserve all prior dirty changes; changes stay uncommitted.

## Constraints and review focus

- Existing Compose only; local PostgreSQL and synthetic browser accounts.
- Pair-ID scoped batches only; existing mutation revision/token/audit checks.
- Complete graph is required for batch suggestion; cannot-link, soft conflict/reason, explicit defer, historical unknowns and oversized/truncated regions need coverage.
- Default queue changes must preserve stored filters and group navigation.
- Truth labels never affect task construction. Report sampled frequency context and candidate-level duplicate omissions.

## Tasks

- [x] Add failing backend tests for bounded cluster reads, complete versus incomplete graph, weak bridge, negative/blocked pairs, oversized region, effective assessment precedence, cross-run isolation and actual exact-ID bulk processing.
- [x] Add read-only cluster endpoint and typed client contract; bound queries and source hydration. No new migration.
- [x] Add failing frontend tests for REQUIRED default, preserved filters, concise evidence and cluster batch scope/staleness; implement cluster dialog through existing batch confirmation and queue.
- [x] Extend benchmark diagnostics for known duplicates outside required review, with truth read only after computation and candid limits.
- [x] Run backend/frontend checks, existing Docker builds and real browser comparison of individual/cluster review; report clicks, exposures and scripted duration, verify final clusters agree.
- [x] Obtain final independent review, fix material findings, and record a concrete stage-2 report.
