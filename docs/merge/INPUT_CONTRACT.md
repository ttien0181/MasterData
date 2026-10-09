# P-119 input contract v1

Ngày: 2026-10-08. Phạm vi: CSV intake, resolver, import storage và migration 0021;
không thay candidate generation, scorer, frontend hoặc schema export.

## Cấu hình và mapping

[`input_contract_v1.json`](../../backend/src/masterone/mdm/input_contract_v1.json)
được đóng gói cùng backend. Version `p119_input_v1`, resolver
`p119_source_schema_v1`; cấu hình mới cần version mới, không sửa ý nghĩa version
đã dùng cho dữ liệu production. Provenance lưu version, SHA-256 của cấu hình
JSON canonical, logical-field -> original-header mapping và unknown fields.

Các trường bắt buộc là `source_record_id`, `source_system`, `name`; contacts,
address, DOB và government ID có thể thiếu. Giữ sources CRM/ERP/BILL/CSV và các
alias LINH/LEGACY/CRM/ERP/BILL; bổ sung aliases OneMaster, gồm `contact_email`,
`date_of_birth`, `account_holder`, `notification_phone`, `cccd`, `cmnd`.
Không chọn cột normalized làm raw input.

Header được so sánh bằng Unicode NFKC + strip + casefold. Header trùng chính xác,
collision Unicode, header rỗng, prefix `raw__` và mapping mơ hồ đều bị từ chối.
Hai alias cùng logical field là lỗi, không âm thầm chọn priority. Ngoại lệ lineage
có chủ đích: `source_record_id` là authoritative khi có thêm `id`/`record_id`;
các cột auxiliary vẫn được giữ nguyên trong original/extra fields.

Unknown fields không bị loại bỏ: lưu trong `extra_fields` và liệt kê ở
`profile.files[].mapping_report.unknown_fields`. Alias đã map không được coi là
unknown. CSV đọc bằng Python csv strings, UTF-8/BOM, hỗ trợ comma/semicolon/tab/pipe.
Không chuyển identifier, phone hoặc CCCD thành số.

## Storage và identity

- `original_fields`: tất cả header/value nguyên gốc, gồm khoảng trắng, multiline,
  unknown fields và source ID trước strip; raw bytes vẫn theo storage upload cũ.
- `raw_fields`: logical field values nguyên gốc, trước normalization.
- `normalized_fields`, `quality_flags`: standardizer hiện tại nhận bản strip giống
  parser cũ; tests equivalence bảo vệ năm schema cũ.
- `mapping_provenance`: version/hash/mapping/unknown fields.
- `lineage`: `record_id`, `source_system`, `source_record_id`, `source_file`,
  `source_row_number`, cộng `source_file_id` và `source_file_sha256` khi persist.

`record_id` là UUID của record trong snapshot/run, cũng là PK hiện tại; không lấy
external ID hay hash source path làm PK. `source_row_number` là logical data row
1-based, không gồm header; newline trong quoted value không tăng logical row.
Stable source identity là cặp `(source_system, external_id)` sau strip/source
uppercase. Cặp này giữ nguyên qua lần import khác, trong khi UUID snapshot khác.
`lineage.source_record_id` chứa external ID; không nhầm với UUID có tên
`source_record_id` trong một số DTO review hiện hữu.

Unique constraint mới: `(run_id, source_system, external_id)`. Hai namespace có
ID `001` đều hợp lệ; lặp ID trong cùng namespace/run bị từ chối, kể cả qua nhiều
file. Cùng source ID trong run khác được phép. Source namespaces được cấu hình
trong contract; không dùng một unique ID toàn cục.

## Errors và upload compatibility

Giữ các route/body/status/counters upload cũ. HTTP mapping errors trả 422 với
`detail` dạng chuỗi để frontend hiện tại hiển thị được, cộng `error_report` gồm
`code`, `message`, `resolver_version`, `details`. Report không chứa row values.
Draft validation lưu report JSON trong `validation_error` hiện hữu; serializer
trả thông báo chuỗi ở `validation_error` và report riêng ở `validation_report`.
Thông báo legacy không phải JSON giữ nguyên. Draft lỗi vẫn retry/replace được;
commit chỉ chạy khi READY. Không sửa frontend.

## Migration và rollback

`0021_input_contract` thêm ba nullable JSON columns trên records, đổi unique
constraint và nâng startup HEAD. Không đổi PK, FK, raw/normalized JSON, review,
golden hoặc lịch sử cũ. Metadata NULL của legacy records nghĩa là không có dữ
liệu đủ để tái dựng original headers/whitespace/row position; không giả tạo backfill.

Upgrade qua Alembic trên DB đã backup và trong phạm vi triển khai được giao.
Rollback về 0020 bị chặn nếu có overlapping external IDs giữa sources trong
cùng run. Khi không có overlap, rollback khôi phục uniqueness cũ, giữ ba JSON
columns để không mất raw/provenance; runtime cũ bỏ qua nullable columns này.
Re-upgrade sử dụng lại các columns. Không chạy migration trên DB thật trong task.
SQLite preservation/rollback đã test; PostgreSQL deployment rehearsal còn pending.

## Reference provenance

Đọc working tree OneMaster tại `C:\VinUni\Dự án nhóm\onemaster-workspace`, HEAD
`bc5fb6ea10d0c0771a96efff3fe9a5d806f0b1f5`; working tree dirty, chỉ đọc.

| File tham chiếu | SHA-256 |
| --- | --- |
| `src/onemaster/schema.py` | `5A1259211B8B37A7C0F427401A7DE138624C0843508845A3F322F8E583442CB4` |
| `src/onemaster/io/preparation.py` | `F0E94B954104402F8014E006C12CC80EF7DD069AF12B0479E713D6733E57B6C6` |

Resolver P-119 viết theo contract tham chiếu: canonical header keys, strict
ambiguity, source-ID precedence, lossless strings và lineage separation. Không
copy runtime preparation/Arrow/DuckDB pipeline hay import module ngoài repo;
không thêm dependency runtime. Không tìm thấy LICENSE/COPYING trong inventory
tham chiếu; không gán license bên thứ ba hoặc tuyên bố quyền phân phối OneMaster.

## Kiểm tra lại trực tiếp source (2026-10-08)

Đã đọc lại cả hai file tham chiếu; SHA-256 khớp bảng trên. Không có lỗi quyền
đọc OneMaster. P-119 giữ các nguyên tắc của reference nhưng thích nghi với API
và storage hiện tại, không sao chép toàn bộ preparation runtime:

| Concern | OneMaster | P-119 |
| --- | --- | --- |
| Header | NFKC/strip/casefold, duplicate/collision, raw__ reserved | Cùng nguyên tắc, tách duplicate/collision error codes |
| Alias | Config fields/column_aliases; strict conflict default; source_record_id ưu tiên | Versioned JSON, strict conflict; source_record_id ưu tiên, auxiliary IDs lưu original/extra |
| Strings/raw | Arrow string columns và raw__ copies | Python CSV strings, original_fields/raw_fields giữ nguyên |
| Lineage | 5 columns; snapshot hash theo source path/row | 5 JSON keys; UUID snapshot tương thích PK/FK, source identity theo `(source_system, external_id)` |
| Row number | Logical CSV row, 1-based | Cùng đơn vị, không đếm newline trong quoted values |
| Missing/ambiguous | InputError với code/details | HTTP 422 detail chuỗi và error_report; draft vẫn trả validation_error chuỗi |

Kiểm thử bổ sung xác nhận ID giống nhau trong cùng namespace qua nhiều file
bị từ chối và rollback toàn run; input `record_id/source_file/source_row_number`
không ghi đè lineage nội bộ và vẫn được giữ trong original/extra fields. Required
values rỗng báo logical role và row number, không đưa giá trị hàng vào report.
Quyền đọc source không đồng nghĩa quyền chạy migration/service trên DB thật;
kiểm tra lại chỉ dùng isolated local runner.
