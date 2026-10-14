# Bản kiểm thử trên Nam_Backup — 2026-10-14

Bản này phục vụ bạn cùng nhóm chạy thử, đánh giá và quyết định merge. Chưa phải
hoàn tất toàn bộ thiết kế chuyển workflow sang Parquet. Giữ người duyệt xác nhận
trước khi gộp; không bật tự động gộp theo benchmark.

## Phần đã triển khai

- Golden dùng verified/quality, ưu tiên nguồn theo trường và thời gian nguồn hợp
  lệ; giữ thông tin nguồn và các giá trị shared/không hợp lệ làm phương án đối chiếu.
- Hash canonical theo luồng, kiểm tra component đã duyệt với một ngân sách chung,
  tần suất liên hệ của toàn snapshot và ràng buộc Deny/không gộp.
- Duyệt nhóm 21–100 qua receipt, phân trang thành viên/bằng chứng và lý do; nhóm
  vượt 100 không được chia tùy ý. Có đường duyệt riêng từng cặp khi vùng lớn còn chờ.
- `entity_id` độc lập với ID nhóm theo generation. Nhánh tách giữ ID ở đúng một
  nhánh; gộp xét tuổi thực thể và thành viên neo. Singleton ảo cũng tham gia lineage.
- Singleton mặc định không tạo một dòng nhóm/thành viên cho mỗi bản ghi. Xác nhận
  toàn bộ dùng một receipt; chỉnh sửa/xác nhận riêng tạo exception.
- Khi trường bổ sung mất nguồn, thao tác bỏ lựa chọn cũ yêu cầu lý do, giữ các
  giá trị trước đó/người thực hiện/thời gian trong audit và lịch sử thực thể.
- Nhánh thử nghiệm Parquet có artifact niêm phong, kiểm tra hash/ownership,
  nguồn singleton đóng băng, preview, publication theo lô, chốt Golden qua tác vụ
  có lease, receipt nhỏ và kiểm tra độ phủ/ID duy nhất trước khi xuất CSV.

## Phạm vi chuyển đổi còn dở

`WORKFLOW_PARQUET_ENABLED` mặc định **false**. Luồng JSON/singleton ảo hiện hữu vẫn
là luồng mặc định của bản kiểm thử. Chỉ bật cờ này trong môi trường thử nghiệm riêng
để đánh giá nhánh mới; chưa dùng nó cho triển khai.

Task 8 còn việc tích hợp repair/apply/undo, assessment và annotation theo luồng,
kế thừa xác nhận singleton sau repair, kiểm thử lỗi giữa các lô và đánh giá các
fence khi chuyển toàn bộ producer/consumer. Task 9 còn các ca browser nhóm lớn,
stale/back/reload và chốt nền. Task 10 được thu hẹp theo yêu cầu ngày 14/10: một TC,
bộ nhỏ nhất, không chạy lại 100k/1M. Không đánh dấu các task này hoàn tất.

Lượt review độc lập tìm bốn vấn đề: lịch sử Golden kế thừa, lineage của singleton
ảo, tính toàn vẹn/khôi phục cache locator và danh sách unresolved của Parquet.
Đã thêm kiểm thử tái hiện rồi sửa; kết quả kiểm thử cuối được ghi bên dưới.

## Cách chạy kiểm tra

Trong môi trường Python đã cài `backend[dev]`, từ thư mục gốc:

```powershell
.venv/Scripts/python.exe -B -X utf8 scripts/run_cluster_checks.py --suite all --timeout 900
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run build
```

Runner backend tạo DB/store riêng, không lấy DB, R2 hoặc LLM của người phát triển.
Trên Linux/macOS thay đường dẫn interpreter và `npm.cmd` bằng lệnh tương ứng.

Chỉ tải và chạy TC1/10k:

```powershell
.venv/Scripts/python.exe -B -X utf8 scripts/fetch_cluster_bundles.py --case TC1_easy_match --scale 10k --output artifacts/cluster-improvements-20261013/datasets
.venv/Scripts/python.exe -B -X utf8 scripts/verify_cluster_workflow.py --bundle artifacts/cluster-improvements-20261013/datasets/TC1_easy_match/10k/seed-7 --output artifacts/cluster-improvements-20261013/tc1-review-new --review-mode declared-fixtures
```

Đây là review mô phỏng theo bằng chứng nguồn: Accept chỉ cho cặp `MERGEABLE`, nhóm A,
có quyền duyệt và không cần lý do xung đột mềm; các trường hợp khác giữ riêng.
Truth chỉ được mở sau khi chụp dự đoán để đánh giá. Bộ nguồn là synthetic PRIOR/L0;
kết quả không chứng nhận chất lượng production hoặc quy mô 100k/1M. Không có cặp
dự đoán thì precision là null.

Dataset TC1/10k có **10.022 bản ghi thực tế**, không phải đúng 10.000; 12/12 file
trong manifest đã khớp SHA-256. Manifest hash:
`bdcd39fac470c0929d07ece1da0e237bf58d7de2563bf454688a0ff4a5526ce4`.
Không đẩy dataset/cache/DB/CSV sinh ra lên Git.

## Migration và hợp đồng

Migration mới là `0032_generation_coverage` (file
`0032_workflow_generation_coverage.py`), sau `0031_pair_automation`; một Alembic
head. Thêm coverage, singleton confirmation, storage version; ID nhóm/thành viên
mở rộng tới 128 ký tự để chứa ID singleton ảo. Chạy migration qua công cụ migration
hiện có trước khi chạy backend trên DB của môi trường kiểm thử. Phiên này không
chạy migration lên DB đang sử dụng.

CSV mới xuất `master_record_id=entity_id` và receipt ghi `workflow_entity_v1`;
receipt lịch sử giữ hợp đồng ID cũ. Nhánh Parquet trả 202 cho `/finalize-golden`,
cần chờ operation hoàn thành trước khi xuất. Frontend poll theo active operation.
Không tạo Docker Compose mới; file Compose hiện có đã thay đổi từ trước phiên này.

## Kết quả kiểm tra cuối

Backend đầy đủ sau sửa đọc lặp: **1.476 passed, 2 skipped**, 492,53 giây. Lệnh runner ở trên;
log `artifacts/merge-baseline/cluster-all-q160daej/pytest.log`. Các kiểm thử migration
và tương thích receipt cũ nằm trong bộ này.
Frontend: 237 tests/33 files, typecheck và production build đã đạt.
Browser trên production build, bridge tới API thật/worker có claim của fixture riêng:
41 request, không có page error hoặc response HTTP >=400; một số request bị hủy
khi chuyển màn hình (status 0). Kiểm tra duyệt nhóm có lý do,
preview → publication → đối chiếu nhóm và bỏ lựa chọn bổ sung có lý do/lịch sử qua reload.
Fixture browser là dữ liệu tổng hợp để kiểm tra thao tác, không phải benchmark TC khác.

`docker compose config --quiet` đạt trên file Compose hiện có; không khởi động
container. `git diff --cached --check` đạt. Báo cáo JSON lint/browser được giữ
cục bộ; nhánh review chỉ giữ hướng dẫn này cùng code, test và công cụ kiểm thử.

TC1/10k hoàn tất với trạng thái **SIMULATED_REVIEW_END_TO_END**, `FINALIZED` và
CSV đã xuất. Độ phủ 10.022/10.022 bản ghi, không lặp/thiếu ID nguồn; 9.999 dòng CSV,
gồm 23 nhóm hai thành viên và 9.976 singleton. Code/input hashes khớp ở cuối lượt.
Báo cáo chi tiết của lượt chạy nằm tại
`artifacts/cluster-improvements-20261013/tc1-10k-after-reader-fix/report.json`
trong máy đã chạy kiểm thử; không đưa kết quả sinh ra lên nhánh review.

| Chỉ số trên TC1/10k | Kết quả |
| --- | ---: |
| Candidate recall | 100% |
| Cặp đúng trong truth | 4.894 |
| Cặp được gộp / đúng / sai | 23 / 23 / 0 |
| Pair precision | 100% trên 23 cặp |
| Pair recall | **0,47%** |
| Exact entity matches | 257 |
| JUNK co-cluster pairs | 0 |

Quy tắc duyệt mô phỏng đã chọn `Không gộp` cho 83.104 cặp còn lại. Recall cuối rất
thấp phản ánh chính sách mô phỏng bảo thủ chỉ Accept nhóm A đủ điều kiện, không phải
thiếu candidate. Precision trên 23 cặp không chứng minh chất lượng gộp đạt yêu cầu;
cần đánh giá thêm cách phân loại/bằng chứng/duyệt của những cặp còn lại. Không mở
quyền gộp hoặc dùng truth để nâng kết quả của lượt này.

| Bước thực tế | Giây | SQL statements |
| --- | ---: | ---: |
| Preview worker | 200,92 | 654 |
| Publication worker | 140,96 | 710 |
| Đọc toàn bộ các trang nhóm | 84,02 | 2.115 |
| Xác nhận Golden | 0,40 | 124 |
| Chốt Golden qua API JSON | 10,67 | 93 |
| Xuất CSV qua API | 18,92 | 32 |

Đỉnh RSS của **cả process** là 5.379.997.696 byte, khoảng **5,01 GiB**; không phải
đỉnh riêng từng bước. Đường JSON vẫn tốn tài nguyên với 83.127 candidate; lượt nhỏ
này không chứng minh các bước chuyển tiếp đã đủ nhanh hay bộ nhớ đã đạt mục tiêu
quy mô lớn. Đây là lý do giữ phần chuyển đổi theo luồng trong danh sách còn dở.

Log/cache đầy đủ nằm trong `artifacts/` được ignore. Các thống kê cần cho review
được giữ trong hướng dẫn này. Người review có thể chạy lại bằng các lệnh ở trên;
không cần dataset/cache/DB/CSV hay tài liệu kế hoạch của tác giả để chạy unit tests.

## Quyết định và giới hạn khi bàn giao

- Giữ checkpoint trên `Nam_Backup` theo yêu cầu để đồng đội đánh giá. Không merge;
  giữ baseline và ledger cục bộ vì kế hoạch vẫn đang thực hiện.
- Chỉ chạy TC1/10k với cấu hình mặc định JSON/virtual singleton. Các kiểm thử API
  nhỏ riêng có bật Parquet; kết quả TC1 không chứng minh hiệu năng nhánh Parquet.
- Chưa đủ cơ sở bật Parquet mặc định: repair/assessment/annotation và failpoint
  theo lô còn thiếu. Chi phí của việc giữ cờ tắt là đường JSON tương thích vẫn có
  chỗ đọc lại nguồn và materialize kết quả; cần đo riêng trước khi đổi mặc định.
- Bốn finding của reviewer được coi là Important và đã sửa trong một lượt:
  phiên bản Golden ghi sau khi kế thừa lựa chọn; singleton ảo tham gia ancestry;
  locator có receipt SHA-256 và khôi phục từ store đã xác minh; unresolved đọc
  danh sách loại khỏi generation và nguồn đóng băng của đúng snapshot.
- Harness hiện có pairwise/candidate recall, exact entity và JUNK metrics; chưa
  có B³, số byte IO/disk theo từng bước hoặc toàn bộ phân loại timeout/budget của
  Task 10. Hash inventory của adapter được đặt tên `adaptation_inventory_hash`;
  đây không phải hash cấu hình matcher hay chứng nhận cấu hình production.
- Ruff đạt trên **73 file của đợt triển khai này**, so với baseline đã giữ lại.
  Toàn bộ patch 113 file Python còn 192 lỗi lint thuộc phần thay đổi có sẵn;
  không ghi nhận toàn bộ patch đạt Ruff. Hai import block phát hiện ở đợt này
  và style trong test migration đã được sửa trước lượt kiểm tra cuối.
- Lượt TC1 đầu được dừng ở phân trang nhóm; hai lần lấy stack chỉ đúng đường
  `list_page → digest` đọc/hash lại phần nguồn. Kiểm thử tái hiện mỗi phần bị
  hash 6 lần/lượt đọc. Iterator JSON mới xác minh từng phần một lần và lọc cursor
  trước truy vấn thành viên, giữ kiểm tra hash/chủ sở hữu/số dòng. Bộ hồi quy
  singleton/storage/hash/harness đạt **67 tests**. Log lượt chưa hoàn tất được
  giữ cục bộ để đối chiếu.
  Lượt kiểm tra lại vẫn chỉ dùng TC1/10k với cache nguồn đã kiểm tra hash.
