# Baseline trước cải thiện tạo nhóm — 13/10/2026

Chưa sửa code ứng dụng cho đợt cải thiện này. Người dùng chọn giữ duyệt trước khi
gộp; thiết kế đầy đủ đang chờ duyệt. Không tạo hoặc sửa Docker Compose trong đợt
kiểm tra này. Checkout đã có nhiều thay đổi chưa commit từ trước.

## Dữ liệu đã xác minh

Nguồn: [Drive do người dùng cung cấp](https://drive.google.com/drive/folders/1ZSRF1p6OY9hCglV95jebNcOZusQ_D1Ws).
Cache riêng: `artifacts/cluster-improvements-20261013/datasets`.

| Bundle seed-7 | File khớp manifest | Dòng input | Khóa nguồn khác nhau | Input/truth khớp | JUNK |
| --- | ---: | ---: | ---: | --- | ---: |
| TC6/100k | 19/19 | 100.000 | 100.000 | Có | 0 |
| TC8/100k | 19/19 | 100.000 | 100.000 | Có | 10.000 |
| TC13/100k | 19/19 | 100.000 | 100.000 | Có | 0 |

Mỗi bộ có đủ tám CSV, từng file đúng số dòng manifest, không trùng source key.
Các cột truth/owner/JUNK/component không có trong header input. Tải gặp HTTP 503
ở một số file; retry thành công và chỉ dùng nội dung khớp SHA-256. Không sửa file
nguồn hay manifest. Truth chỉ dùng để đánh giá offline.

Biên nhận và kiểm tra: [fetch-report.json](../../artifacts/cluster-improvements-20261013/datasets/fetch-report.json),
[input-validation.json](../../artifacts/cluster-improvements-20261013/input-validation.json).
README Drive ghi TC6/1M có một CSV lệch checksum; chưa chạy hoặc xác minh lại bộ
1M trong baseline này.

## Component probes

Engine hiện có được chạy qua harness `verify_cluster_benchmark.benchmark` với
tối đa năm component mỗi stratum và kích thước tối đa 100. TC6 chạy 65 component,
TC8 chạy 40, TC13 chạy 85: tổng 190, không có runtime exception. Hash tám file
engine được ghi trước và kiểm tra sau; code không đổi trong phép chạy.

Kết quả đầy đủ: [component-baseline.json](../../artifacts/cluster-improvements-20261013/component-baseline.json).
Đây là kiểm tra component có giới hạn, không phải throughput full workflow hoặc
candidate recall. Frequencies/budget ở phạm vi từng component. Các guard probe
giả định eligible có nhãn riêng; không coi là steward đã duyệt. Đường assessment
chưa có ACCEPTED không tạo nhóm nhiều record; không suy precision 100% từ abstention.
TC13 hot-200/hot-1000 nằm ngoài giới hạn kích thước của phép probe này.

## TC6/100k: đầy đủ pipeline offline hiện có

Chạy toàn bộ 100.000 record và các stage PREPARE/NORMALIZE/BUILD_CANDIDATES/SCORE/
DECIDE/CLUSTER bằng `StageRunner` hiện có. Không dùng truth trong compute, không
thay code hay threshold, không thực hiện merge/publication trên database ứng dụng.

| Chỉ số | Giá trị |
| --- | ---: |
| Thời gian pipeline | 101,37 giây |
| CPU | 88,63 giây |
| Peak RSS được lấy mẫu của harness | 3.381.075.968 bytes, khoảng 3,15 GiB |
| Candidate | 770.232 |
| Serving pair sau DECIDE | 28.900 |
| Đề xuất nhóm nhiều record | 25.400 |
| Review case | 2.700 |
| Positive pairs theo truth | 84.000 |
| Positive candidate | 84.000, recall 100% trên bundle này |
| Positive co-cluster pairs trong proposal | 26.200, recall 31,19% |
| Cross-owner co-cluster pairs trong proposal | 0 |

Peak RSS có thể gồm cả đánh giá offline sau pipeline vì monitor chạy đến cuối
harness; không coi đây là riêng RAM bước CLUSTER. Thời gian CLUSTER được stage
ghi là 9,245 giây. Baseline này chưa đo snapshot workflow, publication DB,
golden confirmation, export, frontend, retry/cancellation hoặc đường manual ACCEPTED.
Proposal của StageRunner không phải nhóm đã được công bố trong session workflow.
Dữ liệu tổng hợp PRIOR/L0 và một bundle không chứng nhận chất lượng production.

Kết quả và từng stage: [measurement.json](../../artifacts/cluster-improvements-20261013/tc6-full-offline-baseline/measurement.json).

Lệnh chạy từ repo root:

```powershell
./.venv/Scripts/python.exe -B -X utf8 scripts/qa_pair_cluster_benchmark.py --bundle artifacts/cluster-improvements-20261013/datasets/TC6_CLUSTER_BRIDGE/100k/seed-7 --output artifacts/cluster-improvements-20261013/tc6-full-offline-baseline --cap 100000
```

Harness từ chối output đã tồn tại; khi chạy lại phải chọn thư mục output mới.
Candidate cap của harness là 1.000.000 và không bị vượt ở lượt TC6 này. Các phép
đo 100k TC8/TC13 và 1M chưa được tuyên bố hoàn thành. Kết quả trên là baseline
để đối chiếu từng thay đổi sau khi thiết kế được duyệt.
