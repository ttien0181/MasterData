# Kế hoạch cải thiện MasterOne ER — Hướng dẫn cho AI Agent

> Tài liệu tham chiếu: `OneMaster_ER_Technical_Spec.md`
> Mỗi task là 1 đơn vị công việc độc lập, có file đầu vào/đầu ra rõ ràng.
> Agent thực thi PHẢI đọc file spec tương ứng (section §) trước khi code.

---

## PHASE 0 — Sửa lỗi runtime (ưu tiên cao nhất)

### TASK-0.1: Bổ sung field declarations thiếu trong Settings

**Vấn đề**: `backend/src/masterone/llm/config.py` → `LLMSettings` đọc ~20 field từ `Settings` (ví dụ `LLM_ENABLED`, `LLM_SEMANTIC_ENABLED`, `OPENROUTER_API_KEY`...) nhưng class `Settings` tại `backend/src/masterone/config.py` KHÔNG khai báo các field này. Test bypass bằng `model_construct()`, production sẽ crash.

**Hành động**:
1. Đọc `backend/src/masterone/llm/config.py` → hàm `build_llm_settings()` để xác định TẤT CẢ field name mà nó đọc từ Settings
2. Thêm các field vào class `Settings` tại `backend/src/masterone/config.py` với default value hợp lý (disabled by default)
3. Chạy `pytest backend/tests/` để verify không break test hiện tại

**File sửa**: `backend/src/masterone/config.py`
**File tham chiếu**: `backend/src/masterone/llm/config.py`

---

### TASK-0.2: Xóa broken imports

**Vấn đề**: Một số module import model không tồn tại: `ResolutionEntity`, `ResolutionHistory`, `ResolutionRequestKey`, `SourceRecord`, `Candidate`, `GoldenRecordDraft`. Import sẽ fail khi module được load.

**Hành động**:
1. Grep toàn bộ `backend/src/` tìm các import name trên
2. Xác nhận model không tồn tại trong `backend/src/masterone/mdm/models.py`
3. Xóa dòng import và code sử dụng chúng (nếu là dead code)
4. Nếu code CÓ sử dụng → tạo model stub hoặc refactor logic

**Lệnh tìm**:
```bash
grep -rn "ResolutionEntity\|ResolutionHistory\|ResolutionRequestKey\|SourceRecord\|Candidate\|GoldenRecordDraft" backend/src/
```

---

## PHASE 1 — Chuẩn hóa dữ liệu (STG-02)

### TASK-1.1: Vietnamese Name Tokenization

**Spec**: §STG-02-FR-020, §STG-02-FR-021
**Vấn đề**: Hiện tại `standardization.py` chỉ fold ASCII cho name, KHÔNG tách surname/middle_name/given_name.
**Tại sao cần**: Policy A3 yêu cầu `given_name_compatible` check (§18). Không tách tên → không thể implement A3.

**Hành động**:
1. Thêm hàm `tokenize_vietnamese_name(name: str) -> dict` vào `backend/src/masterone/mdm/pipeline/standardization.py`
2. Logic:
   - Input: full name string (đã NFC + lowercase)
   - Split tokens bằng whitespace
   - Token đầu = `surname` (họ)
   - Token cuối = `given_name` (tên)
   - Tokens giữa = `middle_name` (tên đệm)
   - Xử lý edge case: tên 1 token (surname = given = token đó), tên 2 token (surname + given, middle rỗng)
3. Gọi hàm này trong `normalize_record()`, lưu kết quả vào `normalized_fields["_name_parts"]`
4. Viết test: tên phổ biến VN ("Nguyễn Văn An", "Trần Thị Bích Ngọc", "Lê A")

**File sửa**: `backend/src/masterone/mdm/pipeline/standardization.py`
**File test**: `backend/tests/mdm/pipeline/test_standardization.py` (thêm test mới)

---

### TASK-1.2: given_name_compatible cho subgroup A3

**Spec**: §18 (A3 definition), §STG-04-FR-008
**Phụ thuộc**: TASK-1.1 hoàn thành

**Hành động**:
1. Thêm hàm `given_name_compatible(parts_a: dict, parts_b: dict) -> bool` vào `matching.py`
   - True nếu: given_name EX hoặc NX (sau ASCII fold), HOẶC one is initial of other
   - False nếu: given_name khác nhau hoàn toàn
2. Cập nhật `_name_state()` trong `matching.py` để trả thêm flag `given_name_ok`
3. Cập nhật policy A3 trong `policy_engine.py` để check flag này
4. Viết test

**File sửa**: `backend/src/masterone/mdm/pipeline/matching.py`, `backend/src/masterone/mdm/pipeline/policy_engine.py`

---

### TASK-1.3: Thay SequenceMatcher bằng rapidfuzz

**Spec**: A-64 (Jaro-Winkler cho name, Levenshtein cho address)
**Vấn đề**: `matching.py` dùng `difflib.SequenceMatcher` — chậm và không đúng metric.

**Hành động**:
1. Thêm `rapidfuzz` vào `backend/requirements.txt` (hoặc pyproject.toml)
2. Trong `matching.py`:
   - Thay `_similarity()` cho name bằng `rapidfuzz.distance.JaroWinkler.similarity()`
   - Thay `_similarity()` cho address bằng `rapidfuzz.distance.Levenshtein.normalized_similarity()`
   - Giữ nguyên threshold values hiện tại (0.85, 0.7, 0.5)
3. Chạy tất cả test regression T1–T28 — nếu fail thì điều chỉnh threshold

**File sửa**: `backend/src/masterone/mdm/pipeline/matching.py`
**File thêm dependency**: `backend/requirements.txt`

---

### TASK-1.4: Multi-value phone/email

**Spec**: §STG-02-FR-030
**Vấn đề**: Mỗi record chỉ lưu 1 phone, 1 email. Spec yêu cầu `list<ContactValue>`.

**Hành động**:
1. Cập nhật `normalize_record()` trong `standardization.py`:
   - Nếu raw field chứa `;` hoặc `,` → split thành list
   - Chuẩn hóa từng giá trị, lưu `normalized_fields["phones"]` = list[dict] với mỗi dict có `value`, `quality`, `is_primary`
   - Giữ backward compat: `normalized_fields["phone"]` = giá trị đầu tiên valid
2. Cập nhật `blocking.py`: block trên MỖI phone/email trong list
3. Cập nhật `matching.py` `_contact_state()`: so sánh list-vs-list, trả EX nếu BẤT KỲ pair match

**File sửa**: `standardization.py`, `blocking.py`, `matching.py`

---

### TASK-1.5: Org name normalization

**Spec**: §STG-02-FR-022
**Vấn đề**: Không có xử lý riêng cho tên tổ chức (bỏ "TNHH", "Cổ phần", "Co., Ltd"...).

**Hành động**:
1. Thêm hàm `normalize_org_name(name: str) -> dict` vào `standardization.py`
   - Bỏ legal suffixes: "TNHH", "Cổ Phần", "CP", "Co., Ltd", "JSC", "LLC", "Inc"
   - Trả `{"org_name_core": "...", "legal_form": "TNHH"}`
2. Gọi khi `entity_type == "ORGANIZATION"` trong `normalize_record()`
3. Viết test

**File sửa**: `backend/src/masterone/mdm/pipeline/standardization.py`

---

### TASK-1.6: Mở rộng address_mapping

**Spec**: §STG-02-FR-052
**Vấn đề**: `address_mapping.py` chỉ có 8 entry (Hà Nội Ba Đình). Cần mapping cho toàn quốc.

**Hành động**:
1. Tạo file data `backend/src/masterone/mdm/data/vietnam_admin_mapping.json` chứa mapping xã/phường cũ → mới cho 63 tỉnh
   - Nguồn: Nghị quyết sáp nhập đơn vị hành chính các năm (có thể hard-code các trường hợp phổ biến: Hà Nội, HCM, Đà Nẵng, Hải Phòng trước)
   - Format: `{"old_name": "new_name", "old_district": "new_district"}`
2. Cập nhật `address_mapping.py` để load từ file JSON thay vì hardcode dict
3. Thêm field `mapping_version` vào kết quả normalize

**File sửa**: `backend/src/masterone/mdm/pipeline/address_mapping.py`
**File tạo mới**: `backend/src/masterone/mdm/data/vietnam_admin_mapping.json`

---

## PHASE 2 — Blocking nâng cao (STG-03)

### TASK-2.1: Government ID blocking

**Spec**: §STG-03-FR-002
**Vấn đề**: `blocking.py` KHÔNG block trên government_id — bỏ lỡ duplicate rõ ràng nhất.

**Hành động**:
1. Trong `generate_candidates()` tại `blocking.py`, thêm pass:
   ```python
   gov_id = fields.get("government_id", "")
   gov_quality = quality.get("government_id", "MISSING")
   if gov_id and gov_quality == "VALID":
       blocks[("exact_gov_id", gov_id)].add(record_id)
   ```
2. Bucket limit cho exact_gov_id = 20 (giống exact_phone)
3. Viết test: 2 record cùng CCCD → phải thành candidate

**File sửa**: `backend/src/masterone/mdm/pipeline/blocking.py`

---

### TASK-2.2: Composite blocking keys

**Spec**: §STG-03-FR-004
**Vấn đề**: Chỉ có single-field blocking. Spec yêu cầu composite keys cho recall cao hơn.

**Hành động**:
1. Thêm composite pass vào `generate_candidates()`:
   - `name_prefix(3) + birth_year` → limit 30
   - `surname_ascii + birth_year` → limit 30 (cần TASK-1.1 cho surname)
   - `name_ascii + province_key` → limit 40
   - `phone_suffix(4) + birth_year` → limit 20
2. Mỗi composite key chỉ thêm khi CẢ HAI thành phần đều có giá trị VALID

**File sửa**: `backend/src/masterone/mdm/pipeline/blocking.py`
**Phụ thuộc**: TASK-1.1 (cho surname extraction)

---

### TASK-2.3: TF-IDF candidate generation

**Spec**: §STG-03-FR-006 đến §STG-03-FR-009
**Vấn đề**: Chỉ có character trigram blocking, không có TF-IDF scoring.

**Hành động**:
1. Tạo file mới `backend/src/masterone/mdm/pipeline/tfidf_blocking.py`
2. Implement:
   - Build character n-gram (3,4) TF-IDF matrix bằng `sklearn.feature_extraction.text.TfidfVectorizer`
   - Partition theo `birth_year` hoặc `province` trước khi build matrix
   - Cosine similarity top-K per record (K=20)
   - `max_df=0.3` để loại n-gram quá phổ biến
   - Posting cap = 1000 per n-gram
3. Integrate vào `generate_candidates()` — kết quả TF-IDF merge với kết quả blocking thông thường
4. Thêm `scikit-learn` vào dependencies

**File tạo mới**: `backend/src/masterone/mdm/pipeline/tfidf_blocking.py`
**File sửa**: `blocking.py` (import và merge)

---

## PHASE 3 — Scoring nâng cao (STG-04)

### TASK-3.1: Verified identity hard constraint

**Spec**: §11.4
**Vấn đề**: `hard_constraints.py` có comment "future" cho verified phone/email conflict.

**Hành động**:
1. Trong `hard_constraints.py`, thêm constraint:
   - Nếu phone/email state = CF VÀ ít nhất 1 bên có `verified=True` → HARD_VERIFIED_PHONE / HARD_VERIFIED_EMAIL
2. Cập nhật `normalized_fields` schema để hỗ trợ `_phone_verified`, `_email_verified` flags
3. Cập nhật `check_all()` return type để include constraint mới
4. Viết test

**File sửa**: `backend/src/masterone/mdm/pipeline/hard_constraints.py`

---

### TASK-3.2: Conflict rule versioning

**Spec**: §STG-04-FR-006
**Vấn đề**: Không có `conflict_rule_version` trong comparison result.

**Hành động**:
1. Định nghĩa constant `CONFLICT_RULE_VERSION = "v3.1.0"` trong `matching.py`
2. Thêm field `"conflict_rule_version"` vào dict trả về của `score_pair()`
3. Lưu vào `ResolutionPair` model (thêm column `conflict_rule_version` String(20))
4. Migration Alembic mới

**File sửa**: `matching.py`, `models.py`
**File tạo mới**: migration Alembic

---

## PHASE 4 — Policy & Config Externalization

### TASK-4.1: Policy từ YAML file

**Spec**: §4.5.5, §SVC-06
**Vấn đề**: Policy 12 subgroups hardcoded trong `policy_engine.py`. Spec yêu cầu file config có version.

**Hành động**:
1. Tạo file `backend/src/masterone/mdm/config/policy_person_v3.yaml`:
   ```yaml
   version: "person-v3"
   subgroups:
     A1:
       min_match_weight: 10.5
       auto_merge_enabled: true
       fields:
         name: {states: [EX, NX], required: true}
         phone: {states: [EX, NX], required: true}
       # ... (migrate từ SUBGROUP_POLICIES dict hiện tại)
     A2:
       ...
   ```
2. Cập nhật `policy_engine.py`:
   - Load policy từ YAML file thay vì hardcode dict
   - Cache kết quả load
   - Validate schema khi load
   - Giữ `SUBGROUP_POLICIES` dict hiện tại làm fallback/default
3. Thêm `policy_version` vào mọi pair decision output
4. Viết test: load YAML → apply → kết quả giống hardcoded version

**File tạo mới**: `backend/src/masterone/mdm/config/policy_person_v3.yaml`
**File sửa**: `backend/src/masterone/mdm/pipeline/policy_engine.py`

---

### TASK-4.2: Subgroup predicate config

**Spec**: §STG-05-FR-009
**Vấn đề**: Subgroup predicates (A1/A2/A3...) hardcoded bằng if-elif chain.

**Hành động**:
1. Trong file YAML policy (TASK-4.1), thêm mục `predicates`:
   ```yaml
   predicates:
     A1:
       required_states: {name: [EX,NX], phone: [EX,NX]}
       forbidden_states: {government_id: [CF]}
     A2:
       required_states: {name: [EX,NX], email: [EX,NX]}
       ...
   ```
2. Viết `SubgroupClassifier` class đọc predicates từ config và match
3. Thay thế if-elif chain trong `_classify_subgroup()` hiện tại

**File sửa**: `policy_engine.py`, YAML config

---

## PHASE 5 — Database Schema mở rộng (STG-07 / §6.3)

### TASK-5.1: Entity + Entity Members tables

**Spec**: §6.3 — `entities`, `entity_members`, `entity_versions`
**Vấn đề**: Entity chỉ tồn tại tạm thời trong export snapshot, không persist.

**Hành động**:
1. Thêm 3 model vào `models.py`:
   ```python
   class ResolutionEntity(Base):
       __tablename__ = "mdm_resolution_entities"
       id: Mapped[str]  # entity_id
       run_id: Mapped[str]  # FK → runs
       version: Mapped[int]
       status: Mapped[str]  # ACTIVE, SPLIT, MERGED
       created_at: Mapped[datetime]

   class ResolutionEntityMember(Base):
       __tablename__ = "mdm_resolution_entity_members"
       entity_id: Mapped[str]  # FK → entities
       record_id: Mapped[str]  # FK → records
       added_at: Mapped[datetime]
       added_by: Mapped[str]  # "SYSTEM" | user_id

   class ResolutionEntityVersion(Base):
       __tablename__ = "mdm_resolution_entity_versions"
       entity_id: Mapped[str]
       version: Mapped[int]
       golden_fields: Mapped[dict]  # JSON
       changed_by: Mapped[str]
       created_at: Mapped[datetime]
   ```
2. Tạo Alembic migration
3. Cập nhật `resolution_service.py` → sau clustering, persist entities vào DB
4. Cập nhật `golden.py` → lưu golden fields vào `entity_versions`

**File sửa**: `models.py`, `resolution_service.py`, `golden.py`
**File tạo mới**: migration Alembic

---

### TASK-5.2: Negative edges table

**Spec**: §6.3 — `negative_edges`
**Vấn đề**: Negative edge hiện lưu qua `ResolutionPair.user_decision = "NON_MATCH"`, không tách bảng.

**Hành động**:
1. Thêm model:
   ```python
   class ResolutionNegativeEdge(Base):
       __tablename__ = "mdm_resolution_negative_edges"
       id: Mapped[str]
       left_record_id: Mapped[str]
       right_record_id: Mapped[str]
       created_by: Mapped[str]  # user_id hoặc "SYSTEM"
       reason: Mapped[str]  # "STEWARD_REJECT" | "HARD_CONSTRAINT" | "SPLIT"
       active: Mapped[bool]
       created_at: Mapped[datetime]
   ```
2. Migration Alembic
3. Cập nhật `resolution_review.py`: khi reject pair → insert negative edge
4. Cập nhật `resolution_split.py`: khi split → insert negative edge
5. Cập nhật `resolution_service.py`: load negative edges từ bảng mới thay vì query pairs

**File sửa**: `models.py`, `resolution_review.py`, `resolution_split.py`, `resolution_service.py`

---

### TASK-5.3: Golden field values table

**Spec**: §6.3 — `golden_field_values`
**Vấn đề**: Golden record fields lưu JSON blob trong proposal, không có bảng riêng.

**Hành động**:
1. Thêm model:
   ```python
   class ResolutionGoldenFieldValue(Base):
       __tablename__ = "mdm_resolution_golden_field_values"
       entity_id: Mapped[str]
       version: Mapped[int]
       field_name: Mapped[str]
       display_value: Mapped[str]
       source_record_id: Mapped[str]
       provenance: Mapped[str]  # SOURCE_PRIORITY | STEWARD_OVERRIDE | QUALITY_TIER
   ```
2. Migration Alembic
3. Cập nhật `golden.py` → build golden record output cũng persist vào bảng
4. API endpoint `GET /entities/{entity_id}/golden` đọc từ bảng

**File sửa**: `models.py`, `golden.py`
**File tạo mới**: migration, API endpoint

---

## PHASE 6 — API hoàn thiện

### TASK-6.1: PII Masking theo role

**Spec**: §SVC-01-FR-002
**Vấn đề**: Mọi role thấy đầy đủ PII. Viewer phải chỉ thấy phone 3 số cuối, email ký tự đầu.

**Hành động**:
1. Tạo `backend/src/masterone/mdm/pii_masking.py`:
   ```python
   def mask_phone(phone: str, role: str) -> str:
       if role in ("VIEWER",):
           return "***" + phone[-3:] if len(phone) >= 3 else "***"
       return phone

   def mask_email(email: str, role: str) -> str:
       if role in ("VIEWER",):
           local, domain = email.split("@", 1)
           return local[0] + "***@" + domain
       return email

   def mask_record(record: dict, role: str) -> dict:
       # Deep copy, mask phone/email/gov_id fields
   ```
2. Inject masking vào tất cả API response trả record data
3. Viết test: Viewer nhận masked, Steward nhận full

**File tạo mới**: `backend/src/masterone/mdm/pii_masking.py`
**File sửa**: `backend/src/masterone/api/mdm_resolution.py`

---

### TASK-6.2: Four-eyes approval cho Group D

**Spec**: §SVC-02-FR-006
**Vấn đề**: Mọi pair chỉ cần 1 reviewer. Group D (conflict) cần 2 reviewer độc lập.

**Hành động**:
1. Thêm columns vào `ResolutionReviewCase`:
   - `second_reviewer_id: Mapped[str | None]`
   - `second_decision: Mapped[str | None]`
   - `second_decided_at: Mapped[datetime | None]`
2. Cập nhật review logic trong `resolution_review.py`:
   - Nếu case.group == "D": sau reviewer 1 quyết định → status = "PENDING_SECOND_REVIEW"
   - Reviewer 2 (khác reviewer 1) mới được approve
   - Hai decision phải agree → mới finalize
3. Migration Alembic
4. Cập nhật frontend `PairReviewFlow` component

**File sửa**: `models.py`, `resolution_review.py`, frontend component
**File tạo mới**: migration

---

### TASK-6.3: Benchmark API endpoint

**Spec**: §SVC-07
**Vấn đề**: `benchmark.py` có logic nhưng không expose qua API.

**Hành động**:
1. Thêm endpoint `POST /api/mdm/resolution/{run_id}/benchmark` vào `mdm_resolution.py`:
   - Yêu cầu role POLICY_ADMIN hoặc AUDITOR
   - Input: `ground_truth_field` (tên column chứa benchmark_entity_id, default "benchmark_entity_id")
   - Gọi `benchmark.py` → trả JSON kết quả 5 level
2. Thêm endpoint `GET /api/mdm/resolution/{run_id}/benchmark/latest` → cached result

**File sửa**: `backend/src/masterone/api/mdm_resolution.py`

---

### TASK-6.4: Policy Admin simulate endpoint

**Spec**: §SVC-06
**Vấn đề**: Không có endpoint simulate thay đổi policy.

**Hành động**:
1. Endpoint `POST /api/mdm/resolution/policies/simulate`:
   - Input: policy YAML mới (hoặc diff) + run_id
   - Re-run `apply_policy()` cho tất cả pairs trong run với policy mới
   - Trả so sánh: bao nhiêu pair thay đổi decision (MERGE→REVIEW, REVIEW→MERGE, v.v.)
   - KHÔNG thay đổi dữ liệu thật — chỉ simulate
2. Role required: POLICY_ADMIN

**File sửa**: `backend/src/masterone/api/mdm_resolution.py`, `policy_engine.py` (thêm hàm simulate)

---

## PHASE 7 — Infrastructure

### TASK-7.1: Abstract ObjectStore interface

**Spec**: A-02
**Vấn đề**: Chỉ có `R2ObjectStore` concrete, không có ABC. Khó test và swap backend.

**Hành động**:
1. Tạo ABC trong `backend/src/masterone/storage/object_store.py`:
   ```python
   from abc import ABC, abstractmethod

   class ObjectStore(ABC):
       @abstractmethod
       def upload_file(self, path, key, content_type="application/octet-stream"): ...
       @abstractmethod
       def download_file(self, key, path): ...
       @abstractmethod
       def exists(self, key) -> bool: ...
       @abstractmethod
       def presigned_url(self, key, expires_in=3600) -> str: ...
       @abstractmethod
       def delete(self, key): ...
   ```
2. `R2ObjectStore` kế thừa `ObjectStore`
3. Tạo `LocalObjectStore` cho dev/test (lưu local filesystem)
4. Cập nhật tất cả chỗ dùng `R2ObjectStore` → inject `ObjectStore` qua config

**File sửa**: `backend/src/masterone/storage/object_store.py`
**File tạo mới**: `backend/src/masterone/storage/local_store.py`

---

### TASK-7.2: Structured JSON logging

**Spec**: §XC-OBS-006
**Vấn đề**: Logging dùng format text thường, không structured JSON.

**Hành động**:
1. Thêm `python-json-logger` dependency
2. Tạo `backend/src/masterone/logging_config.py`:
   - JSON formatter với fields: `ts`, `level`, `logger`, `message`, `run_id`, `stage`, `event`
   - PII filter: mask phone/email trước khi log
3. Cập nhật `main.py` / app startup để configure logging
4. Cập nhật resolution_service.py, pipeline modules → dùng structured logger

**File tạo mới**: `backend/src/masterone/logging_config.py`
**File sửa**: `backend/src/masterone/main.py`

---

### TASK-7.3: Config fail-fast validation

**Spec**: §8.2
**Vấn đề**: App không crash sớm khi thiếu config bắt buộc trong production.

**Hành động**:
1. Trong `config.py`, thêm `@model_validator(mode="after")`:
   ```python
   @model_validator(mode="after")
   def validate_production(self):
       if self.app_env == "production":
           assert self.database_url, "DATABASE_URL required in production"
           assert self.auth_csrf_secret, "AUTH_CSRF_SECRET required in production"
           if self.r2_endpoint_url:
               assert self.r2_access_key_id, "R2 credentials incomplete"
       return self
   ```
2. Viết test: production config thiếu field → raise, development OK

**File sửa**: `backend/src/masterone/config.py`

---

## PHASE 8 — Pipeline Architecture (Nâng cấp lớn, cần thận trọng)

### TASK-8.1: Stage Runner abstraction

**Spec**: §4.0.1, §XC-RUN-001
**Vấn đề**: Pipeline là 1 hàm `_compute()` monolithic. Cần tách thành 7 stage có thể chạy riêng và resume.

**Hành động**:
1. Tạo `backend/src/masterone/mdm/pipeline/stage_runner.py`:
   ```python
   @dataclass
   class StageContext:
       run_id: str
       attempt_id: str
       stage_name: str  # PREPARE|NORMALIZE|BUILD_CANDIDATES|SCORE|DECIDE|CLUSTER|MATERIALIZE
       config_hash: str
       input_artifacts: list[str]
       output_dir: Path

   class StageRunner:
       def run(self, ctx: StageContext) -> StageResult:
           # Check if already completed (idempotent)
           # Run stage logic
           # Write output artifact
           # Return StageResult with metrics
   ```
2. Tách `_compute()` thành 7 hàm riêng, mỗi hàm nhận input từ stage trước
3. Giữ `_compute()` hiện tại làm shortcut gọi tuần tự 7 stage (backward compat)
4. Mỗi stage ghi `StageResult` vào DB (new model `ResolutionStageResult`)

**File tạo mới**: `backend/src/masterone/mdm/pipeline/stage_runner.py`
**File sửa**: `resolution_service.py`

**LƯU Ý**: Task này phức tạp, agent cần đặc biệt cẩn thận không break pipeline hiện tại. Giữ backward compat bằng cách `_compute()` vẫn chạy được nhưng internally gọi 7 stage.

---

### TASK-8.2: Parquet artifact output

**Spec**: §4.0.2, §XC-DUR-001
**Phụ thuộc**: TASK-8.1

**Hành động**:
1. Mỗi stage output 1 Parquet file (dùng `parquet_io.py` đã có):
   - NORMALIZE → `normalized_records.parquet`
   - BUILD_CANDIDATES → `candidate_pairs.parquet`
   - SCORE → `scored_pairs.parquet`
   - DECIDE → `pair_decisions.parquet`
   - CLUSTER → `clusters.parquet`
   - MATERIALIZE → `entities.parquet`, `golden_records.parquet`
2. Mỗi artifact có SHA-256 checksum, ghi vào manifest
3. Output vẫn persist vào PostgreSQL (song song), Parquet chỉ là artifact durability layer

**File sửa**: `stage_runner.py`, `parquet_io.py`, `manifest.py`

---

## PHASE 9 — Frontend bổ sung

### TASK-9.1: Entity view + history

**Spec**: §SVC-01 — `/entities/{entity_id}`, `/entities/{entity_id}/history`
**Phụ thuộc**: TASK-5.1

**Hành động**:
1. Backend: endpoint GET `/api/mdm/resolution/entities/{entity_id}` → entity + members + golden
2. Backend: endpoint GET `/api/mdm/resolution/entities/{entity_id}/history` → entity versions timeline
3. Frontend: `EntityDetail` component hiển thị members, golden fields, provenance, version history

---

### TASK-9.2: Policy Admin UI

**Spec**: §SVC-06
**Phụ thuộc**: TASK-4.1, TASK-6.4

**Hành động**:
1. Frontend page cho Policy Admin:
   - Xem policy YAML hiện tại
   - Editor để chỉnh thresholds
   - Nút "Simulate" → gọi simulate endpoint → hiển thị impact analysis
   - Nút "Apply" → save YAML mới, bump version
2. Chỉ hiện cho role POLICY_ADMIN

---

## PHASE 10 — Test Coverage cho các feature mới

### TASK-10.1: Integration test end-to-end

**Spec**: §TC-E2E-01, §TC-E2E-02
**Phụ thuộc**: Tất cả phase trên

**Hành động**:
1. Tạo `backend/tests/mdm/test_e2e_pipeline.py`:
   - Load 10 example records từ spec §46 (KT examples)
   - Chạy full pipeline
   - Assert: đúng clusters, đúng golden records, đúng review cases
2. Chạy qua tất cả 7 stage riêng biệt
3. Verify Parquet artifacts readable và checksum match

---

### TASK-10.2: Bounded memory test

**Spec**: §TC-STG01-04
**Hành động**:
1. Test với 10K synthetic records
2. Monitor RSS peak
3. Assert RSS < configurable limit (e.g. 512MB)
4. Dùng `tracemalloc` hoặc `resource` module

---

## THỨ TỰ THỰC HIỆN (dependency graph)

```
PHASE 0 (bug fixes)     ← Làm NGAY, không phụ thuộc gì
  ├── TASK-0.1
  └── TASK-0.2

PHASE 1 (standardization) ← Làm song song với Phase 0
  ├── TASK-1.1 (name tokenize) ← Nền tảng cho 1.2, 2.2
  ├── TASK-1.2 (given_name)    ← cần 1.1
  ├── TASK-1.3 (rapidfuzz)     ← Độc lập
  ├── TASK-1.4 (multi-value)   ← Độc lập
  ├── TASK-1.5 (org name)      ← Độc lập
  └── TASK-1.6 (address map)   ← Độc lập

PHASE 2 (blocking)        ← cần 1.1
  ├── TASK-2.1 (gov_id)    ← Độc lập
  ├── TASK-2.2 (composite) ← cần 1.1
  └── TASK-2.3 (TF-IDF)   ← Độc lập

PHASE 3 (scoring)          ← Độc lập
PHASE 4 (policy config)    ← Độc lập
PHASE 5 (DB schema)        ← Độc lập
PHASE 6 (API)              ← cần Phase 5
PHASE 7 (infrastructure)   ← Độc lập
PHASE 8 (pipeline arch)    ← cần Phase 5, 7
PHASE 9 (frontend)         ← cần Phase 5, 6
PHASE 10 (tests)           ← cần tất cả
```

## QUY TẮC CHO AI AGENT THỰC THI

1. **Đọc spec trước code**: Mỗi task ghi section § tham chiếu → đọc section đó trong `OneMaster_ER_Technical_Spec.md` trước khi viết code
2. **Chạy test sau mỗi task**: `pytest backend/tests/ -x` — KHÔNG merge nếu test fail
3. **Backward compatible**: Giữ signature hiện tại của hàm public, thêm parameter mới với default value
4. **Không xóa test cũ**: Chỉ thêm test mới hoặc cập nhật assertion
5. **Commit mỗi task**: 1 task = 1 commit, message format `feat(mdm): TASK-X.Y — mô tả ngắn`
6. **Type hints**: Mọi function mới phải có type hints đầy đủ
7. **Không thêm dependency không cần thiết**: Chỉ thêm khi task yêu cầu rõ ràng (rapidfuzz, scikit-learn, pyyaml)
