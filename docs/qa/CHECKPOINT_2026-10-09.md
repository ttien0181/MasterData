# MasterOne full-system QA checkpoint — 2026-10-09

> **Superseded historical checkpoint.** Work resumed on 2026-10-09 and the scoped audit assessment/report handover is now complete. Current decision: **NOT READY**; unresolved items remain. Read the completion section at the end and `PRODUCTION_READINESS.md` before continuing. Earlier PAUSED, missing-report and 2-request statements describe the original pause only. **All 4/4 authorized generation requests are now consumed; no more paid requests are authorized.**


**PAUSED at the user's explicit request because their quota is nearly exhausted.**
Resume this work from this file, the working tree and the artifacts; do not restart discovery or repeat paid canaries. This is a checkpoint, not the final audit report. The task is incomplete. Current readiness assessment: **NOT READY**, pending correctness/readiness work described below.

## Authorization and safety boundary

- Repository: `C:\VinUni\Dự án nhóm\P-119`; Windows PowerShell, Docker Desktop. User authorizes ordinary technical fixes and isolated tests without repeated permission requests.
- Only use the five **10k** datasets under `C:\VinUni\Dự án nhóm\GenVN\benchmarks-v1\TC*\10k\seed-7`. Do not run 21k/100k/1M just because the original task template lists them.
- Production data must not be reset/deleted/migrated destructively; do not deploy to production. Preserve all original uncommitted/untracked work. No commits have been made by this audit.
- Cloud credentials are in ignored `.env.qa`, supplied by the user for dedicated QA Neon/R2. Never print them, include them in reports, inspect container environment dumps, or ask for secrets in chat.
- Canary budget is **USD 0.10 total for at most 4 synthetic generation requests, no retries**. **2 generation requests already executed**, both passed. At most 2 remain under the SAME total budget. Do not reset accounting or run bulk LLM benchmarks. Actual billing is not fully reconciled; do not claim a precise paid total.
- Three additional Gemini **models-list authentication GETs**, one per key, passed and generated no content. Numbered keys are not a new budget.
- No subagents were spawned. Applicable developer instruction prohibits spawning unless explicitly asked. Read `frontend/AGENTS.md` before frontend work. Installed Next output docs were read before Docker/frontend configuration changes.

## Git/workspace state

- Original branch: `NamNT`; original HEAD: `b45142a` (`test(merge): validate migration shadows and document production blockers`).
- Current branch: **`qa/full-system-20261009`**, created in the existing checkout to retain dependencies and user artifacts. No staging/commits.
- Original tracked tree was clean, with many user untracked paths. Preserve `benchmark/`, `.npm-cache/`, `gemini_3_5_flash_lite/`, `eval/results/*`, `artifacts/cp1-audit-20261009/`, `artifacts/normalization-wip*`, `scripts/audit_cp1_candidates.py`, and untracked `data/*improvement_progress.py`; they are not all audit changes.
- `git diff --check` passed at checkpoint (only a Git LF/CRLF warning).
- Audit artifacts: ignored `artifacts/full-system-20261009/` plus existing baseline conventions. Machine checkpoint snapshot: `artifacts/full-system-20261009/checkpoint-state.json`.
- The required six final reports are **not yet written**: `FULL_SYSTEM_AUDIT.md`, `TEST_MATRIX.md`, `PERFORMANCE_BENCHMARK.md`, `INFRA_VALIDATION.md`, `PRODUCTION_READINESS.md`, `RUNBOOK.md`. Write/update them after finishing necessary execution; do not present this checkpoint as final handover.

## Actual architecture discovered

- FastAPI package: `backend/src/masterone`; Next frontend: `frontend/app` and `frontend/src/components`, Next 16.3.6 / React 19. Local Python 3.11.9 `.venv`, Node 24.21.0.
- PostgreSQL runtime is selected by `MDM_DATABASE_PROFILE` (`local` or `neon`) and actual `MDM_DATABASE_URL[_LOCAL]`, not all example variables in the user template. SQLite is primarily used for unit-test isolation.
- Independent PostgreSQL-backed worker: `python -m masterone.mdm.resolution_worker`. Queue claims, leases, ownership fencing, retries; Redis is not required by this pipeline.
- Main mounts auth, mdm_runs, mdm_uploads, mdm_resolution and generic routes. **`large_imports` and `mdm_explanations` routers are not mounted.** The newer OpenRouter LLM subsystem has tests/code but is not fully wired into main/UI. Legacy direct Gemini semantic/explanation functions are the active integrations.
- Input contract `p119_input_v1`: requires source ID, source, name; supports CRM/ERP/BILL/CSV. Runtime upload sessions accept up to 10 CSVs with 1000-row parse batches. README limits and embedded-worker/SQLite descriptions are stale.
- At **<=10000** records, ORM snapshot goes through `_compute`; at **>10000**, service uses seven-stage DuckDB/Parquet `StageRunner` and publishes through strict `EngineResultAdapter`.
- Candidate union is global across ingestion chunks, with exact/composite/gram blocking and default DOB/province-partitioned TF-IDF. Optional global sparse TF-IDF is implemented but opt-in; similarity work can still approach all-pairs. Full indexes/candidate union and legacy clustering remain in memory.
- Rule score is not a calibrated probability. Hard constraints, guards, review, golden provenance, preview/execute idempotency, CSV export, audit and split/unmerge paths exist. Legacy/v2 policy behavior differs; do not silently enable v2 in production.
- Read README, ARCHITECTURE, env examples, compose/Dockerfile, pyproject, frontend package, docs/merge STATUS/PROJECT_MAP/AGENT_RULES/CONTRACTS/BASELINE/FINAL_REPORT/WORKERS, large-file docs and relevant runtime/tests. More targeted code review remains; do not claim every source line was audited.

## Running isolated Docker environment

Command prefix:

```powershell
docker compose --env-file .env.qa.local -p masterone-qa -f docker-compose.qa.yml
```

Services at pause:

| Service | Access | Verified state |
|---|---|---|
| PostgreSQL 16 | `127.0.0.1:15432`, DB `masterone_qa` | Healthy; schema head `0025_pipeline_checkpoints` |
| FastAPI | `http://127.0.0.1:18001` | Healthy; dependency `/ready` tested |
| Next production frontend | `http://127.0.0.1:13000` | Healthy; actual browser login/upload/export tested |
| Independent worker | no public port | Running; actually processed local jobs; no healthcheck yet |
| Moto S3 emulator 5.2.3 | `http://127.0.0.1:19000` | Running; multipart/checksum/range/concurrency tested; no healthcheck |

Dedicated volumes: `masterone-qa_qa_postgres`, `masterone-qa_qa_runtime`. Leave them intact. The unrelated `onemaster-postgres` at 5432 and `onemaster-redis` at 6379 were not touched.

`.env.qa.local` contains a generated random auth secret and is ignored. QA compose does not load developer `.env` or `.env.qa`; it explicitly disables all LLM/tracing and blanks keys, including numbered Gemini keys. R2 emulator server endpoint is `http://storage:9000`; browser signing endpoint is `http://127.0.0.1:19000`. Frontend compiled backend URL uses 127.0.0.1, keeping browser cookie sites consistent.

Moto is **volatile in-memory storage**, not a durability certificate. Before replacing 5.1.12 with 5.2.3, 17 owned QA objects were backed up to `artifacts/full-system-20261009/local-s3-backup.zip` and restored. Newer objects created since that backup are NOT in that archive. Before any emulator recreation, make a fresh backup of the dedicated bucket. Never use `down -v` for routine pause/resume.

**Important image/source caveat:** images were rebuilt again near the end (`docker-cached-build.log`, build exit 0, ~147s), but running containers were not recreated after that later build. Additional source changes (review lookup optimization and explicit migration-target fix) may also postdate the image's source COPY. Before final regression/runtime validation, finish/inspect running jobs, rebuild current source and replace only backend/worker/migrate as needed; preserve emulator state and DB volumes. Do not assume host source equals installed container code.

## Cloud credentials/current state

- `.env.qa` now has valid Neon QA configuration and R2 keys/bucket/endpoint. The user initially pasted an R2 endpoint with an appended bucket path. The audit verified that the path exactly matched `R2_BUCKET` and safely normalized **only `R2_ENDPOINT_URL`** to the S3 API root. Real R2 then passed.
- Neon URL was earlier normalized from `postgresql://` to `postgresql+psycopg://`. Generated `AUTH_CSRF_SECRET` replaced a placeholder. Never show values.
- User's `GEMINI_API_KEY_1/2/3` authenticate successfully. New config fields accept all three and, when singular `GEMINI_API_KEY` is absent, select first nonempty key. **There is no rotation/pooling implementation**; preserve request/retry/cost caps. Explicit singular key takes precedence.
- `OPENROUTER_API_KEY` is absent. User asked to use Gemini. Do not block Gemini-only tests or repeatedly request OpenRouter; report separate OpenRouter integration as unavailable/unwired.
- `.env.qa` has all user LLM flags true. **Do not blindly launch bulk jobs with it**. `qa_cloud_validation.py` explicitly injects only DB/R2/auth config and disables all LLM, tracing and runtime experimental flags.

Real cloud evidence:

- Initially Neon `SELECT 1` passed and public schema had 0 tables (`cloud-connectivity.json`).
- Dedicated QA migrations were explicitly executed to actual head `0025_pipeline_checkpoints`, 41 public tables. Only this staging DB was changed.
- `real-cloud-final/results.json`: real R2 upload/download/checksum/presigned/owned-object cleanup PASS; explicit Neon migration PASS; **100-record API → Neon → deterministic pipeline → preview/execute → R2 CSV export → signed download checksum → `/ready` PASS** (~97.187s pipeline/workflow case).
- Cloud pipeline was invoked synchronously in process using actual runtime service and DB; **not** a separate cloud worker/deployed frontend test. Local Docker worker is separately tested.
- First cloud harness attempt used `@example.test`, which registration correctly rejected. Fixed fixture email to reserved synthetic `@example.com`, then reran successfully. Preserve failed artifact as evidence; no production auth weakened.
- `gemini-canary.json`: explanation schema canary PASS and semantic response-ID/enum canary PASS using synthetic data, model `gemini-3.5-flash-lite`, max output 512 and retries 0. First pre-request harness KeyError sent zero requests. Explanation usage was captured (118 prompt /124 candidate tokens); semantic helper does not expose usage. Do not claim complete billing reconciliation or a deep LLM privacy/injection/hallucination audit.
- `cloud-updated.json` records successful free authentication of each numbered key, but its R2 BLOCKED status was captured BEFORE endpoint normalization; superseded by `real-cloud-final/results.json`.

## Execution evidence completed

| Test | Actual result | Artifact |
|---|---|---|
| Original backend full baseline | **846 PASS, 11 FAIL, 1 SKIP**, 560.30s | `artifacts/merge-baseline/20261009T025648Z-0770z2tc/` |
| Backend focused after first fixes | **747 PASS, 1 SKIP**, 499.73s | `artifacts/merge-baseline/20261009T031708Z-gds6j_bk/` |
| Full backend regression finished during checkpoint | **868 PASS, 1 SKIP**, 567.97s, exit0 | `artifacts/merge-baseline/20261009T033640Z-quzpx_cg/` |
| New focused QA regressions | **11 PASS**, 3.71s; later migration test added and not included in this execution | `artifacts/full-system-20261009/new-regressions.xml` |
| Frontend Vitest | **50/50 PASS**, 10 files | `frontend-baseline.xml` |
| Frontend lint and TypeScript | PASS | Earlier execution; rerun only for new UI changes |
| Existing Playwright E2E | **10/10 PASS**, ~1.1m, Next dev + Edge; API mocked | `artifacts/merge-validation/frontend-1791517010820/` |
| Real Docker HTTP smoke | all **8 cases PASS** including 100/1k/4k pipelines, invalid imports, auth/CSRF/CORS, preview/replay/download and S3 adapter | `live-fixed/results.json` |
| Real browser → Docker API → worker → CSV export | **6 cases PASS**, zero console errors/failed requests | `browser-live/results.json`, screenshots and `browser-export.csv` |
| Real Neon + R2 small workflow | all **3 cases PASS** | `real-cloud-final/results.json` |

The real browser fixture currently uses three synthetic records with names including QA/QC. It completed upload sessions, worker processing, preview/execute/download and history refresh. **It has not proved an actual multi-record merge** (list had no merge item). Strengthen this fixture with valid Vietnamese synthetic names and assert proposal/source coverage/DB changes, then test review/approve/reject, viewer restrictions, golden selection and split/rollback. Do not label these missing checks PASS.

### Docker datasets before and after NULL fix

| Dataset (10k directory) | Input count | Original live baseline | Live rerun after fix |
|---|---:|---|---|
| TC1 easy match | 10022 | FAIL after worker retries (`ValueError`) | PASS, 83139 candidates, 73166 serving pairs, 2190 proposals, 124.157s pipeline |
| TC2 easy non-match | 10000 | PASS, 39.625s pipeline | PASS, 72220 candidates, 50249 serving pairs, 49.984s pipeline |
| TC3 hard match | 9992 | PASS, 34.703s pipeline | PASS, 90130 candidates, 2245 serving pairs, no proposals, 40.046s pipeline |
| TC4 hard non-match | 10014 | FAIL (`ValueError`) | PASS, 79531 candidates, 52784 serving pairs, no proposals, 52.875s pipeline |
| TC5 real-world | 10010 decoded accepted | HTTP 422 | HTTP 422; unsupported source/missing-name contract, unresolved |

Artifacts `datasets-baseline/results.json` and `datasets-fixed/results.json`. Job completion, DB input counts, raw originals and lineage presence were asserted. Counts alone do not prove every golden-value lineage rule.

Timing comparisons above are **not controlled performance improvements**: concurrent QA/build activity and regenerated UUIDs affect timing and TF-IDF tie-break candidate counts. TC1/TC4 now complete, but there was no completed original live baseline to claim a speedup.

### Raw decoder and truth evaluation

- `scripts/qa_dataset_adapter.py` strictly reads authorized `raw/` with source_mapping.yaml, preserves source/ID/raw provenance, decodes declared UTF-8/CP1258/mixed CSV and XLSX, concatenates per source. This is **a QA adapter**, not native product format support. Never relabel Billing/DW to BILL/CRM, invent missing names/IDs, or use truth during computation.
- `datasets-v2/` is the correct inventory. TC5 has **10010 accepted + 9 rejected = 10019 logical rows**; 56 supplemental physical rows. Rejects: 6 missing IDs, 3 malformed rows; accepted rows contain 30 missing required names and Billing/DW sources.
- Earlier `datasets/` inventory wrongly counted three decomposed-Unicode total rows as malformed. Fixed decoder NFC detection to match GenVN's source contract; no truth or model data changed.
- `scripts/qa_benchmark.py` computes from raw-decoded CSVs first; only AFTER computation reads truth record_key/entity_id/label_status/batch_id/source_system. Only CERTAIN labels used for pair metrics. Ground truth never feeds matching.

Offline serial StageRunner baseline (`stages-baseline/`):

| TC | Runtime s | Peak process RSS bytes | Candidates | Known positive candidate recall | Auto-merge false labelled pairs |
|---|---:|---:|---:|---:|---:|
| 1 | 97.812 | 1184858112 | 83124 | 4894/4894 = 100% | 0 |
| 2 | 52.859 | 968937472 | 72213 | N/A, zero positives | 0 |
| 3 | 33.391 | 795766784 | 90129 | **3256/4864 = 66.94%** | 0 |
| 4 | 32.375 | 1048399872 | 79522 | N/A, zero positives | 0 |
| 5 | 42.813 | 920854528 | 85492 | **9680/10582 = 91.48%** | 0 |

- TC3 all 4864 known positives cross both source and batch; only 3256 recovered. **1608 genuine pairs are missing.** Global ingestion is not limited to same chunk, but default blocking policy still misses hard duplicates. Do not claim cross-batch recall is complete.
- TC5 cross-batch 7395/8212, cross-source 8781/9607 recovered. Pure offline pipeline completed despite native API ingestion failure; not a full TC5 E2E PASS.
- Auto-merge pair recall: TC1 44.75%; TC3 0%; TC5 3.52%. No false merge among evaluated CERTAIN pairs in this run is a scoped observation, not a blanket proof of correctness.
- `stages-optimized/TC1_easy_match/measurement.json` contains a first dictionary-review-lookup attempt: total **103.328s**, CLUSTER **63000ms** vs original **60171ms**; CPU 101.328s vs94.438s. **Decision and cluster hashes exactly match**, but speed did NOT improve. Do not report it as faster.
- Next identified source bottleneck: legacy `clustering.build_proposals` calls `run_cluster_guards(... pairs=ALL_PAIRS)` for every merge; `_shared_value_guard` scans the whole pair list each time. `load_policy()` also parses YAML per union. No fixes to these have been made yet. Prefer correctness-preserving indexed inter-cluster pair lookup, benchmark/hash comparison and guard regression; do not weaken any guard.

## Changes made (uncommitted)

1. Isolation/config/build: new `docker-compose.qa.yml`, `.env.qa.example`, ignored `.env.qa[.local]`, safer Docker context excludes, frontend `.dockerignore`; Node 20→24 because actual installed dependencies require newer Node. Root compose explicitly selects local DB profile for backend/worker. Root backend Docker dependency install separated from source copy (build evidence recorded; latest build still147s including image export—do not overclaim caching benefit).
2. `db/session.py`: PostgreSQL connect timeout5s, pool pre-ping and recycling.
3. `mdm/migrate.py`, `main.py`: read-only `validate_current` gate for production startup, no automatic production migration; new `/ready` probes schema/DB and configured S3 HeadBucket with safe unavailable response. Nonproduction behavior retains automatic upgrade.
4. `backend/migrations/env.py` **latest, not yet independently regression-run**: explicit Alembic config URL now wins over ambient `MDM_DATABASE_URL`; bounded PostgreSQL connection timeout. Before this, programmatically selected local migration could be redirected by environment to another database. New test verifies the unrelated DB remains untouched.
5. `resolution_import.py`: configured R2 source upload failure now fails HTTP502 and rolls DB back; compensate only owned attempted source keys on later failures, preserving unrelated objects. Cleanup failure is logged safely, not hidden. Durable orphan reconciliation remains incomplete.
6. `storage/object_store.py`, `storage/r2.py`: download body closes even on read error; optional `R2_PRESIGN_ENDPOINT_URL` separates server/browser endpoints. `.env.example` updated.
7. `stage_runner.py`: direct in-memory fixture identity defaults, preserve explicit source identity; removed swallowed normalization exception; Parquet decisions now read via Arrow `pq.read_table(...).to_pylist()` preserving NULL instead of Pandas NaN; first dictionary review-pair lookup optimization (no measured speedup).
8. `resolution_worker.py`: safe traceback function-name/line list in failure logs (no exception text, code lines, locals, SQL params or secrets).
9. `config.py`, legacy Gemini funcs: output token cap and bounded configurable retries; accept numbered keys with first-nonempty fallback. No key rotation. Semantic default retry count remains1; canary explicitly0. QA/test isolation clears ALL numbered keys to prevent reading developer secrets through new fallback.
10. `scripts/run_merge_baseline.py`: explicit test-origin allowlist fixes harness CORS preflight failure while retaining production/auth controls.
11. `backend/tests/test_mdm/test_qa_regressions.py`: storage compensation/atomicity, stream closure, signing endpoints, production read-only schema gate, source identity preservation, normalization failure visibility, output/retry caps, key fallback, real mixed NULL/hard-conflict serving boundary, latest explicit migration-target regression.
12. New QA scripts: `qa_live_system.py`, `qa_dataset_adapter.py`, `qa_cloud_validation.py`, `qa_benchmark.py`, `qa_browser.mjs`. QA-only host installs: openpyxl3.1.5, et_xmlfile2.0.0, psutil7.2.2; need document/pin QA reproducibility dependencies. Do not upgrade major runtimes arbitrarily.

Root causes verified, not guesses: diagnostic StageRunner over failed TC1 succeeded, but serving adapter over decisions Parquet yielded **68279 `ENGINE_GROUP` errors**; Arrow groups were None while Pandas groups were NaN. Fix retained strict adapter. Initial Moto5.1.12 caused multipart FlexibleChecksumError with current boto SDK; Moto5.2.3 passed without disabling validation. Source ingestion silently swallowed R2 errors originally; fixed fail-closed. Old backend baseline10 StageRunner failures were missing fixture external_id; remaining original CORS failure was harness allowlist.

## Active work at pause — inspect before re-running

The already-launched isolated **full backend regression finished while the checkpoint was being saved: 868 PASS, 1 SKIP, 567.97 seconds, exit0**:

```powershell
.venv/Scripts/python.exe -X utf8 scripts/run_merge_baseline.py --suite all --timeout 900
```

- Tool session ID **68749** completed; there is no remaining active CLI test session.
- Report directory **`artifacts/merge-baseline/20261009T033640Z-quzpx_cg/`**. Inspect machine report, XML and log. This run began before the latest migration-target test/optimization edits; run relevant new regression tests afterward despite this green result.
- No paid LLM call is running/pending. Live dataset, cloud, benchmark, browser and Docker build scripts have finished.
- Docker stack is intentionally left running for resume. No containers/volumes were deleted for pause.

## Exact next steps on resume

1. Read this file; inspect `git status`, current reports and full-test result. Preserve user work. Check Docker QA health and active job state. No need to rediscover repository or ask again for credentials/budget already supplied.
2. Run latest targeted QA + migration + cluster/stage tests with `run_merge_baseline.isolated_environment`; no ambient secrets/LLM. Use safe harness commands; NEVER ordinary pytest against developer env without isolation.
3. Capture a real synthetic cross-batch A/B/C at batches1/5/8 and CRM/ERP/BILL with small batch size; verify global candidate union, dedup and scoring coverage. Investigate TC3/TC5 missing positives through aggregate/evidence-safe diagnostics. Default DOB/province partitions and guarded oversized blocks are likely limitations; test implemented sparse/global option in isolation. Do not remove workload guards or change truth to improve recall. If large redesign is needed, document a concrete blocker/proposal.
4. Optimize measured legacy guard full-pair rescans only after retaining baseline artifacts. Preserve ordering/all evidence/guards and compare decision/cluster hashes and candidate counts. First review-lookup optimization did not speed up; report that honestly.
5. Rebuild current backend image, activate only QA backend/worker after jobs finish, rerun E2E smoke and relevant datasets. No need to recreate Moto and lose objects. For current source change: `docker compose --env-file .env.qa.local -p masterone-qa -f docker-compose.qa.yml build backend` then `... up -d backend worker` (inspect dependency behavior; avoid unnecessary storage replacement).
6. Improve live browser fixture and DB assertions. Test real review approve/reject, golden field selection, viewer restrictions, stale/double submit, actual merge, audit/split/rollback; no API mocks. Existing mocked browser tests already passed and should be labeled separately.
7. Run local PostgreSQL real concurrency/queue claim/fencing/recovery, worker termination/restart/lease resume, pool recovery after DB restart, upload concurrency, missing/failed storage consistency, EXPLAIN/index and bounded latency samples. Existing unit tests cover many paths, but chaos/load checks and independent two-worker local assertions remain incomplete. Never restart the unrelated project services.
8. Test existing v2/generation/resume/streaming flags in synthetic QA only. Pin selections and keep policy/guard caps. Compare legacy/v2 outputs rather than assuming equivalence. Record unimplemented/unmounted features accurately.
9. Extend real R2 probe to bounded multipart/Parquet/range/concurrency/retry tests if necessary; basic real cloud upload/export already PASS. Staging tests should be modest and only owned-prefix cleanup. No additional bulk/cloud cost assumptions.
10. Complete six required reports and machine matrix/gates, with commands/artifacts, exact actual outcomes and NOT RUN/BLOCKED for missing evidence. Define inventory counts from actual components. Current blockers: hard-match candidate recall66.94%, TC5 API source/name/native format incompatibility, unfinished recovery/RBAC/rollback validation, unwired OpenRouter/new LLM routes, legacy Group D four-eyes policy gap, storage reconciliation/durability limitations, incomplete production metrics/log wiring. `logging_config` PII arg-mask issue was noticed but not fixed/activated; deeper LLM/security audits must remain separate.
11. Final Vietnamese handover should explain that 21k was NOT RUN per explicit10k-only instruction, quote controlled measurements without fabricated speedups, and choose exactly READY FOR NEXT AUDIT / CONDITIONALLY READY / NOT READY based on actual gates. Do not claim full readiness merely because services are Up and tests are numerous.

Useful completed commands (rerun only if changed/needed):

```powershell
.venv/Scripts/python.exe -X utf8 scripts/qa_live_system.py --mode smoke --output artifacts/full-system-20261009/<new-unique-folder>
.venv/Scripts/python.exe -X utf8 scripts/qa_live_system.py --mode datasets --datasets artifacts/full-system-20261009/datasets-v2 --output artifacts/full-system-20261009/<new-unique-folder>
.venv/Scripts/python.exe -X utf8 scripts/qa_benchmark.py --datasets artifacts/full-system-20261009/datasets-v2 --output artifacts/full-system-20261009/<new-unique-folder> --only TC1_easy_match
.venv/Scripts/python.exe -X utf8 scripts/qa_cloud_validation.py --output artifacts/full-system-20261009/<new-unique-folder>
node scripts/qa_browser.mjs
node scripts/run_merge_frontend_smoke.mjs
```

`qa_benchmark.py` output subdirectory must be new (exist_ok=False). Its default truth path derives from the actual Unicode working-directory parent, avoiding PowerShell stdin Unicode encoding corruption. Browser script currently overwrites its own `browser-live/` evidence; preserve a copy or parameterize a new output path before changing it. Broad Ruff on stage_runner had26 existing style/unused issues; unsafe autofix could delete DuckDB dynamically discovered Arrow variables. Targeted new-code Ruff passed earlier, but final lint across latest changes remains pending.

Safe pause/stop if the user wants resources released later: run **only** QA project's `... stop` (or `down` without `-v` after fresh emulator backup). Keep `.env.qa`, `.env.qa.local`, worktree, volumes and artifacts. No automatic stop/deletion was performed at this pause.


## Resume completion ? 2026-10-09

This update preserves the original paused snapshot above. It supersedes its current-status, remaining-budget and missing-report statements.

- Scoped assessment and all six handover reports are written. Current decision **NOT READY**, not a full-system certification. Machine state: `artifacts/full-system-20261009/audit-state.json`; test index: `test-matrix.json`; measurements: `benchmark-results.json`.
- Reports: `FULL_SYSTEM_AUDIT.md`, `TEST_MATRIX.md`, `PERFORMANCE_BENCHMARK.md`, `INFRA_VALIDATION.md`, `PRODUCTION_READINESS.md`, `RUNBOOK.md` in this directory.
- Latest full backend execution: **884 PASS / 1 SKIP**, 469.10s, `artifacts/merge-baseline/20261009T045434Z-j07fsl5t/pytest.xml`. This full suite precedes the final Gemini schema change; related post-change regression: **43 PASS**, `gemini-schema-final/results.xml`. Frontend: 50 unit PASS, 10 mocked E2E PASS, 9 actual production-browser workflow cases PASS; Viewer and DB lineage/override checks have separate artifacts. CI-required broad Ruff still **FAIL: 210 findings**.
- Final backend image built successfully and matches 13 changed module hashes. Five QA services running: PG/API/Next have healthy healthchecks; worker/Moto have functional evidence but no container healthcheck. Legacy local worker crash/retry, two workers/claim fencing, interrupted QA DB/storage and missing-output recovery are exercised. No production deployment.
- Real QA Neon and R2 tests executed, including cloud 100-record API pipeline, R2 11.7MB multipart/checksum/range/Parquet/concurrency/abort and owned-artifact cleanup. Full independent cloud worker/TLS/recovery matrix remains NOT RUN.
- Only requested five 10k datasets were used. TC1?4 native Docker pipelines completed. TC5 native API rejects unsupported source/name/format contract; offline QA adapter results are not native PASS. 21k/100k/1M NOT RUN.
- Synthetic cross-batch fixture passed, but genuine candidate misses remain: default TC3 recall 66.94%, TC5 91.48%. Global sparse union probe improves coverage but still misses positives and is NOT activated. No labels, guards or policy thresholds weakened.
- Verified guard optimization: TC1 97.812?34.437s, CLUSTER 60.171?1.687s, equal decision/cluster hashes during the controlled optimization comparison; about 100MiB RSS tradeoff. Later calendar-validation final run 35.578s; model source fingerprint changes are separately reconciled by pair ID, including actual TC5 evidence/subgroup/score corrections. Opt-in v2 TC1 completes but takes 474.531s and has different policy semantics. Experimental flags restored OFF.
- Gemini generation ledger now **4/4**, no retries. Request 3 exposed actual API schema failure; explicit bounded request 4 passed after schema enum/summary fix and subsequent API replay used cache. Exact billed spend is not reconciled. **Do not rerun canaries or reset accounting.** Three numbered keys select first available fallback, not rotation or a multiplied budget. OpenRouter/new LLM routes remain unwired; no new credentials needed for the executed Gemini scope.
- No commit/staging/push; branch remains `qa/full-system-20261009`. Preserve all prior user work and ignored local artifacts. QA Docker is left running. Moto is volatile: do not stop/recreate it without a fresh backup; older 17-object backup does not cover subsequent artifacts. Never run `down -v` or prune.

### Next work, without repeating the completed audit

1. Read `PRODUCTION_READINESS.md`, `FULL_SYSTEM_AUDIT.md`, then machine `audit-state.json` and `TEST_MATRIX.md`. Verify current Docker readiness and source/image hashes if resuming after further edits.
2. Resolve candidate retrieval recall with a shadow comparison retaining the baseline candidate set and independent CERTAIN labels. Define product support for native TC5 rather than relabeling/dropping unsupported data.
3. Profile v2 checkpoint throughput while retaining resume/guard invariants; resolve active GroupD approval and legacy/new LLM runtime/cost ownership contracts before making business-policy changes.
4. Implement/validate durable reconciliation, worker monitoring and cloud independent-worker/TLS/recovery evidence; repair required CI lint and dependency reproducibility.
5. Rerun only tests affected by subsequent changes, then regression/E2E/benchmarks and update gates. Paid LLM execution requires a new explicitly authorized allowance; no secret values in chat, logs or reports.

Commands, safety boundaries and unique output-directory conventions are in `RUNBOOK.md`. Historical failures stay archived; never overwrite them into PASS.
