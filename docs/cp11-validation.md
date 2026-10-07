# CP11 validation — system acceptance

Date: 2026-10-07  
Baseline commit: `43d7dfc` (the working tree contains the implementation under review)

## Verified

- Playwright browser smoke in installed Microsoft Edge: **3 passed**. The landing page toggles between light/dark and persists the choice across reload; the login form fits a 390 px viewport; upload and session-history actions open slide-out drawers, and their expand control fills the viewport. The workspace drawer test stubs API responses, so it verifies browser behavior rather than a live authenticated workflow.
- Frontend Vitest: **37 tests passed across 8 files**.
- Frontend TypeScript check (`npm run lint`): passed.
- Next.js production build: passed, including TypeScript and static page generation. The build needed to run outside the sandbox because worker creation was blocked with `spawn EPERM`; the generated `next-env.d.ts` change was restored afterward.
- Backend focused session/upload/R2/audit/RBAC/auth suite: **42 passed**, one Starlette deprecation warning.
- Backend Ruff for changed API, upload, resolution, authorization and test files: passed.
- `git diff --check`: passed.
- Live R2 smoke: configuration was enabled; a uniquely named small object was uploaded, downloaded and byte-compared, then that exact object was deleted. No key or secret value was printed.
- The multi-file integration test validates three CSV files in one run, one combined export, source-record coverage, R2 snapshot persistence, clearing DB CSV bytes, and compare from the R2-backed snapshot.

## Full backend suite

The full run completed with **283 passed and 10 failed**. I reran those same 10 tests against a clean archive of commit `43d7dfc`; **all 10 fail there too**, confirming they predate the current uncommitted implementation. They are outside the current upload/session/UI changes:

- Four LLM config tests expect settings fields that are absent from the current settings model; one LLM audit test imports a missing `LLMRequest` model.
- Two document-pipeline tests fail on existing scoring/fixture behavior (a `REVIEW` result where the test expects `MATCH_CANDIDATE`, and a selective-unmerge fixture missing a record `id`).
- Two address regression tests intersect `list` values with `&`.
- One same-source matching test expects `MATCH_CANDIDATE` but receives `REVIEW`.

The relevant session/upload/R2/audit/auth tests pass when run outside the sandbox. The full backend suite is therefore not green yet.

## Still required for acceptance

- Authenticated browser flow through real login, multi-CSV upload/commit, review, and export is still not covered by Playwright.
- Visual comparison initially was not run. The later reference-layout pass below
  covers upload states, session panels, overview, header and login; full review and
  export visual acceptance remains outstanding.
- The CP00 representative-data replay and CSV semantic comparison were not run because no approved baseline run/dataset artifact is in this checkout.
- No multi-million-row throughput or memory benchmark was run. The R2 smoke used only a small verification object and does not establish large-file performance.
- Retention/purge duration, OIDC provider and password-recovery delivery still need their service contracts before those flows can be enabled.

CP11 remains **partially verified**, not accepted as a full production rollout gate.

## Authenticated runs-list incident and repair (2026-10-07)

The user reported HTTP 500 on `GET /api/v1/mdm/runs?limit=25&offset=0&archived=active`.
The earlier health check and unauthenticated 401 check had not exercised this database query.
The 10 baseline test failures described above did not explain this runtime error.

Inspection of the configured PostgreSQL database found revision `0018_upload_sessions`
already stamped, but `mdm_resolution_runs.upload_session_id` and
`mdm_resolution_upload_files.replaces_file_id` were missing. Querying the run model
raised PostgreSQL `UndefinedColumn` (SQLSTATE `42703`). Updating an already stamped
migration file does not update databases that previously applied it.

Added forward migration `0019_upload_links` to reconcile both early and complete
0018 schemas. It adds missing nullable columns, foreign keys and uniqueness constraints;
it preserves existing links when present. Also fixed percent escaping in Alembic URL
configuration, exposed by the startup-upgrade regression test on Windows.

Verification:

- 12 migration, Sessions Hub and upload tests passed. New migration regression cases
  cover an early 0018 schema missing both links and a complete 0018 schema with existing
  linked data; both retain their records and support the runs-list query after upgrade.
- Ruff for the changed migration/config/test files and `git diff --check` passed.
- The running backend automatically applied 0019 during reload. Live schema inspection
  confirmed both columns, both foreign keys and both unique constraints; the affected
  ORM queries succeeded.
- Authenticated requests to the exact failing URL, the same list with `archived=all`,
  and `GET /api/v1/mdm/upload-sessions` all returned **HTTP 200** with JSON item lists.
  Session credentials and response contents are not recorded in this report.

This resolves the reported runtime 500; it does not replace the remaining browser E2E
and large-data acceptance checks above.

## R2 upload commit incident and repair (2026-10-07)

The reported draft was READY with one CSV containing 4,213 rows, but POST
`/api/v1/mdm/upload-sessions/{session_id}/commit` returned HTTP 500. Its database
schema was current and importing the staged input without the R2 source upload
succeeded in a rolled-back transaction.

Reproduced the failure using the installed boto3/s3transfer implementation with
botocore Stubber for HTTP responses: a small-file upload closes the caller's CSV
stream. The importer then calls `seek(0)` on that closed file and rolls back with
`ValueError: seek of closed file`. Earlier mocked storage tests did not exercise
this SDK behavior.

The R2 adapter now gives the SDK a borrowed stream wrapper. Reads and seeks still
use the original stream without copying the whole file into memory, while SDK
cleanup leaves closing the original file to its owner. No schema, role or API
contract changes were needed.

Verification:

- The new commit regression failed with `seek of closed file` before the fix and
  passed afterward, including retry returning the same run and a saved R2 key.
- All 9 targeted upload/object-store/registry tests passed. Adapter tests exercise
  caller ownership after both successful uploads and S3 errors using the real SDK.
- Ruff on changed Python files and `git diff --check` passed.
- The affected draft was committed through the authenticated running API: HTTP
  **201**, one file, **4,213 rows**. Repeating commit returned **201** with the same
  run ID. GET draft returned **200** with status **COMMITTED**.
- Read-only database inspection confirmed 4,213 persisted records and the saved
  source key; an R2 HEAD request confirmed that source object exists.
- The recovered run is **UPLOADED**; pipeline start, resolution and export were
  not invoked during this incident verification. The remaining browser E2E and
  large-data acceptance gaps above still apply.

## Upload interaction simplification (2026-10-07)

Selecting or dropping CSV files now automatically creates the backing upload
session, transfers the files and validates the completed input. The UI calls this
"Dữ liệu đang chuẩn bị" and removes the separate "Tạo draft" action. The user
explicitly clicks "Bắt đầu phân giải" only after validation succeeds. Existing
resume, replacement, deletion, naming and backend commit/start contracts remain.
Upload, validation and start have distinct activity labels; failed requests
release the controls so the user can retry.

Verification: TypeScript and all 40 frontend unit tests passed, including automatic
multi-file drop into one upload session, validation gating, rejecting unsupported
or excessive selections, upload resumption and commit retry. The Playwright drawer
test passed with mocked APIs; its updated screenshot was inspected. No backend
changes or live pipeline runs were needed for this UI change.

## HTML-reference layout correction (2026-10-07)

Read and rendered all five supplied HTML references (header, login, upload,
replacement/errors, and workspace/session hub). The previous upload redesign
still had oversized headings, nested cards and an oversized left-only drawer.

- Upload now uses a compact form, source/file section labels, file icons, separate
  transfer/ready/error states, record-count and validation chips, and a fixed
  primary-action footer. CSV selection still uploads and validates automatically.
  Editing the name is saved before commit; replacement preserves the typed name.
- Upload opens from the left and history from the right. Both panels have inset
  rounded frames, expand to the viewport, and keep scrolling content separate
  from the bottom action. History uses search, status chips, date groups, quick
  open, and a two-column list/preview when expanded.
- Header uses separate brand/session and account groups. Overview uses four real
  counts followed by three compact evidence/source cards. Login spacing and the
  Mo illustration follow the reference composition. Both themes use existing
  shared tokens; backend models, roles and pipeline remain unchanged.
- Mock-only reference features were adapted to supported contracts: CSV input,
  actual run statuses, actual queue/source counts, existing archive/restore and
  export workflows. No fake XLSX support, progress percentages, duplicate-upload
  detector, quality scores, notifications or source integrations were added.

Verification: 40 frontend tests and TypeScript passed. Five Playwright tests
passed; API-mocked scenarios cover valid CSV, invalid replacement, retained name,
disabled start, filters, left/right panels, full-screen preview, both themes and a
390px viewport. Inspected screenshots against rendered references, then corrected
inherited CSS that stretched overview cards and colored disabled buttons as active.
This is UI verification with mocked API responses, not another live pipeline run.

## Upload recovery and large-import incident (2026-10-07)

Closing the upload drawer previously unmounted the intake component. The drawer
now remains mounted while hidden, so switching panels preserves file handles and
ongoing transfer/commit requests. An account-scoped localStorage hint remembers
the upload ID; a fresh page restores server progress, including a committed upload
whose response was lost. The header indicates pending work. Starting a recovered
run checks its current status and does not start an already running run again.
Reloading/closing the browser during an incomplete transfer still requires the
user to select the local file again; matching uploaded chunks are reused.

The reported draft `d8f99cd4-b093-44b7-b4fe-973cf2f65f4b` contains a 189,829,291-byte
CSV with 1,000,000 rows. A controlled importer replay used an outer rollback-only
transaction, existing local assembled input and disabled R2 writes. PostgreSQL
raised SQLSTATE **53100 / DiskFull** during record insertion:

> could not extend file because project size limit (1024 MB) has been exceeded

The transaction was rolled back. Read-only inspection confirmed the upload is
still **READY**, with **zero linked runs**. This is a database capacity limit,
not a file upload limit. The importer now rolls back and returns **507** with an
actionable Vietnamese message instead of an unhandled 500. Uploaded files remain
available for retry. Handling includes failure on the first run insert as well
as later record batches. Other database errors retain their existing behavior.

Verification:

- 43 frontend unit tests and TypeScript passed.
- All 6 Playwright tests passed, including closing/reopening a busy upload,
  switching panels, reloading READY data, losing a commit response, restoring a
  COMMITTED draft, and starting that same run once. Browser APIs are mocked.
- 10 upload/object-store backend tests passed. New fault-injection tests cover
  PostgreSQL storage exhaustion at both insertion stages, no partial run, retained
  READY input, successful retry after storage becomes available and idempotency.
- Ruff on changed Python files and `git diff --check` passed.

The million-row import and resolution remain blocked by actual database capacity;
the error-handling fix does not increase the configured storage allowance. No
roles, schema contracts, paid infrastructure plans or file-size caps were changed.

Standard vacuum of `mdm_resolution_records` completed after rollback. Full
compaction was deferred while another import held a table lock; the waiting
maintenance query was cancelled. Storage provisioning is still required for the
million-row workload. PostgreSQL documents the different space/locking behavior
of these operations in its [VACUUM reference](https://www.postgresql.org/docs/current/sql-vacuum.html).

The local reload worker also stopped responding to `/health`. Restarting the
identified uvicorn child restored **HTTP 200**. A second normal vacuum completed;
8,426 previously committed records remain. Full compaction still encountered a
table lock (bounded to three seconds on a direct database connection), so no
storage-reclamation success is claimed.
