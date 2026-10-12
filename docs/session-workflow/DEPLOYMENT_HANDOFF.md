> Ghi chú 12/10/2026: runtime và source hiện tại được kiểm toán trong [AUDIT_20261012.vi.md](AUDIT_20261012.vi.md). Nội dung bên dưới là bằng chứng candidate lịch sử; không dùng trực tiếp project/port/migration cũ cho stack đang chạy.

# Deployment handoff — QA candidate, production NO-GO

Read RELEASE_READINESS.md and FINAL_DEFECT_REGISTER.md before rollout. No production cutover, publication, push or commit occurred.

Source HEAD `4cb00d5870a99a1a56bac6360af3b65bc2cfa8e7` plus dirty source/file hashes in `S16/resume-candidate-04/candidate.json` identifies the candidate. The inherited staged index SHA `880e475b4a9a5eaeb97311beb62b97cc5eaff3264c3523270d2a70efceb82c8e` is preserved. All 125 installed source/data files match; migration head is `0030_workflow_pair_tags`. Containers are healthy; API/worker run as appuser and FE as node, without host source bind or packaged credentials/install tools.

| Runtime | Tested container image identity |
|---|---|
| API / worker | `sha256:ab858a71dd31804ac9d8561fe9607f0d3f125eb90468cdd99402db0c09d3b2c1` |
| Frontend | `sha256:68b332a342abd3e75f433fe418fa7fecd6c5970e16bc686fdd1dfba3167c6460` |
| PostgreSQL | `sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea` |

These are local identities, not published registry digests. OCI index IDs from tag inspection can differ from container image IDs; do not substitute them in acceptance assertions. Final scan tag/layer records are in `S15/resume-final-image-scans.json`.

QA project `masterone-workflow-live`: FE 13018, API 18018, PG 15448, database `masterone_qa/public`, private bucket `masterone-qa`. Frontend embeds `http://127.0.0.1:18018`; staging requires rebuilding for the actual HTTPS API origin. Base digests and runtime lock remain pinned. Resource limits are QA examples, not approved capacity.

The tested Compose stack uses docker-compose.qa.yml, docker-compose.workflow.yml, docker-compose.workflow.live.yml and S10/paid-live-06/runtime.json. The helper privately reads secrets from .env.qa into QA_R2_* and QA_GEMINI_API_KEY process variables. The env file was not edited or copied into images/reports. Capability flags remain true; persisted llm_policy_control.enabled=false stops dispatch. Shared accounting is 8 calls, 26,874 reserved tokens and 80,622 microUSD reserved, not an invoice. Do not reset it or run the paid helper merely to start containers.

Direct run `bfa1fab1-5df8-4e2c-995a-0b84b2d62db8` passes actual browser PUT without proxy, server persistence, final export and same-run restart/restore/reconciliation. Paid run `20ce3983-679b-4472-9300-2aa733fef7bb` verifies worker semantic, mounted explanation and reload cache. Extended browser13 verifies stale409, bridge guard, split, immutable history download and visible audit. Doc/harness-only changes need no paid repetition.

Read-only checks from repository root require fresh output directories:

```powershell
.venv\Scripts\python.exe -X utf8 scripts\qa_workflow_candidate.py --output artifacts/session-workflow/20261011-workflow-01/S16/candidate-NEXT
.venv\Scripts\python.exe -X utf8 scripts\qa_workflow_reconcile.py --source artifacts/session-workflow/20261011-workflow-01/S11/resume-direct-06/browser.json --output artifacts/session-workflow/20261011-workflow-01/S16/reconcile-NEXT
.venv\Scripts\python.exe -X utf8 scripts\qa_workflow_audit.py --output artifacts/session-workflow/20261011-workflow-01/S16/audit-NEXT --baseline artifacts/merge-baseline/20261011T044545Z-9p1okras --evidence-manifest artifacts/session-workflow/20261011-workflow-01/S16/resume-evidence-manifest-01.json
```

The audit returns1 for NO-GO. New evidence needs a new manifest. STAGING_SETUP.md contains deployment templates and validation commands; the actual target/domain/certificate and profile are still required.

Before staging migration, validate DB/bucket ownership, back up DB and every referenced object/checksum, then run explicit migration before API/worker. Preserve prior binary and snapshots; do not blindly downgrade/drop history. Actual prior-binary rollback on clone/staging is NOT_RUN. Recovery clone `workflow_restore_a8ada5c7c7594c63` and its qa-restore prefix are retained. Restore/browser/shadow-missing/outage drills pass; operator, RPO/RTO, retention and production alert sink remain unapproved. See RECOVERY_RUNBOOK.md.

Close WF-03 through WF-07 using actual operational commitments, supported benchmark scope/targets, zlib resolution, remote CI and HTTPS staging with rollback/cookie/origin/storage acceptance. Regenerate affected candidate evidence afterward. Every required gate must PASS for GO; production cutover requires a subsequent user instruction.
