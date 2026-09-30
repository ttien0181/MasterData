# TÀI LIỆU BÀN GIAO DỮ LIỆU ĐẦU VÀO CHO AI ENGINEER (MASTERONE SYSTEM)

> **Mục đích tài liệu:** Hướng dẫn đội ngũ AI/ML Engineer tiếp nhận, hiểu rõ bản chất, cấu trúc schema và cách khai thác tập dữ liệu benchmark sinh bởi **VNER-Bench** để phục vụ việc xây dựng pipeline **Entity Resolution (ER)**, **Record Linkage**, và **Master Data Management (MDM)** trên hệ thống **MasterOne**.

---

## 1. Bối cảnh nghiệp vụ & Vai trò của dữ liệu

Hệ thống **MasterOne** có nhiệm vụ giải quyết bài toán: *Cùng một khách hàng/nhà cung cấp tồn tại nhiều bản ghi khác biệt nhỏ trên các hệ thống nguồn (CRM, ERP, Billing) của Tập đoàn X, gây trùng lặp và sai lệch báo cáo 360 độ*.

Bộ dữ liệu này được thiết kế và sinh ra theo chuẩn **Data-Centric AI**, đóng 2 vai trò song song cho AI Engineer:
1. **Dữ liệu giả lập sản xuất (Production Simulation Data):** Các bản ghi nguồn đa hệ thống (`records`) được đưa vào Database / Vector DB để Agent thực hiện quy trình Blocking $\rightarrow$ Pairwise Matching $\rightarrow$ Clustering $\rightarrow$ Golden Record $\rightarrow$ HITL Review.
2. **Bộ nhãn chuẩn đối soát (Gold-Standard Benchmark):** Chứa các cặp trùng thực sự (`is_match = 1`) và các ca bẫy cực khó (**Hard Negatives** - `is_match = 0`) giúp AI Engineer đo đạc chính xác Precision, Recall, F1-Score và phân tích điểm nghẽn của mô hình (Error Analysis).

---

## 2. Cách xuất dữ liệu cho MasterOne

Bạn có thể xuất dữ liệu dưới 2 định dạng: **CSV** (cho pipeline Pandas/Polars/Splink) hoặc **JSON** (cho API/LangGraph/Review UI).

### Cách 1: Xuất định dạng CSV (Khuyên dùng cho Backend Data Ingestion)
```powershell
# Thiết lập UTF-8 trên Windows PowerShell
$env:PYTHONUTF8 = "1"

# Xuất tập test ra thư mục chứa records.csv và pairs.csv
python -m vner_bench export-masterone --data data --split test --format csv --output outputs/masterone_test/
```
Kết quả tạo ra 2 file:
- `outputs/masterone_test/records.csv`: 4.213 bản ghi khách hàng đã map tên cột chuẩn MasterOne (`id`, `full_name`, `phone_number`, `raw_address`, `source_system`...).
- `outputs/masterone_test/pairs.csv`: 1.200 cặp nhãn đối soát chuẩn (960 cặp trùng + 240 ca bẫy Hard Negatives).

### Cách 2: Xuất định dạng JSON (Khuyên dùng cho LangGraph Payload / Web API)
```powershell
python -m vner_bench export-masterone --data data --split test --format json --output outputs/masterone_test.json
```

Hoặc nạp trực tiếp qua Python script trong codebase của bạn:

```python
from vner_bench.adapters.masterone import MasterOneAdapter

adapter = MasterOneAdapter("data")

# 1. Lấy toàn bộ records thuộc tập test (đã map sang schema MasterOne)
test_records = adapter.load_split_records("test")

# 2. Lấy bộ evaluation trọn gói (records + positive pairs + hard negatives)
eval_bundle = adapter.load_evaluation_dataset(split="test", include_hard_negatives=True)
print(f"Tổng records: {eval_bundle['record_count']}")
print(f"Tổng pairs đánh giá: {eval_bundle['pair_count']}")
```

---

## 3. Cấu trúc Schema chi tiết

File bàn giao (`records.csv` + `pairs.csv`, hoặc `masterone_test.json`) chứa các thông số sau:

| Thông số | Giá trị (tập test) |
| :--- | ---: |
| `record_count` | 4.213 |
| `pair_count` | 1.200 |
| `positive_pair_count` | 960 |
| `negative_pair_count` | 240 |
| Unique entities | 1.200 |
| Sources | CRM 1.408 / ERP 1.398 / BILL 1.407 |

### 3.1. Chi tiết mảng `records` (Bản ghi nguồn)

Mỗi object đại diện cho một bản ghi khách hàng từ một hệ sinh thái nguồn:

| Trường | Kiểu | Ý nghĩa nghiệp vụ | Gợi ý sử dụng cho AI Engineer |
| :--- | :--- | :--- | :--- |
| `id` | `string` | ID duy nhất của bản ghi (VD: `CRM_0000001`, `ERP_0000002`) | Khóa chính (Primary Key) trong Vector DB/Graph DB |
| `source_system` | `string` | Hệ thống nguồn (`CRM`, `ERP`, `BILL`) | Lineage tracking, ưu tiên khi merge Golden Record |
| `full_name` | `string` | Họ và tên khách hàng (đã có biến thể/nhiễu) | Dùng tính Text Similarity (Jaro-Winkler, Levenshtein, Embedding) |
| `phone_number` | `string` | Số điện thoại chuẩn hóa namespace `+84-000-*` | Rule-based matching / Blocking key |
| `email_address` | `string` | Email thuộc domain `*@example.test` | Exact match / Blocking key |
| `raw_address` | `string` | Chuỗi địa chỉ hoàn chỉnh (chứa viết tắt, lỗi gõ, hoặc địa chỉ cũ) | Chuẩn hóa địa chỉ + Embedding semantic match |
| `address_id` | `string` | Mã địa chỉ canonical (`ADDR000001`) | Truy vết vị trí vật lý; dùng khi cần join với bảng địa chỉ chuẩn |
| `entity_id` | `string` | Khóa thực thể gốc (`E000001`) | **CHỈ DÙNG ĐỂ CHẤM ĐIỂM / KHÔNG ĐƯA VÀO MODEL INPUT** |
| `noise_type` | `string` | Loại nhiễu được áp vào bản ghi (12 loại — xem mục 4) | Dùng phân tích điểm yếu (Error Analysis theo slice) |
| `difficulty` | `string` | Độ khó của bản ghi (`easy`, `medium`, `hard`, `extreme`) | Đo lường độ bền của mô hình (Robustness) |
| `temporal_case` | `string` | Trạng thái địa giới hành chính (`NEW_ADMIN`, `OLD_ADMIN`, `MIXED_NEW_COMMUNE_OLD_PROVINCE`, `MIXED_OLD_COMMUNE_NEW_PROVINCE`) | Đánh giá khả năng xử lý sáp nhập địa giới |

#### Ví dụ một bản ghi:
```json
{
  "id": "ERP_0000002",
  "entity_id": "E000002",
  "source_system": "ERP",
  "full_name": "Lê Thanh An",
  "phone_number": "+84-000-000002",
  "email_address": "e000002@example.test",
  "raw_address": "451 Đường Lê Văn Việt, Phường Tăng Nhơn Phú A, Thành phố Hồ Chí Minh",
  "address_id": "ADDR000001",
  "noise_type": "CLEAN",
  "difficulty": "easy",
  "temporal_case": "NEW_ADMIN"
}
```

---

### 3.2. Chi tiết mảng `pairs` (Nhãn chuẩn Ground Truth)

Mỗi object là một cặp bản ghi được đánh nhãn đối sánh:

| Trường | Kiểu | Ý nghĩa |
| :--- | :--- | :--- |
| `id_a`, `id_b` | `string` | ID của 2 bản ghi cần so khớp |
| `is_match` | `int` | **1** (Cùng 1 thực thể khách hàng) hoặc **0** (Khác thực thể) |
| `case_type` | `string` | Phân loại cặp (`NORMAL_DUPLICATE`, `NOISY_DUPLICATE`, `OLD_NEW_ADDRESS`, `MIXED_ADMIN`, `HARD_NEGATIVE`) |
| `difficulty` | `string` | Mức độ phức tạp của cặp so sánh (`easy`, `hard`, `extreme`) |
| `split` | `string` | Tập dữ liệu (`train`, `validation`, `test`) |
| `hard_negative_type` | `string` | Loại bẫy (chỉ có khi `is_match = 0`): `SAME_NAME_DIFFERENT_ADDRESS`, `SIMILAR_PHONE`, `SIMILAR_STREET`, `SAME_SURNAME_SAME_COMMUNE`, `RELATED_ADMIN_UNITS`, `SAME_ADDRESS_DIFFERENT_PERSON` |
| `entity_a`, `entity_b` | `string` | Entity ID của 2 bản ghi (chỉ có ở Hard Negatives, dùng để debug) |

#### Ví dụ cặp Positive (`is_match = 1`):
```json
{
  "id_a": "ERP_0000002",
  "id_b": "BILL_0000002",
  "is_match": 1,
  "case_type": "NOISY_DUPLICATE",
  "difficulty": "extreme",
  "split": "test"
}
```

#### Ví dụ cặp Hard Negative (`is_match = 0` - Bẫy model):
```json
{
  "id_a": "ERP_0000002",
  "id_b": "BILL_0000010",
  "is_match": 0,
  "case_type": "HARD_NEGATIVE",
  "difficulty": "extreme",
  "split": "test",
  "hard_negative_type": "SAME_NAME_DIFFERENT_ADDRESS",
  "entity_a": "E000002",
  "entity_b": "E000009"
}
```

---

## 4. Các đặc thù Tiếng Việt & Thử thách trong dữ liệu

AI Engineer cần đặc biệt lưu ý các dạng nhiễu đã được tích hợp sẵn:

1. **Biến đổi địa giới hành chính (Temporal Changes):**
   - Dữ liệu mô phỏng việc sáp nhập/chia tách đơn vị hành chính cấp Xã/Phường theo các mốc 2025–2026.
   - Cùng một khách hàng, trên CRM có thể lưu tên Phường mới (`NEW_ADMIN`), nhưng trên Billing lại lưu tên Xã/Phường cũ trước sáp nhập (`OLD_ADMIN`).
   - *Thách thức cho Agent:* Phải nhận biết được sự tương đương về mặt địa lý thông qua Knowledge Graph hoặc Geocoding/Semantic Embedding thay vì so khớp từ khóa cứng.

2. **Nhiễu văn bản thực tế tiếng Việt:**
   - **Gõ sai bộ gõ:** Lỗi Telex/VNI (`tr/ch`, `s/x`, thừa/thiếu phím gõ dấu).
   - **Viết tắt:** `Q.` (Quận), `P.` (Phường), `TP.` (Thành phố), `TT.` (Thị trấn), `TX.` (Thị xã), `Đ.` (Đường).
   - **Xáo trộn token:** Thứ tự họ tên hoặc thứ tự địa chỉ bị đảo lộn (VD: *"Hồ Chí Minh Thành phố"* vs *"TP. Hồ Chí Minh"*).
   - **Dính chữ / Mất khoảng trắng:** Lỗi thường gặp do trích xuất OCR hoặc form nhập liệu lỗi.

3. **Tập Hard Negatives (Adversarial Test):**
   - **Cùng tên khác người:** Trùng hoàn toàn Họ và Tên (VD: "Nguyễn Văn A"), nhưng một người ở Hà Nội, một người ở TP.HCM. Model nếu chỉ dùng trọng số Tên quá cao sẽ bị False Positive.
   - **Cùng nhà khác người:** Cùng địa chỉ, trùng Họ nhưng khác Tên (quan hệ gia đình).
   - **Số điện thoại/Email gần giống:** Chỉ khác 1-2 ký tự cuối (thường do lỗi nhập liệu của nhân viên bán hàng).

---

## 5. Hướng dẫn thiết kế Pipeline cho AI Engineer trên MasterOne

Dựa trên cấu trúc dữ liệu này, pipeline khuyến nghị cho AI Engineer triển khai trong LangGraph / FastAPI bao gồm:

```text
[Input Records] 
       ↓
[Phase 1: Blocking / Candidate Selection]
   - Sinh Blocking Key: Soundex tiếng Việt, Prefix Tỉnh/Thành, 3-gram Họ tên
   - Vector Search (Embedding Tên + Địa chỉ) lấy Top-K lân cận
       ↓
[Phase 2: Pairwise Matching (Hybrid Agent)]
   - Rule Agent: So khớp chính xác SĐT/Email, tính khoảng cách Levenshtein/Jaro-Winkler
   - Semantic Agent: So khớp embedding ngữ nghĩa tên và địa chỉ
   - Transition Graph Resolver: Tra cứu lịch sử sáp nhập xã/phường nếu địa chỉ lệch
       ↓
[Phase 3: Decision & HITL Steward]
   - Match Score >= 0.85: Tự động gộp vào Golden Record
   - 0.50 <= Match Score < 0.85: Đẩy vào hàng đợi HITL Review cho Data Steward kèm giải thích lý do
   - Match Score < 0.50: Coi là các cá thể riêng biệt
       ↓
[Phase 4: Golden Record Generation]
   - Áp dụng luật Survivorship: Lấy SĐT từ nguồn ERP, Địa chỉ từ nguồn CRM có timestamp mới nhất
```

---

## 6. Tiêu chí đánh giá & Metrics bắt buộc (Evaluation Protocol)

Khi nghiệm thu pipeline, AI Engineer cần chạy benchmark trên tập `test` và báo cáo các chỉ số:

1. **Blocking Metrics:**
   - **Pair Completeness (Recall của bước Blocking):** Tỷ lệ cặp trùng thật nằm trong tập ứng viên sau blocking (Yêu cầu: $\ge 98\%$).
   - **Reduction Ratio:** Tỷ lệ số phép so sánh giảm được so với tích Descartes toàn phần (Yêu cầu: $\ge 95\%$).

2. **Matching Metrics (Pairwise):**
   - **Precision, Recall, F1-Score:** Tính trên toàn bộ tập nhãn đối sánh.
   - **False Positive Rate trên tập Hard Negatives:** Tỷ lệ đoán sai trên các ca bẫy (Yêu cầu: $< 5\%$).

3. **Stratified Slice Metrics:**
   - F1-Score chi tiết theo từng nhóm độ khó: `easy`, `medium`, `hard`, `extreme`.
   - F1-Score trên nhóm `OLD_ADMIN` vs `NEW_ADMIN` (kiểm tra độ thông minh xử lý địa giới).

---

## 7. Lưu ý quan trọng về dữ liệu

### 7.1. Trường bị trống có chủ đích (Missing Fields by Design)

Trong `records.csv`, một số bản ghi **cố tình** để trống `phone_number` (3,6%) hoặc `email_address` (5,9%) nhằm mô phỏng thực tế doanh nghiệp (nhân viên không thu thập đủ thông tin). Agent của MasterOne phải xử lý được tình huống thiếu trường mà vẫn match đúng dựa trên các trường còn lại.

### 7.2. Trường Metadata (KHÔNG đưa vào Model Input)

Các trường sau **chỉ dùng cho mục đích chấm điểm và phân tích**, KHÔNG được đưa vào features của model matching:
- `entity_id`: Đáp án thực thể gốc.
- `noise_type`, `difficulty`, `temporal_case`: Metadata chẩn đoán để làm Error Analysis.
- `address_id`: Mã địa chỉ canonical, chỉ dùng khi cần truy vết.

### 7.3. Quy tắc bảo mật & Data Governance

- **PII Safety:** Toàn bộ họ tên, số điện thoại (`+84-000-*`), email (`*@example.test`) trong bộ dữ liệu này đều là **dữ liệu tổng hợp (synthetic)** hoặc địa chỉ công cộng (OSM/hành chính). Không chứa thông tin khách hàng thật của Tập đoàn X, hoàn toàn an toàn khi test trên local và deploy Docker/Cloud Run.
- **Data Leakage Prevention:** Dữ liệu đã được chia tập (`train`, `validation`, `test`) nghiêm ngặt theo `entity_id`. Tuyệt đối không để rò rỉ bất kỳ `entity_id` nào từ tập train sang tập test.
