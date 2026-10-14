# Giai đoạn 2 — Giảm thao tác duyệt cặp (13/10/2026)

## Kết quả triển khai

- Phiên mới mặc định mở **Cần xử lý để hoàn tất**. Bộ lọc người dùng đã chọn được giữ lại khi chuyển nhóm/tab. Cặp thiếu bằng chứng vẫn có bộ lọc riêng, vẫn lưu trong hệ thống.
- Giải thích ngắn hiện trực tiếp: thông tin nhận dạng khớp và trường mâu thuẫn. Trường dùng chung không được mô tả là điểm neo cá nhân; điểm quy tắc không bị biến thành xác suất. Bằng chứng đầy đủ và giải thích AI vẫn truy cập được.
- **Duyệt theo cụm** trình bày mỗi thành viên một dòng với nguồn, mã bản ghi, tên, phone, email, DOB và giấy tờ. Cặp cùng tên được phân biệt bằng nguồn/mã; có nút mở đối chiếu đầy đủ. Mâu thuẫn mềm hoặc Hoãn tự mở phần bằng chứng.
- Accept cụm đi qua xác nhận hàng loạt hiện có, dùng đúng danh sách mã cặp còn chờ; giữ token xem trước, phiên bản, lý do bắt buộc, audit và hàng đợi. Đây là quyết định của người duyệt. Không dùng quyền ngoại lệ nhóm để vượt kiểm tra.
- Chỉ đề xuất Accept cùng lúc cho cụm đầy đủ, tối đa 20 thành viên/200 cặp, có bằng chứng trực tiếp giữa mọi thành viên. Thiếu cạnh, thiếu bản ghi, cạnh thiếu bằng chứng/không rõ, Deny/Không gộp/cannot-link hoặc ràng buộc chặn sẽ tắt đề xuất. Cụm vượt giới hạn trả về chế độ từng cặp. Nhóm cuối cùng vẫn qua bộ kiểm tra cụm khi hoàn tất.

## Đo trên trình duyệt thật

Đã chạy API, PostgreSQL, worker và frontend trong **docker-compose.yml hiện có**, với tài khoản và CSV tổng hợp cục bộ; không mock API. Mỗi mẫu gồm một cụm trùng và một người khác chỉ trùng địa chỉ. Hai cách duyệt đều tạo đúng một nhóm nhiều thành viên và một singleton; các cạnh thiếu bằng chứng vẫn UNDECIDED. Không có lỗi JavaScript trong lượt cuối.

| Cụm | Bấm từng cặp → theo cụm | Dòng nguồn trên màn đối chiếu | Thời gian script trung vị (2 lượt) |
|---|---:|---:|---:|
| 4 bản ghi | 6 → 3 | 12 → 4 | 1.629s → 1.742s |
| 5 bản ghi | 10 → 3 | 20 → 5 | 2.857s → 2.360s |

Với cụm 5 bản ghi: giảm 70% số lần bấm trong đường duyệt, và số dòng nguồn trên màn đối chiếu chọn dùng giảm 20 xuống 5. Thời gian script trung vị giảm khoảng 17%. Cụm 4 bản ghi có ít thao tác hơn nhưng trung vị script chậm hơn khoảng 7%, do chi phí đọc cụm/xác nhận/chờ tác vụ hàng loạt.

**Giới hạn phép đo:** thời gian trên là script, không phải nghiên cứu thời gian đọc của người dùng. Chỉ có hai lượt cho mỗi cấu hình. Chỉ số dòng nguồn mô tả màn đối chiếu được chọn; không cộng danh sách cặp xuất hiện trước khi mở cụm, không đo tổng số lần nhìn hoặc thời gian đọc. Phần hoàn tất/tạo nhóm giống nhau ở cả hai cách và không nằm trong thời gian duyệt. Vì vậy kết quả xác nhận giảm thao tác và trình bày lặp, chưa chứng nhận mức tiết kiệm thời gian cho người dùng thực tế.

## Đo cặp trùng nằm ngoài hàng đợi bắt buộc

Matcher chạy trước, nhãn CERTAIN từ truth chỉ được mở sau khi tính xong. Cả 8 bộ giữ nguyên hash candidates, hash điểm/chữ ký và số việc bắt buộc so với sau giai đoạn 1. Giai đoạn 2 không loại thêm cặp khỏi hàng đợi bằng một quy tắc suy diễn mới.

| Bộ dữ liệu | Bản ghi đã đọc | Cặp cần duyệt | Cặp cùng thực thể có nhãn trong candidates | Ngoài hàng đợi: thiếu bằng chứng | Ngoài hàng đợi: bị chặn |
|---|---:|---:|---:|---:|---:|
| TC1 | 10,022 | 4,166 | 4,894 | 0 | 728 |
| TC2 | 10,000 | 1 | 0 | 0 | 0 |
| TC3 | 9,992 | 1 | 3,256 | 2,527 | 729 |
| TC4 | 10,014 | 2,460 | 0 | 0 | 0 |
| TC5 | 10,010 | 4,608 | 9,792 | 4,204 | 1,524 |
| TC6 | 12,000 | 1,168 | 1,238 | 93 | 0 |
| TC8 | 12,000 | 339 | 339 | 3 | 0 |
| TC13 | 12,000 | 972 | 236 | 13 | 0 |

Các cặp này **được giữ lại**, không bị xóa và không bị tự gán Deny. “Ngoài hàng đợi” nghĩa là không bắt buộc người dùng xử lý để hoàn tất, không có nghĩa đã chứng minh khác người hoặc đã phục hồi trùng lặp.

- TC3 có 2.527 cặp cùng thực thể nhưng không đạt evidence floor. TC5 có 4.204 cặp tương tự. Đây là hạn chế chất lượng thật của điều kiện neo hiện tại; giảm hàng đợi không được dùng để che hạn chế này.
- Các cặp cùng thực thể bị chặn được tách theo lý do: TC1 728, TC3 729 do PERSON_POLICY_OUT_OF_SCOPE; TC5 1.321 do ngoài phạm vi cá nhân và 203 do PERSON_ORG_MISMATCH. Không cộng các trường hợp này vào lỗi thiếu neo cá nhân.
- Một ca trình duyệt riêng dùng hai bản ghi được biết là cùng người nhưng không có contact. Hệ thống giữ 1 cặp INSUFFICIENT_EVIDENCE ngoài hàng đợi; phép đo báo đúng 1 cặp trùng chưa được phục hồi. Không có nhãn gold nào được nhập vào matcher.

Đây là phép đo **ở mức cặp ứng viên có nhãn**, chưa phải recall toàn hệ thống: chưa đếm mọi cặp trùng bị blocking bỏ sót, chưa đo precision/recall của đầu ra thực tế. TC1–5 dùng bộ 10k seed7; TC6/8/13 lấy 12k mẫu xác định từ bộ 100k. Mẫu làm thay đổi tần suất contact. TC5 đọc được 10.010 dòng sau khi bỏ footer/header lặp/dòng lỗi, so với manifest 10.019; 9.801 dòng có nhãn CERTAIN ánh xạ được. Không suy rộng kết quả lên toàn bộ 100k/1 triệu bản ghi hoặc dữ liệu khách hàng thật.

## Kiểm chứng và review

- Backend đầy đủ: **1.119 passed, 2 skipped**. Sau khi bổ sung thống kê tách lý do, chạy lại nhóm kiểm tra liên quan: **17 passed**.
- Frontend bản cuối: **209 passed / 30 files**; TypeScript và production Docker build đạt.
- API/frontend/PostgreSQL healthy, worker đang chạy. Chỉ dùng Compose có sẵn; giai đoạn 2 không thêm migration.
- Review độc lập không còn lỗi Critical/Important. Điểm thiếu khả năng phân biệt cặp cùng tên đã được sửa bằng nguồn/mã và đối chiếu đầy đủ; cách mô tả chỉ số dòng nguồn đã được sửa để tránh khẳng định tiết kiệm thời gian đọc chưa đo.
- Git diff whitespace check đạt. Không commit, không ghi đè công việc có trước; baseline giai đoạn 2 là snapshot workspace sau giai đoạn 1, không phải cây sạch ở commit.

Mã kiểm thử browser: `frontend/scripts/verify-review-effort.mjs`; kết quả và ảnh: `frontend/artifacts/review-effort-stage2/`. Hai lượt đo và kiểm chứng: [review-effort-stage2-validation.json](review-effort-stage2-validation.json). Số liệu theo bộ và lý do: [review-effort-stage2-diagnostics.json](review-effort-stage2-diagnostics.json). Đối chiếu giai đoạn 1: [pair-correctness-stage1-20261013.md](pair-correctness-stage1-20261013.md).

Có thể chạy lại từ frontend bằng `node scripts/verify-review-effort.mjs <đường-dẫn-account-STEWARD-cục-bộ.json>` khi Compose đang phục vụ ở 3000/8001. Script chỉ tạo dữ liệu tổng hợp trên hai địa chỉ localhost này và không đưa tài khoản vào báo cáo. Benchmark dùng `scripts/verify_pair_correctness.py --datasets <cache> --max-records 12000 --output <report.json>`; dependency đọc Excel nằm trong `scripts/requirements-qa.txt`. Bộ test backend dùng môi trường cô lập của `scripts/run_merge_baseline.py`, tránh cấu hình DB/R2/LLM bên ngoài trong .env.

## Cơ sở và phần chưa thay đổi

[Splink — Edge Evaluation](https://moj-analytical-services.github.io/splink/topic_guides/evaluation/edge_overview.html) yêu cầu dùng nhãn để đánh giá precision/recall và đánh giá cả cụm đầu ra. [Dedupe — Choosing a Good Threshold](https://docs.dedupe.io/en/latest/how-it-works/Choosing-a-good-threshold.html) phân biệt mẫu active learning nhiều thông tin với mẫu đại diện dùng để chọn ngưỡng. Vì vậy báo cáo tách giảm thao tác khỏi kiểm định chất lượng.

Chưa hạ ngưỡng ghép, chưa nới evidence floor, chưa bật auto-merge, chưa huấn luyện mô hình mới. Phiên đã đánh giá trước giai đoạn 1 cần đánh giá lại một cách tường minh để nhận assessment v3. Phần tiếp theo về cân bằng chất lượng cần xử lý các cặp trùng không có neo, đo blocking recall và kiểm định trên dữ liệu đại diện, trước khi cấp tự động hóa quyết định ghép.
