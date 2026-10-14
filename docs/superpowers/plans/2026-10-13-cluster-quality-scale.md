# Cluster Quality and Scale Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Người dùng đã chọn triển khai lần lượt trực tiếp trong phiên này. Các bước dùng checkbox để ghi kết quả thật.

**Goal:** Sửa các vấn đề còn tồn tại của tạo nhóm/golden, giữ duyệt trước khi gộp và kiểm tra phối hợp qua preview, publication, repair, confirmation và export ở 100k/1M.

**Architecture:** Một graph/index và bộ consistency kiểm tra mọi cặp phục vụ các đường tạo nhóm. Policy golden có phiên bản, entity_id tách khỏi snapshot ID; singleton lấy từ eligible universe đã đóng băng. Manifest Parquet, reader/index disk-backed và iterator thay các snapshot/final result JSON lớn, với reader tương thích dữ liệu cũ.

**Tech Stack:** Python 3.13 của `.venv` hiện có; FastAPI, SQLAlchemy/Alembic, SQLite/PostgreSQL, DuckDB/PyArrow và object store đã có; Next.js/React, Vitest/Playwright.

**Spec:** [2026-10-13-cluster-quality-scale-design.md](../specs/2026-10-13-cluster-quality-scale-design.md), người dùng đã duyệt đầy đủ.

## Global Constraints

- Không tạo thêm bất kỳ file Docker Compose nào. File hiện có là `docker-compose.yml`.
- Giữ các thay đổi chưa commit trong checkout. Không reset, clean hoặc stage toàn bộ diff cũ; checkpoint bằng checklist và diff riêng của tác vụ, chưa commit/push.
- Giữ duyệt trước khi gộp; không bật automation hoặc thay threshold matching.
- Không đưa truth/scenario labels vào feature, phân loại, ưu tiên cạnh, phê duyệt hoặc lựa chọn golden.
- Golden: loại MISSING/INVALID/PLACEHOLDER khỏi primary; contact SHARED/shared_risk chỉ vào alternatives nếu chưa có lựa chọn thủ công rõ ràng.
- Xếp ứng viên hợp lệ: verified → quality → source priority của field → timestamp nguồn có múi giờ → tie-break ổn định.
- Giới hạn thường 20; đường duyệt nhóm lớn 21–100; ngân sách union nhóm lớn ít nhất 4.950. Nhóm >100 không bị chia theo vị trí dòng.
- Exact group approval chỉ miễn direct bridge evidence; hard constraints, cannot-link, REJECTED/NON_MATCH, source uniqueness và quyền cặp còn hiệu lực.
- Giữ operations/audit/revision/lease fencing. Cache không được tăng ngân sách hoặc sử dụng receipt stale.
- ID thực thể ổn định trong cùng run; không hứa liên kết xuyên run độc lập. Không sửa artifact/ID lịch sử.
- QA dùng DB và object store riêng, tắt R2/LLM/tracing thật; không migration hoặc deploy DB đang dùng.
- Đọc hướng dẫn Next trong `frontend/node_modules/next/dist/docs` trước sửa frontend, theo `frontend/AGENTS.md`.

## Review Focus

1. Metadata verified mâu thuẫn quality, contact chỉ SHARED, raw fallback và export danh sách contact phải cùng loại giá trị không phù hợp — Tasks 1, 8.
2. Một cache đã kiểm tra group exception không được cấp quyền cho strict check; ngân sách/None weight/stale receipt phải chặn đúng — Tasks 3, 4, 8.
3. Split/merge/retry không nhân bản entity_id hoặc âm thầm xác nhận override cha cho nhiều người — Tasks 5, 8.
4. Record chưa được kiểm tra hoặc unresolved không thành singleton; bulk confirmation và export chỉ dựa trên source universe đóng băng — Tasks 6–8.
5. Old artifact, partition thiếu/hỏng, cancel/lease expiry và quyết định đổi sau preview không gây publish một phần hoặc đọc nhầm generation — Tasks 7, 8, 10.

## File Structure and Execution Order

Module mới giới hạn theo trách nhiệm: `pipeline/survivorship_policy.py` (lọc/rank),
`workflow_cluster_index.py` (index snapshot), `workflow_cluster_engine.py` (components
đã duyệt và kết quả), `workflow_entities.py` (ID/lineage), `workflow_generation_reads.py`
(nhóm thật/ảo và coverage), `workflow_snapshot_storage.py` (manifest/partitions),
`workflow_snapshot_views.py` (view/index disk-backed), `workflow_finalization.py`
(biên nhận golden/final result theo luồng).

Các đường dẫn backend không có prefix đầy đủ trong danh sách task được tính từ
`backend/src/masterone/mdm/`; đường dẫn `api/mdm_workflow.py` được tính từ
`backend/src/masterone/`. Tên test không có prefix nằm ở `backend/tests/test_mdm/`.

Giữ các entrypoint hiện tại làm adapter để API/worker/legacy callers không đổi tên.
Các task 1–5 hoạt động với JSON artifact hiện có và được kiểm tra trước thay đổi lưu
trữ. Task 6 cập nhật singleton cùng toàn bộ consumer trong một task; task 7 thêm
storage/view có test độc lập; task 8 nối storage vào worker và finalize/export. Không
bật reader mới trước khi producer/consumer và migration của task tương ứng hoàn tất.

Lệnh kiểm tra Python dùng môi trường `isolated_environment()` của runner hiện có.
Tạo `scripts/run_cluster_checks.py` ở Task 1 để gọi pytest theo suite dưới đây;
không đọc `.env` để kết nối DB thật. Pytest plugin autoload tắt, bật riêng
`pytest_asyncio.plugin` và `tests.merge_baseline_plugin`; dùng scratch sở hữu riêng.

## Task 1: Golden Policy, Shared Guard and Contact Export

**Files:**
- Create: `backend/src/masterone/mdm/pipeline/survivorship_policy.py`, `backend/tests/test_mdm/test_survivorship_policy.py`, `scripts/run_cluster_checks.py`.
- Modify: `pipeline/golden.py`, `pipeline/golden_v2.py`, `pipeline/cluster_policy.py`, `mdm/config/clustering_person_v2.yaml`, `pipeline/cluster_guards.py`, `workflow_exports.py`, `golden_service.py`, `generation_staging.py`, `workflow_cluster_preview.py` (tất cả trong `backend/src/masterone/mdm/`).
- Test: existing `test_safe_clustering.py`, `test_golden_and_benchmark.py`, `test_cluster_guards.py`, `test_workflow_golden.py`.

**Interfaces:**
- Consumes: existing record dictionaries, `ClusterPolicy`, `build_golden(...)`, `selected_golden(...)`, `field_value(field, data)`.
- Produces: `eligible_primary(field: str, observation: dict) -> bool`; `observation_rank(field: str, observation: dict, policy: ClusterPolicy) -> tuple`; `primary_values(field: str, data: dict) -> list[dict]`.
- Extend `ClusterPolicy` with `survivorship_version='p119_golden_v3'` and `field_source_priority: tuple[tuple[str, tuple[tuple[str, int], ...]], ...] = ()`. YAML field overrides default `{}`; source fallback stays CRM=0, ERP=1, BILL=2, unknown=99.
- Observation adds `eligible_primary: bool`, `excluded_reason: str | None`; contact `values` contains eligible values, `alternatives` retains other observations/provenance. With no eligible primary, `value=None`, contact `values=[]`; warning does not alone require a nonblank contact to confirm.
- `scripts/run_cluster_checks.py --suite <name>` provides `golden`, `hash`, `components`, `groups`, `identity`, `singletons`, `storage`, `workflow`, `all`; prints report path and returns pytest exit code.

- [x] **Step 1: RED — add tests with exact assertions.**

```python
assert golden_with_crm_placeholder_erp_verified()['fields']['name']['source_system'] == 'ERP'
assert golden_with_shared_and_personal_contact()['fields']['email']['value'] == 'personal@example.test'
assert all_shared_golden()['fields']['email']['value'] is None
assert all_shared_golden()['fields']['email']['values'] == []
assert all_shared_golden()['fields']['email']['alternatives'][0]['quality'] == 'SHARED'
assert field_value('email', golden_with_shared_and_personal_contact()['fields']['email']) == 'personal@example.test'
assert eligible_primary('name', {'value': 'bad', 'verified': True, 'quality': 'INVALID'}) is False
```

Include raw fallback retaining bad quality, PARTIAL/UNKNOWN, timezone-equivalent times,
invalid/naive time, deterministic ties/permutations, field-specific source override,
source not configured, v1-shaped records and explicit manual source/override.
Add DF-only shared guard assertion `is None`, SH warning assertion `severity == 'WARN'`.
Các helper trong assertion là fixture test mới, xây bằng helper record hiện có với
đúng quality/source/value nêu trên; phải định nghĩa chúng trước chạy RED để tránh
NameError thay cho failure về hành vi.

- [x] **Step 2: Run RED.** Run `.venv/Scripts/python.exe -B -X utf8 scripts/run_cluster_checks.py --suite golden`; confirm failures are the intended behavior, not fixture/import/environment errors. First create the runner as diagnostic infrastructure using existing isolation helper.
- [x] **Step 3: GREEN — implement policy/filter/rank once.** Route new golden builds through these primitives while retaining legacy output shape/readability; never rewrite old drafts. Use real source time and separate ID tie-break. Update manual-selected contact metadata so an explicit source/manual choice can be exported, while untouched shared alternatives cannot leak through `field_value`. Add new policy/module hashes to execution/publication identity; do not cache a policy across YAML content changes.
- [x] **Step 4: Verify.** Run golden suite and workflow golden/export round trip. Existing override/timestamp tests and new shared-only export assertions must pass; record new policy version in golden outputs. Update the existing assertion in `test_golden_source_verification_real_timestamp_and_ties` that currently makes unverified CRM beat verified BILL: the approved policy deliberately reverses this result. Keep both source-priority tie behavior and verification-over-source tests; do not delete coverage to obtain green.
- [x] **Step 5: Checkpoint.** Inspect task diff, update checklist, record before/after golden examples as aggregate assertions without raw benchmark rows.

## Task 2: Canonical Incremental Hash and Repair Publication Consistency

**Files:** Modify `backend/src/masterone/mdm/generation_staging.py`, `workflow_resolution.py`; create `backend/tests/test_mdm/test_workflow_digest.py`; extend `test_workflow_audit_fixes.py`, `test_workflow_partial_publication.py`.

**Interfaces:**
- Consumes: `compute_publication(snapshot, run_id, revision, *, previous_plan=None)` and canonical `encode(value)`.
- Produces: existing `digest(value) -> str` with exactly the old SHA-256 byte contract; `repair_preview_groups(...)` delegates eligibility/group creation to `compute_publication` and returns `(computed, groups)`.

- [x] **Step 1: RED — add compatibility and transition tests.**

```python
assert digest(payload) == sha256(encode(payload)).hexdigest()
assert digest('Tiếng Việt \u2028') == sha256(encode('Tiếng Việt \u2028')).hexdigest()
assert repair_preview_groups(snapshot, 'run', 2)[1] == compute_publication(snapshot, 'run', 2)['clusters']
```

Parametrize nested objects, key order, empty containers, Unicode, floats including -0.0,
large strings, booleans/None; unsupported values, NaN and Infinity must still raise.
Monkeypatch `encode` to fail during a large `digest` call to prove it no longer builds
the whole encoded payload. Add repair with pending pair, partial/complete, and a
disconnected safe group; pending records must not become repair-preview singleton.

- [x] **Step 2: Run RED.** `.venv/Scripts/python.exe -B -X utf8 scripts/run_cluster_checks.py --suite hash`.
- [x] **Step 3: GREEN — stream canonical encoding into SHA-256.** Preserve sort_keys/ensure_ascii/separators/allow_nan behavior and numeric representation. Hash large strings in bounded chunks; do not treat an encoder that creates a full escaped string as solving the large-string case. Remove per-record conflict-set construction by using the shared publication function.
- [x] **Step 4: Verify.** Hash suite plus snapshot replay, plan cache, repair/apply/undo, artifact checksums and golden/export tests; legacy receipts remain valid.
- [x] **Step 5: Checkpoint.** Record compatible fixture hashes and bounded-allocation evidence; do not claim the whole workflow is streaming yet.

## Task 3: Shared Snapshot Index, Accepted Components and Guard Cache Modes

**Files:** Create `backend/src/masterone/mdm/workflow_cluster_index.py`, `workflow_cluster_engine.py`, `backend/tests/test_mdm/test_reviewed_components.py`; modify `workflow_finalize.py`, `workflow_partial.py`, `workflow_plan_cache.py`, `workflow_group_approvals.py`, `pipeline/clustering.py`, `pipeline/cluster_guards.py`, `pipeline/decision_store.py`.

**Interfaces:**
- Consumes: snapshot records/pairs/overlays/reasons/negative links/approvals and the policy from Task 1.
- Produces: `SnapshotIndex(snapshot, *, frequencies=None)` with `by_id`, indexed pairs/incident links, global frequencies, `subset(record_ids) -> dict`, `region(seeds) -> set[str]` and `accepted_components() -> Iterator[list[str]]`.
- `compute_components(snapshot, run_id: str, *, index=None, frequencies=None) -> dict` returns existing clusters/safe_clusters/conflicts/pairs/conflict_record_ids/cluster_metrics plus `review_groups` descriptors (`member_record_ids`, `reason`, `fingerprint`, `pair_ids`). Existing `cluster_snapshot` remains its adapter.
- `DecisionStore.check(a, b, *, mode='strict')` and `save_check(a, b, reason, *, mode='strict')`; ephemeral SQLite cache PK includes mode. Validation derives mode from full policy and evidence exemption. Deny/veto/size checks still precede cache lookup.
- Ordinary policy bound remains 20; Task 4 adds reviewed-large bound. Scalar cache metrics retain `checked`, `visited`, `cache_hits`, `blocked`.

- [x] **Step 1: RED — accepted-component and cache tests.**

```python
assert memberships(compute_components(snapshot, 'run')) == memberships(compute_components(permuted(snapshot), 'run'))
assert not silently_drops_accepted_edge(conflicting_accepted_component())
assert group_approval_of_chain()['safe_clusters'][0]['member_record_ids'] == ['a', 'b', 'c']
assert strict_after_exception_check() == 'CLUSTER_BRIDGE_MISSING_EVIDENCE'
assert partial_with_weak_candidate_between_groups()['publication']['covered_records'] == 4
```

Use all edge permutations, reversed record list, random pair ID assignment, equal/None
weights; contradictory ACCEPTED triangle; strong control; chain and false bridge;
explicit hard/deny/veto; source uniqueness; group receipt stale/member change;
global vs subset frequencies; budget exhausted; full/cache results equal.
Instrument comparison/lookup calls and assert each distinct identity pair is evaluated
at most once per permission/consistency mode in a snapshot, not once per accepted edge.
Fixture vùng yếu có bốn record a/b/c/d, hai cạnh ACCEPTED a–b và c–d, thêm candidate
b–c INSUFFICIENT_EVIDENCE chưa được duyệt; candidate này không được giữ cả hai nhóm.
Helpers trong các assertion khác được định nghĩa trong test, dùng snapshot/chain
fixtures hiện có, không dùng mock thay engine consistency.

- [x] **Step 2: Run RED.** `.venv/Scripts/python.exe -B -X utf8 scripts/run_cluster_checks.py --suite components`.
- [x] **Step 3: GREEN — one component/consistency path.** Build accepted components before validation; on conflict keep the complete reviewed component unresolved and expose causal pairs rather than picking a weight-dependent subset. Missing direct evidence becomes an exact-group review draft. Keep existing auto proposal entrypoint as an adapter applying its selected policy; do not turn assessment review into auto eligibility. Share index and global frequencies across full/partial/approval/repair. Traverse only actionable membership dependencies; retained weak candidates alone do not connect unrelated publication regions.
- [x] **Step 4: Verify.** Components suite, existing legacy/v2 safe clustering, group approval, recovered-pair support, partial publication and plan cache tests. Preview and repair must use the same computed memberships as publication.
- [x] **Step 5: Checkpoint.** Record invariant/reason changes; audit explanations distinguish score, reviewer decision, missing evidence and hard permission conflict.

## Task 4: Reviewed Groups of 21–100 and Paginated Review UX

**Files:** Modify `pipeline/cluster_policy.py`, `mdm/config/clustering_person_v2.yaml`, `workflow_contracts.py`, `workflow_group_approvals.py`, `workflow_review_clusters.py`, `workflow_cluster_engine.py`, `workflow_cluster_preview.py`, `api/mdm_workflow.py` in backend; create `backend/tests/test_mdm/test_large_group_review.py`; modify `frontend/src/components/GroupApprovalDialog.tsx`, `GroupMembersTable.tsx`, `PairClusterReview.tsx`, `PreviewGroups.tsx`, `frontend/src/lib/workflowContracts.ts`, `workflowApi.ts`; add frontend group review tests.

**Interfaces:**
- Consumes: `review_groups`, fingerprint and shared validation from Task 3.
- Produces: policy `max_reviewed_cluster_size=100`, `max_reviewed_pair_checks_per_union=4950`; request transport accepts record IDs up to 100, server uses active policy.
- `validate_group(..., review_reason=None, frequencies=None)` selects reviewed bounds only with a nonblank group reason/valid exact receipt. Ordinary `build_proposals` remains bounded at 20.
- Group-member and additional-evidence pages have `items`, `total`, `next_cursor`; maximum page size 100, default 25. Preview embeds at most 25 members and reports the full member count; approval binds all IDs, not only the displayed page.
- GET `/workflow/cluster-previews/{operation_id}/review-groups/{fingerprint}/members` and `/evidence` are server-owned paginated reads of the sealed draft. Existing small-group route remains compatible.

- [x] **Step 1: RED — limits and API transitions.**

```python
assert ordinary_group(21)['reason'] == 'CLUSTER_SIZE_LIMIT'
assert reviewed_group(21, reason='Đã đối chiếu lịch sử')['conflicts'] == []
assert reviewed_group(100, reason='Đã đối chiếu lịch sử')['conflicts'] == []
assert reviewed_group(101, reason='Đã đối chiếu lịch sử')['reason'] == 'GROUP_MEMBERS_INVALID'
assert reviewed_group_with_cannot_link(100)['conflicts']
```

Test 19/20/21/100/101; reasonless large acceptance; 4.950 distinct-pair checks;
one mismatching member on a later page; stale token/profile; hidden internal pending
pair accepted with reason; hard/unique/veto cannot be exempted. Frontend must not
reject 21 by a hardcoded 20, must show totals and page navigation, and must send the
full frozen ID set. Over-100 review draft reports a bounded ID preview and total,
with no arbitrary split proposal.

- [x] **Step 2: Run RED.** Python groups suite and `npm.cmd --prefix frontend test -- GroupApprovalDialog PairClusterReview`.
- [x] **Step 3: GREEN — policy-driven bounds and paging.** Update every API/UI/worker bound together; separate normal and reviewed-large budget. Both full and partial publication recognize valid reviewed-large groups. Approval still verifies every member pair and the complete accepted connectivity. Add preview metadata for normal/reviewed bounds.
- [x] **Step 4: Verify.** Groups suite, components/partial/preview/fence tests; frontend group tests and `npm.cmd --prefix frontend run lint`.
- [x] **Step 5: Checkpoint.** API receipt replay and first/last page behavior must match publication, including groups with members absent from page one.

## Task 5: Persistent Entity Identity and Preserved Draft Choices

**Files:** Create `backend/src/masterone/mdm/workflow_entities.py`, `backend/tests/test_mdm/test_workflow_entity_identity.py`; modify `workflow_finalize.py`, `cluster_publication.py`, `golden_service.py`, `workflow_cluster_reads.py`, `workflow_exports.py`, `workflow_models.py`, `generation_staging.py`; extend existing recovery/audit/golden tests.

**Interfaces:**
- Consumes: complete generation membership plan, old group metadata/selections, ResolutionEntity/Member/Version tables and Task 1 policy.
- Produces: `assign_entity_ids(db, run, generation, groups, *, actor) -> Iterator[dict]`; each descriptor gets `entity_id`, `anchor_record_id`, `ancestor_entity_ids`, `identity_version=1`.
- Preserve versioned `cluster_id`; add optional `entity_id` to API responses. New export uses entity_id and records `master_id_version='workflow_entity_v1'`; old finalized generations still export their historical IDs.
- No schema replacement or new global identity service. New IDs derive with UUID5 from run/generation/server group identity for deterministic retry; virtual singleton entity IDs derive from run/record with a separate namespace. Persist ResolutionEntity only for materialized groups/exceptions. Use existing entity versions for bounded group golden/history.
- `carry_choices` keeps ambiguous/unconfirmed values in payload `pending_selections: dict[str, list[dict]]`; applicable selections remain in `selections`; `requires_manual_review` lists pending fields. One surviving branch may keep a changed-membership override as draft; other split branches only show it as ancestor candidate until explicit selection.

- [x] **Step 1: RED — lineage/choice tests.**

```python
assert unchanged_generation()['entity_id'] == previous['entity_id']
assert changed_single_ancestor()['entity_id'] == previous['entity_id']
assert len({group['entity_id'] for group in split_groups()}) == 2
assert sum(group['entity_id'] == previous['entity_id'] for group in split_groups()) == 1
assert changed_membership_override()['pending_selections']['name'][0]['value'] == 'Steward value'
assert not changed_membership_override()['confirmed']
```

Test merges choose oldest entity then ID; splits choose retained anchor else greatest
overlap then smallest member ID; global assignment never uses one ID twice; multiple
ancestor overrides remain visible; source removed/changed becomes pending; retry after
rollback uses the same IDs; reopen/repair/undo/history keeps valid lineage; source edit
cannot keep confirmation. Cross-run reuse does not occur. Export old vs new ID contract.

- [x] **Step 2: Run RED.** `.venv/Scripts/python.exe -B -X utf8 scripts/run_cluster_checks.py --suite identity`.
- [x] **Step 3: GREEN — assignment and draft preservation.** Plan ID assignment for the entire generation before publishing rows; do not rely on iteration order or current membership after partial updates. Keep assignment metadata in sealed publication plan so retries do not invent IDs. Reuse existing entity lifecycle transactionally; do not mutate immutable old generation payloads. Reset confirmation when required and expose pending candidates via golden API.
- [x] **Step 4: Verify.** Identity, groups, golden/exports, repair/apply/undo, retry and publication fence suites.
- [x] **Step 5: Checkpoint.** Record old/new API and export fixtures; no source-record or user-defined external ID is repurposed as time or verified identity.

## Task 6: Virtual Singleton Reader, Coverage and Confirmation Together

**Files:** Create `backend/src/masterone/mdm/workflow_generation_reads.py`, `backend/tests/test_mdm/test_virtual_singletons.py`, migration `backend/migrations/versions/0032_workflow_generation_coverage.py`; modify `workflow_models.py`, `migrate.py`, `cluster_publication.py`, `workflow_finalize.py`, `workflow_cluster_reads.py`, `golden_service.py`, `workflow_resolution.py`, `workflow_pair_reads.py`, `workflow_service.py`, `workflow_exports.py`; extend migration/storage/golden/fence tests.

**Interfaces:**
- Consumes: generation's frozen eligible IDs/source snapshot and actual multi-member groups; Task 5 entity identity.
- Produces: `GenerationReader(db, run, generation)` exposing `group(cluster_id) -> dict`, `group_page(limit, cursor) -> dict`, `iter_groups(scope=None) -> Iterator[dict]`, `record(record_id) -> dict`, `coverage() -> dict`, `unconfirmed_count() -> int`.
- Migration adds `WorkflowGeneration.coverage: JSON` default `{}`, `singleton_confirmation: JSON` default `{}`, `storage_version: String(32)` default `workflow_json_v1` with old-compatible server defaults and one Alembic head. New virtual generations use `workflow_virtual_v2`; partitioned generations from Task 8 use `workflow_parquet_v2`. Coverage metadata contains counts and immutable artifact references, not a full ID array.
- `virtual_cluster_id(generation_id: str, record_id: str) -> str` uses reversible `s:{generation_id}:{record_id}`; resolve only after run/generation ownership and eligible membership checks. Entity ID remains stable independently of this snapshot-scoped ID. Old hashed cluster IDs remain supported.
- Virtual singleton exceptions reuse WorkflowCluster/Member only when individually selected/edited/confirmed. Bulk singleton confirmation records one generation/hash receipt; explicit exception confirmation overrides the default. Persisted singleton exceptions replace, never duplicate, their virtual row.

- [x] **Step 1: RED — coverage and mutation tests.**

```python
assert publish_all_singletons(1000).persisted_cluster_count == 0
assert publish_all_singletons(1000).coverage['covered_records'] == 1000
assert count_exported_source_ids(finalized_all_singletons(1000)) == 1000
assert unresolved_record_id not in published_singleton_ids(partial_generation)
assert edit_one_virtual_singleton(1000).persisted_cluster_count == 1
assert confirm_all_singletons(1000).singleton_confirmation['default_confirmed'] is True
```

Test empty/one/all singleton, multi-record+singleton mixture, paging boundaries,
selected/excluded scopes, cross-run forged virtual IDs, stale generations, frozen
raw fields after source edit, shared-only sparse golden, manual edit after bulk confirm,
an unresolved extra-field/pending-override exception still blocking finalize, reopen,
repair/undo and old materialized generations. Query/statement spies prove no write for
each untouched singleton and no N-group hydration to compute counts.

- [x] **Step 2: Run RED.** Singletons suite and additive migration test from HEAD 0031.
- [x] **Step 3: GREEN — reader plus all consumers in this task.** Keep compact eligible-universe artifact under old partitioned JSON until Task 7. Generate singleton golden lazily from the pinned source snapshot/policy. Replace direct group/member-count and group-scope assumptions across service, reads, golden mutations, repair and export. Set HEAD=0032 only when migration/model/tests agree. Keep old generation readers and operations replay compatible. Confirmation does not imply identity merge approval.
- [x] **Step 4: Verify.** Singletons/migration/workflow golden/finalization/repair/exports/coverage suites. Read after reopen and refresh must return the same official coverage and correct read-only state.
- [x] **Step 5: Checkpoint.** Compare row counts/DB statement counts for all-singleton vs mixed fixture; no loss or duplicate export records.

## Task 7: Partitioned Workflow Manifests and Disk-backed Snapshot Views

**Files:** Create `backend/src/masterone/mdm/workflow_snapshot_storage.py`, `workflow_snapshot_views.py`, `backend/tests/test_mdm/test_workflow_snapshot_streaming.py`; modify `workflow_artifacts.py`, `workflow_cluster_index.py`, `pipeline/decision_store.py`, `generation_staging.py`, `config.py`; extend `test_workflow_handoff_storage.py`, `backend/tests/test_partition_artifacts.py`.

**Interfaces:**
- Consumes: existing `DatasetManifest`, `PartitionArtifact`, `ArtifactWriter`, object-store upload/readback receipts and Task 2 hash; existing JSON artifact reader.
- Produces: `seal_snapshot(factory, run_id: str, phase: str, metadata: dict, streams: Mapping[str, Iterable[dict]], *, operation_id: str) -> str`; `open_snapshot(db, artifact_id: str, run_id: str, phase: str)` context manager returning `SnapshotView`.
- `SnapshotView` exposes immutable small `metadata`, ID-addressable `records`, indexed iterable `pairs`, `overlays`, `review_reasons`, `negative_pairs`, `group_approvals`, `iter_rows(name, *, batch_size=512)` and `record_page(ids)`; all resource/index ownership is context-managed.
- Manifest version `p119_workflow_snapshot_v2`; metadata includes schema/hash/count/source-snapshot identity, operation/attempt ownership and verified partition references. Record/pair partition schema uses stable IDs plus canonical JSON payload columns; no truth columns. Default read/write batch 512, maximum 2048; maximum partition 16 MiB. Each row exceeding that bound fails with a structured artifact-budget error.
- DB artifact JSON stores bounded manifest metadata. Local QA uses existing LocalObjectStore under an owned workflow-artifact directory; configured remote uses existing store. Add `workflow_artifact_dir='./data/workflow-artifacts'` and isolated-runner override to scratch. No service/Compose dependency added.
- SnapshotIndex has the same interface as Task 3, but supports record/pair/frequency/adjacency/visited/component index tables on scratch SQLite/DuckDB. Oversized connected regions retain a disk-backed exclusion set, full count and bounded summary; do not build a giant Python member set or turn unvisited nodes into singleton.

- [x] **Step 1: RED — protocol and bounded access tests.**

```python
assert round_trip_snapshot(1100).record_count == 1100
assert snapshot_manifest().version == 'p119_workflow_snapshot_v2'
assert max(observed_read_batch_sizes()) <= 2048
assert group_page_from_manifest(510, 5)['total'] == expected_total
assert legacy_json_snapshot().records['record-id'] == original_record
```

Test old/new equivalence, Unicode/raw types, ordered pair keys, missing/modified partition,
wrong schema/hash/count/ownership/run, unverified remote readback, mixed old/new generation,
row larger than 16 MiB, cleanup only owned scratch. A sentinel forbids `list(all_records)`,
`deepcopy(full_snapshot)` and all-row DataFrames; requests opening one group may read
only its data and bounded metadata. Frequencies must equal full-snapshot calculation.
Large connected region >100 must retain every excluded record on disk with bounded summary.

- [x] **Step 2: Run RED.** `.venv/Scripts/python.exe -B -X utf8 scripts/run_cluster_checks.py --suite storage`.
- [x] **Step 3: GREEN — implement writer/view without switching worker yet.** Reuse artifact hash/upload/lease protocol; reference only verified immutable parts in a sealed receipt. Build indexes incrementally and parameterize queries. Disk index is a derived cache, never authority replacing sealed evidence. Legacy load remains available only to legacy consumers; new views do not materialize themselves through the compatibility adapter.
- [x] **Step 4: Verify.** Storage, object store, artifact corruption/page/readback, component/frequency equivalence and scoped group reads tests.
- [x] **Step 5: Checkpoint.** Record peak batches, partitions and queried rows; this task alone does not certify streaming publication or export.

## Task 8: Stream Worker, Finalization and Export with Existing Fences

**Files:** Create `backend/src/masterone/mdm/workflow_finalization.py`, `backend/tests/test_mdm/test_workflow_streamed_publication.py`; modify `workflow_finalize.py`, `workflow_cluster_engine.py`, `workflow_cluster_preview.py`, `workflow_partial.py`, `workflow_plan_cache.py`, `workflow_generation_reads.py`, `workflow_group_approvals.py`, `workflow_group_annotations.py`, `workflow_resolution.py`, `workflow_cluster_reads.py`, `golden_service.py`, `workflow_exports.py`, `workflow_assessment.py`, `workflow_assessment_reads.py`, `workflow_jobs.py`.

**Interfaces:**
- Consumes: Tasks 3–7; public routes/operation/revision contracts remain compatible.
- Produces: `finalize_generation(db, run, generation, *, actor) -> dict` and `open_finalized_generation(db, run)` context manager with `iter_groups()`, `record(rid)`, `coverage`, `result_hash`, policy/master-ID versions.
- New `WorkflowGeneration.finalized_result` holds a bounded manifest receipt (`version='p119_workflow_final_v2'`, artifact reference, counts, golden revision, entity-ID version), not all groups/records. Old JSON results remain validated with their old hash and ID semantics.
- Preview/publication/repair iterate the same group plan; output artifacts and local evidence are written in bounded batches. Summary payloads keep 25 prioritized conflict/review groups and 100 ID previews maximum. Membership rows keep batches at 500 clusters/2000 members. Large full coverage/unresolved IDs live in indexed artifacts, not operation result JSON.
- Export uses GenerationReader plus frozen final selections; disk-backed uniqueness/coverage checks replace `covered=[]` and giant sets. Contact output uses Task 1 `primary_values`, explicit choices remain effective. Incremental hash/readback and final revision/lease fence precede export registration.

- [ ] **Step 1: RED — end-to-end and failure-boundary tests.**

```python
assert preview_memberships() == published_memberships() == repair_preview_memberships()
assert finalized_receipt()['version'] == 'p119_workflow_final_v2'
assert 'clusters' not in finalized_receipt()
assert exported_membership_counts() == frozen_input_record_counts()
assert tampered_final_partition_export().status_code == 409
assert changed_decisions_after_preview().status_code == 409
```

Use real API/leased worker for review→preview→approval→partial/complete publication→
golden→confirmation→finalize→export→reopen→repair/apply/undo. Test shared contact does
not reappear in CSV, changed-membership override pending, entity IDs through split,
virtual singleton exception, stale policy/assessment/input/negative edge, cross-run
tokens, cancellation, attempt replacement and lease/deadline expiry at each boundary.
Inject failure after a publication batch: no partially advanced generation/confirmation
may be visible. Retry reuses sealed snapshot/IDs and does not duplicate entity/member rows.
New worker tests forbid full `load(CLUSTER)` and `digest(list(all_records))`; old generations
still have deliberate compatibility tests. GROUP_ANNOTATIONS also uses frozen view and
bounded lookup, and its failure never changes membership or confirmation.

- [ ] **Step 2: Run RED.** `.venv/Scripts/python.exe -B -X utf8 scripts/run_cluster_checks.py --suite workflow`.
- [ ] **Step 3: GREEN — connect producers and consumers.** Keep HTTP handlers queue-only. Prepare snapshot/assessment projections from DB streams, never from a full `query.all()` of records/pairs; the existing assessment must project current effective evidence and revisions into bounded indexes. Reuse region validation only under matching global inputs/budget. Finalize verifies coverage/choices against frozen generation and writes sealed final receipt. Keep run locks short around fencing/registration; artifact IO occurs outside the locks, with readback before authority is granted.
- [ ] **Step 4: Verify.** Workflow suite, full backend tests and existing lease/attempt/recovery/export/assessment tests. Changed-file Ruff and `git diff --check`. Migration checks must retain one head and legacy run rows.
- [ ] **Step 5: Checkpoint.** Record actual API/worker timeline, counts and failpoint results; no full-workflow PASS based only on mocks or StageRunner.

## Task 9: Unified Frontend Flow and Browser Transitions

**Files:** Modify `frontend/src/lib/workflowContracts.ts`, `workflowApi.ts`, `frontend/src/components/ClusterWorkspace.tsx`, `GoldenRecords.tsx`, `GoldenFieldEditor.tsx`, `GoldenResolution.tsx`, `ClusterPreviewDialog.tsx`, `PreviewGroups.tsx`, `PairClusterReview.tsx`, `GroupApprovalDialog.tsx`, `GroupMembersTable.tsx`, `PairCompletion.tsx`, `SessionWorkspace.tsx`, `GenerationStatus.tsx`; extend related Vitest tests and `frontend/scripts/verify-handoff-integration.mjs`.

**Interfaces:**
- Consumes: entity_id/identity version, paginated review groups, policy limits, virtual cluster IDs, pending_selections, immutable generation/read-only metadata and unchanged mutation revisions.
- Produces: frontend contracts accepting old responses and new optional fields; same actions and review ownership, with large-group paging and pending draft selection visible.
- Preserve normal cluster_id for mutation routing; entity_id is the stable identity displayed/exported. URL-encode virtual IDs. Persisted vs virtual storage is not a product action users must choose.

- [ ] **Step 1: RED — meaningful transition tests.** Add tests for 21/100/101 approval,
member on page two, shared-only primary blank with provenance alternatives, source/manual
choice preserved across refresh, pending override requiring explicit selection, virtual
singleton edit/confirm, partial coverage/blocked region navigation, stale preview and
operation failure/retry. Browser back/reload must not send stale review tokens or lose
unsaved edits silently.
- [ ] **Step 2: Run RED.** Relevant Vitest files with `npm.cmd --prefix frontend test -- <test filenames>`; confirm intended failures.
- [ ] **Step 3: GREEN — integrate contracts/UI.** Read local Next app-router/data-fetching guides first. Use server metadata for limits and readiness; never derive permission from displayed page contents or coverage alone. Explain pending choices in normal product language, retain existing permission and unsaved-change guards.
- [ ] **Step 4: Verify.** `npm.cmd --prefix frontend test`, `npm.cmd --prefix frontend run lint`, `npm.cmd --prefix frontend run build`; then browser against isolated real API/worker. Mock-browser tests remain supplementary and labeled.
- [ ] **Step 5: Checkpoint.** Record browser actions/network errors and exact source build hashes; screenshots are from the tested build, not an earlier version.

## Task 10: Complete Benchmark, Integration Review and Final Evidence

**User scope update (2026-10-14):** Run only one test case on the smallest original
dataset bundle available for that case, to save time. Do not run the previously
planned three 100k and two 1M benchmarks. Keep input integrity, isolated workflow,
quality/coverage metrics and regression checks. A small-bundle result does not
certify 100k/1M scale; record that limitation explicitly. The user also authorized
commit/push to Nam_Backup for colleague testing/review; no merge is authorized.

**Files:** Create `scripts/fetch_cluster_bundles.py`, `scripts/verify_cluster_workflow.py`, `backend/tests/test_mdm/test_cluster_benchmark_contract.py`, `docs/benchmark/cluster-quality-scale-20261013-final.md`; keep generated aggregates under `artifacts/cluster-improvements-20261013/` and link them from the report. Extend existing benchmark harnesses only where required; do not change source manifests.

**Interfaces:**
- Current scoped fetch: `fetch_cluster_bundles.py --case TC1_easy_match --scale 10k --output <owned cache>`: path validation, stream hash, fail on changed existing files; input files are accepted only when manifest hashes match. Previously recorded larger-bundle integrity problems remain historical evidence, not a reason to run another TC.
- Current scoped workflow: `verify_cluster_workflow.py --bundle <cache>/TC1_easy_match/10k/seed-7 --output <new directory> --review-mode <none|declared-fixtures>`: isolated DB/store, pinned backend code/input hashes, stage time/process high-water RSS/statement metrics and quality/coverage report. Complete config/environment/IO hashes and remaining metrics are still outstanding and must not be implied by the small-bundle checkpoint.
- `none` runs actual undecided evidence/partial publication and reports remaining review, without forcing FINALIZED. `declared-fixtures` uses explicit QA review operations defined from source evidence or separate permission fixtures, not ownership truth; it can exercise the complete workflow but is labeled simulated review and not production-quality certification. Truth is opened only for evaluation after predictions.
- Report candidate recall, pairwise/B³/exact-cluster metrics, cross-owner/JUNK co-clusters, explicit constraint violations, record coverage, abstention and budget/timeouts separately. Undefined precision remains null, not 100%.

- [ ] **Step 1: RED — benchmark/report contracts.** Tests must reject traversal/absolute
manifest paths, changed input/code during a run, truth passed to matcher/review strategy,
null JUNK owner treated as a common entity, zero predictions reported as perfect precision,
incomplete dataset labeled full PASS, missing export or mocked UI labeled real end-to-end,
and a candidate/guard budget failure silently omitted from denominators.
- [ ] **Step 2: Run RED.** Benchmark contract suite via the isolated runner; validate all 12 files declared by the TC1/10k manifest before execution.
- [ ] **Step 3: GREEN — harness and scoped benchmark.** Run only TC1/10k with full
original source data, ordinary assessment and explicitly labeled simulated review.
Retain the production candidate cap/profile; do not increase budgets. Record any
limit or incomplete workflow explicitly rather than reporting PASS. Measure actual
API/worker stages and record input coverage, export and quality metrics. Do not
download or run further 100k/1M cases under the narrowed user scope.
- [ ] **Step 4: Verify final integration.** Run full backend suite, frontend suite/typecheck/
production build, changed-file lint, diff check, migration upgrade/read-compatibility and
browser/API/worker transitions after the final edit. Run a focused final review against
all five Review Focus conditions; any review-driven fix gets a regression test and only
the affected checks repeated before final full verification. Follow requesting-code-review
and verification-before-completion skills; execution stays direct as selected by user.
- [ ] **Step 5: Record final outcome.** Link exact command logs, code/config/input hashes,
baseline/final timing and memory scopes, migrations required for deployment, API/export
contract versions, counts and any unresolved integrity/budget limitations. Do not report
1M or full workflow success unless that exact run completed. User receives the complete
reviewable checkpoint on Nam_Backup; the user has authorized commit/push for colleague
review. No merge, live migration, deployment or new Compose file. Incomplete Tasks 8–10
stay explicitly unchecked and their gaps appear in the checkpoint report.

## Plan Self-review and Approval State

Spec coverage: Tasks 1–2 cover golden/shared/recency/hash and repair discrepancy;
Tasks 3–4 cover component order, bridge approval, bounds, policy differences and regions;
Task 5 covers ID/choices; Task 6 covers singleton consumers; Tasks 7–8 cover bounded
storage and whole-workflow producers/consumers; Tasks 9–10 cover UI/transitions/data/scale.
Interfaces above define the shared names and bounds used by later tasks.

Execution method already selected: direct, sequential implementation in this session.
The user approved this written plan and instructed implementation to begin. Execute continuously; do not ask the execution-method question again.
