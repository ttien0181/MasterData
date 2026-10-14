# Kiểm chứng luồng phân loại cặp → nhóm bản ghi, 13/10/2026

## Thay đổi đã thực hiện

- Accept cặp Group D có xung đột mềm cần lý do; lý do được lưu cùng quyết định, hàng chờ và lịch sử. Quyết định cũ thiếu lý do phải được đối chiếu lại.
- Ngoại lệ nhóm gắn với đúng 2–20 thành viên, bằng chứng và phiên bản đánh giá. Phải có đường nối bằng các cặp đã Accept; lý do chung có thể xác nhận các cặp nội bộ còn chờ. Deny, Không gộp, định danh đã xác minh, quy tắc nguồn và giới hạn thành viên vẫn được kiểm tra, kể cả ở nhánh engine cũ.
- Xem trước chạy qua worker, dùng cùng engine với công bố. Xem trước chẩn đoán toàn bộ hiển thị xung đột khi còn cặp chờ; công bố một phần tiếp tục loại vùng chưa đủ điều kiện. Giao diện phân biệt cặp chờ trên server với thao tác trong hàng chờ trình duyệt.
- Xác nhận tạo nhóm gắn với snapshot và token đã xem. Worker kiểm tra lại input, profile, assessment, cấu hình và các liên kết phủ định trước công bố. HTTP tạo nhóm chỉ kiểm tra điều kiện và xếp tác vụ; snapshot lớn được chuẩn bị trong worker.
- Tra cứu cặp/thành viên được dựng một lần; quyền trên cặp được cache trong một snapshot. Ngoại lệ chỉ đọc các thành viên và cặp nội bộ. Công bố nhóm/thành viên theo lô, giữ kiểm tra khóa và lease. Tóm tắt vùng chờ chỉ giữ 25 vùng ưu tiên; giao diện ghi rõ các giới hạn hiển thị.
- Gửi lại yêu cầu không có lý do vẫn đối chiếu được biên nhận trước nâng cấp. Lựa chọn Không gộp có ở lần duyệt đầu, tách khỏi Deny và Hoãn.

## Bằng chứng kiểm tra

| Kiểm tra | Kết quả |
| --- | --- |
| Toàn bộ backend trong môi trường SQLite riêng, tắt R2/LLM/tracing | **1.083 pass, 2 skip**, exit 0 |
| Bộ test frontend | **199 pass**, 27 file |
| TypeScript | Pass |
| Build production Next.js | Pass |
| Ruff các file Python sửa và các harness mới | Pass |
| `git diff --check` | Pass |
| Rà soát độc lập sau sửa lỗi | Không còn phát hiện quan trọng trong phạm vi rà soát; 28 test phê duyệt/xem trước pass |
| Trình duyệt Edge trên build production với API giả lập | 10 kiểm tra pass, 48 request giả lập, 0 request tới backend thật, 0 lỗi trình duyệt |

Kết quả backend cuối: [pytest.log](../../artifacts/merge-baseline/20261013T044627Z-7gm1nt6g/pytest.log), [run.json](../../artifacts/merge-baseline/20261013T044627Z-7gm1nt6g/run.json), [JUnit XML](../../artifacts/merge-baseline/20261013T044627Z-7gm1nt6g/pytest.xml).

Kiểm tra trình duyệt: [browser.json](../../frontend/artifacts/cluster-handoff-20261013/browser.json). Đây là kiểm tra UI với API giả lập; luồng API/worker thực được kiểm tra riêng bằng backend TestClient. Ảnh và browser report được chụp trước các bổ sung cuối về nhãn giới hạn và xem trước chẩn đoán; các bổ sung đó được kiểm tra bằng test frontend.

Ruff toàn repository còn **164 lỗi trong các file không nằm trong thay đổi hiện tại**. Không sửa lan sang những file này. Lượt chạy backend đầu dùng trực tiếp cấu hình máy đã được dừng vì quá chậm; kết quả hoàn tất phía trên dùng runner cách ly hiện có.

## Benchmark và hiệu chỉnh thử nghiệm

[Kiểm chứng offline ban đầu](cluster-handoff-20261013.md) ghi đúng phạm vi 774 tình huống đầy đủ khi bản local thiếu file. [Báo cáo hiệu chỉnh thử nghiệm](cluster-calibration-20261013.md) dùng bản overlay khôi phục từ Drive: bốn CSV đều khớp manifest, không sửa Downloads.

Hiệu chỉnh đọc 200.000 dòng CSV và đánh giá 2.100 tình huống TC6/TC8, 68.000 cặp, bảy ngưỡng. Ở ngưỡng 2,0–6,0, recall cặp cùng nhóm sau guard đạt 2,872% cho TC6 và 66,667% cho TC8, chưa quan sát gộp sai hoặc vi phạm ràng buộc. TC6 có 57.100 cặp positive trong các ca kích thước 19/20/21 bị gate giữ lại; kết quả không chứng minh khả năng tự động gộp những nhóm đó.

Dữ liệu tổng hợp PRIOR/L0 và phép quét trên cùng các tình huống không đủ chứng nhận ngưỡng production. Các khoảng tin cậy, mẫu hỗ trợ và giới hạn được ghi trong báo cáo. **Không chọn mức rủi ro thay người dùng và không áp dụng ngưỡng thử nghiệm vào production.**

## Sử dụng và giới hạn

Chạy migration `0030_review_reasons` theo README trước khi triển khai backend/worker mới. Chưa chạy migration trên database đang sử dụng và chưa triển khai/rebuild container. Không tạo hoặc sửa Docker Compose.

Các kiểm tra không phải benchmark toàn pipeline 100k/1M, không đo candidate recall toàn bộ, không kiểm tra export production và không thay thế đánh giá trên dữ liệu doanh nghiệp. Snapshot/artifact workflow vẫn dùng cơ chế JSON hiện có; việc chuyển sang lưu trữ phân vùng cần một thay đổi kiến trúc riêng để chứng minh khả năng chạy 1M bản ghi.

Các thay đổi ở checkout chung để người dùng xem diff; không push hoặc merge.
