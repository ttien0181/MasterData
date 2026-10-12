# Tự xử lý xung đột và tạo Golden

Ngày: 2026-10-11. Áp dụng cho session workflow v2; giữ các thao tác hiện có.

## Trải nghiệm

- Nút **Tự xử lý & tạo Golden** có ở Các nhóm bản ghi và Golden record.
- Phạm vi ưu tiên các nhóm đã tích chọn, gồm chọn toàn bộ qua nhiều trang và loại trừ. Nếu chưa tích chọn, xử lý nhóm đang xem; tại Golden record xử lý toàn phiên. Có nút riêng **Xử lý tất cả nhóm** để mở rộng phạm vi rõ ràng.
- Xung đột không vô hiệu hóa nút xử lý. Quyền tài khoản, công việc đang chạy và các lựa chọn chưa lưu vẫn được kiểm tra.
- Kết quả nêu số Golden đã xác nhận, số vùng cần duyệt và các trường cần chọn. Hiển thị trạng thái AI thực tế.
- Đề xuất có nhóm/thành viên trước và sau, loại xung đột, bảng liên kết thay đổi có phân trang, giải thích và phạm vi các nhóm liên quan ngoài lựa chọn.
- **Áp dụng đề xuất** là quyết định duyệt thay đổi đã hiển thị. Hệ thống tạo lại nhóm, kiểm tra và xác nhận Golden hợp lệ trong vùng đó.
- **Hoàn tác đề xuất vừa áp dụng** có bước xem lại ảnh hưởng. Chỉ hoàn tác khi phiên và các liên kết chưa bị sửa tiếp; sau đó tạo lại nhóm, giữ lịch sử và kiểm tra lại xác nhận.

## Quy tắc

1. Đọc phiên bản nhóm, snapshot quyết định và artifact đã xác minh checksum, đầy đủ bản ghi; không lấy danh sách thành viên hay quyết định từ client.
2. Nhóm đã chọn không chạm vào vùng xung đột được kiểm tra định danh, Deny và giới hạn thành viên trước khi công bố membership riêng.
3. Chọn giá trị Golden theo quy tắc survivorship hiện có: đã xác minh, chất lượng, nguồn và thời điểm. Giá trị địa chỉ liên quan giữ cùng nguồn. Không bịa thông tin.
4. Giữ lựa chọn thủ công phù hợp; trường bổ sung mâu thuẫn hoặc dữ liệu không hợp lệ vẫn cần duyệt. Khi tách thay đổi membership, giá trị nhập tay của nhóm cha cần kiểm tra lại trước khi dùng cho nhóm con; không tự sao chép cho mọi người.
5. Gom xung đột liên quan thành vùng đóng theo nhóm. Đề xuất kiểm tra mọi cặp trong component bằng engine đã chọn, Deny, định danh và bằng chứng liên hệ; chỉ loại các liên kết MERGE không thuộc cùng component được đề xuất.
6. Không chia theo vị trí dòng hoặc mỗi 20 bản ghi. Phân tích có thể kiểm tra component nhất quán đến 100 thành viên trong ngân sách giới hạn; component hợp lý vượt giới hạn công bố 20 hoặc vượt ngân sách phân tích được giữ để xem xét chính sách/bằng chứng.
7. Khi duyệt, các liên kết được liệt kê chuyển thành KEEP_SEPARATE. Không chuyển Deny, không xóa nguồn, không đổi baseline; snapshot và revision mới được tạo để chạy lại CLUSTER.
8. Chỉ công bố chính thức những nhóm không chạm xung đột. Chốt và xuất toàn phiên tiếp tục yêu cầu generation đầy đủ và mọi Golden được xác nhận.

## AI

- Dùng gateway hiện có cho địa chỉ khó đối chiếu và giải thích tín hiệu tổng hợp. Advice không sửa điểm, không cấp quyền gộp và không ghi giá trị Golden.
- Mọi lời gọi chịu công tắc global, cấu hình provider/model, accounting, giới hạn lượt/token/chi phí và revision fence hiện có.
- Giải thích tối đa ba vùng mỗi lần xử lý; địa chỉ theo giới hạn gateway hiện có. Không có AI hoặc provider lỗi thì quy tắc vẫn hoạt động.
- Môi trường QA `13018` hiện tắt LLM và không được cấp key; giao diện ghi rõ điều này. Không coi câu mẫu là kết quả AI.

## API và lưu trữ

- `POST /mdm/runs/{run_id}/golden/resolve`: operation nền GOLDEN_RESOLVE, scope đóng băng và bốn revision kỳ vọng; replay theo request ID.
- `GET /mdm/runs/{run_id}/golden/resolution`: kết quả gần nhất, đề xuất, số xác nhận hiện tại và tính stale.
- `GET /mdm/runs/{run_id}/golden/resolution/changes`: phân trang các liên kết của đề xuất.
- `POST /mdm/runs/{run_id}/golden/resolution/apply`: chỉ nhận operation/proposal ID của server, yêu cầu quyền approve và reject.
- `POST /mdm/runs/{run_id}/golden/resolution/undo`: khôi phục các quyết định đã lưu nếu fence vẫn hợp lệ.
- Artifact kế hoạch và dữ liệu trước thay đổi lưu tại R2 có hash/readback. Operation, snapshot, membership, Golden và audit sử dụng các bảng sẵn có; không cần migration.
- Generation PARTIAL giữ artifact preview đã kiểm chứng và membership được công bố riêng. API Golden preview trả trạng thái thực tế DRAFT/CONFIRMED và `provisional` theo membership; bộ lọc CONFIRMED không trả bản dự kiến.

## Kiểm chứng

- Unit/integration backend: phạm vi lựa chọn, công bố riêng, chọn thủ công, Deny, sửa/hoàn tác, stale, artifact bị sửa, phân quyền và thực thể nhất quán vượt 20.
- Frontend: scope từng nhóm/tất cả/loại trừ, trước/sau, nguồn giải thích, duyệt/hoàn tác và lựa chọn chưa lưu.
- Browser trên Docker/R2: dữ liệu giả lập riêng; đối chiếu trạng thái chính thức qua API và sau reload, không sửa phiên của người dùng.

Giới hạn: chưa chứng nhận chất lượng trên tập ground truth hoặc tải 100k/1M. Tính năng đưa ra đề xuất thận trọng khi thiếu bằng chứng, không đảm bảo mọi xung đột đều có thể tự giải.
