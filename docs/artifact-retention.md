# Quản lý dung lượng artifacts

## Kết quả dọn ngày 16/10/2026

Phần đọc được của `artifacts/` trước khi dọn là **28,850 GB** (26,869 GiB).
Đã xóa **24,448 GB**, còn khoảng **4,402 GB**. Đơn vị GB ở đây là 10⁹ byte.

Nguyên nhân chính là các script benchmark tạo SQLite sandbox riêng cho từng
lần thử nhưng giữ database sau khi xuất kết quả. Có lần M2B giữ gần 5,83 GB;
những lần thất bại vì hết RAM hoặc timeout cũng giữ database. Giới hạn đĩa
12 GiB của M2B áp dụng cho từng lần chạy, không giới hạn tổng thư mục.
`.gitignore` và `.dockerignore` chỉ tránh đưa file vào Git hoặc Docker build;
chúng không hạn chế dung lượng trên máy.

Đã dọn:

- Database `state/test.db` và các sidecar của những run M0/M2A/M2B đã có
  manifest + báo cáo hoàn tất, hoặc có bằng chứng thất bại rõ ràng.
- Index `*.parquet.index.sqlite` còn sót của benchmark khi file Parquet gốc
  vẫn còn. `ParquetSnapshot` tạo lại index này khi mở dữ liệu.
- Wheel torch tải trùng và các chunk tải xuống. Đã so sánh SHA-256 với bản
  còn giữ ở `backend/encoder/wheels/` trước khi xóa.

Giữ dataset, prediction, evaluation, report, manifest lịch sử, log, model E5,
môi trường encoder, baseline được Git theo dõi và dữ liệu ứng dụng trong `data/`.
Nhật ký từng file đã xóa nằm ở `artifacts/cleanup/`.

Manifest lịch sử được giữ nguyên để lưu bằng chứng gốc. Các mục database đã
dọn trong manifest đó phải đối chiếu với nhật ký cleanup; không còn xác minh
toàn bộ bundle gốc hoặc tiếp tục trực tiếp từ database cũ. Muốn debug trạng
thái database cần chạy lại benchmark.

Một thư mục `merge-baseline/20261013T130057Z-qcl1fajm` bị từ chối quyền đọc
được để nguyên. Junction `baseline-comparison/frontend/node_modules` cũng
được bỏ qua để không quét hoặc xóa dữ liệu ở đích liên kết. Số liệu trên không
bao gồm phần không đọc được hoặc nội dung đích của junction.

## Cơ chế giữ dung lượng nhỏ

Ba script `run_m0_e0.py`, `run_m2a_pipeline_trial.py` và
`run_m2b_experiments.py` nay mặc định đóng engine, xóa SQLite sandbox sau
khi xuất đủ kết quả thành công, rồi mới tạo manifest. Chúng giữ file
`retention.json` ghi tên và kích thước database đã bỏ. Khi cần database để
debug, thêm `--keep-state` vào lệnh benchmark; controller truyền tùy chọn
này xuống tiến trình con. Run thất bại vẫn giữ trạng thái để điều tra.

Định kỳ sau một đợt thử nghiệm, chạy từ gốc dự án trong PowerShell:

```powershell
# Chỉ xem dung lượng và danh sách đề xuất, không xóa
.venv\Scripts\python.exe -B scripts\artifact_retention.py --include-failed --include-caches

# Dọn các file đủ điều kiện, mặc định chỉ file cũ ít nhất 7 ngày
.venv\Scripts\python.exe -B scripts\artifact_retention.py --apply --include-failed --include-caches
```

Công cụ chỉ xử lý những loại file/thư mục benchmark được liệt kê trong
`RUN_PATTERNS` và các cache cụ thể. Nó không xóa toàn bộ thư mục theo ngày,
không đi qua symlink/junction, không xóa file Git đang theo dõi. Mỗi lần áp
dụng đều ghi kế hoạch và kết quả xóa. File bị thay đổi từ lúc lập kế hoạch
được bỏ qua. Các họ benchmark mới cần bổ sung chính sách tương ứng.

Chỉ dùng `--min-age-days 0` khi đã kiểm tra benchmark không còn chạy và muốn
dọn ngay. Tránh dùng `--keep-state` cho hàng loạt run. Khi thử nghiệm nhiều
cấu hình, kiểm tra tổng `artifacts/` giữa các đợt; giữ khoảng 5–10 GB cho
dataset/model/báo cáo và dọn database debug không còn dùng.

Kiểm thử công cụ dọn, không chạy benchmark nặng:

```powershell
.venv\Scripts\python.exe -B -m unittest discover -s scripts -p test_artifact_retention.py -v
```
