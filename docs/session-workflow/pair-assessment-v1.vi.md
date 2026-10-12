# Đánh giá cặp bản ghi v1

Luồng mới: ràng buộc và luật → mô hình bằng chứng mềm đã kiểm định trong phạm vi cho phép → LLM cho cặp còn mơ hồ và có dữ liệu hữu ích → người duyệt Accept/Deny → kiểm tra nhóm và cannot-link.

## Group và Subgroup

| Phân loại | Ý nghĩa | Ví dụ Subgroup |
| --- | --- | --- |
| A | Có điểm neo cùng thông tin tên phù hợp, cần người duyệt | Tên + điện thoại; tên + email; tên + điện thoại + ngày sinh |
| C | Chưa đủ căn cứ, kể cả dữ liệu quá ít | Thiếu điểm neo; thiếu thông tin đối chiếu; không có dữ liệu hữu ích |
| D | Mâu thuẫn mềm cần đối chiếu | Ngày sinh khác; nhiều trường mâu thuẫn; giấy tờ chưa xác minh khác nhau |
| Bị chặn | Ràng buộc cứng | Khác loại thực thể; giấy tờ/ngày sinh đã xác minh mâu thuẫn; cannot-link |

Mỗi cặp có một Group/Subgroup khi được đánh giá mới. Biến đổi chuẩn hóa B nằm trong bằng chứng, không phải nhóm quyết định riêng. Group là loại bằng chứng của **cặp**, không phải nhóm khách hàng sau clustering.

`VALID` chỉ là đúng định dạng. Xung đột giấy tờ chặn cứng cần cả hai đã xác minh, cùng loại/phạm vi cấp và đáp ứng hợp đồng định danh duy nhất, một giá trị. CSV giữ riêng metadata `government_id_verified`/`government_id_verification_status`, namespace, uniqueness và single-valued; thiếu metadata là unknown, không suy ra từ độ dài hay định dạng.

## Mô hình và phạm vi kiểm định

Chạy `scripts/train_pair_review_model.py` với TC4 10k seed-7 và output `data/models/pair-review-v2`. Mô hình Fellegi–Sunter/Splink học phân phối m/u ở train; hiệu chỉnh Platt và chọn dải đề xuất trên dev; test đóng băng. Train/dev/test gồm 3.150/675/675 bản ghi, tách trước khi sinh biến thể. Từ chối nguồn hoặc mẫu dữ liệu trùng và kiểm tra riêng entity/source/template overlap. Train và runtime dùng cùng chuẩn hóa và mask giá trị invalid/placeholder. Runtime kiểm tra phiên bản, phạm vi, hash mã chuẩn hóa, hash model/report/snapshot và điều kiện kiểm định.

Đây là **nhãn thực thể synthetic tạo theo kịch bản**, không phải nhãn gốc TC4: file nhãn E… của phần archive hiện tại không nối được với mã nguồn băm trong records.parquet. Không suy ra nhãn từ score hay LLM. Báo cáo [pair-review-synthetic-v2.json](../benchmark/pair-review-synthetic-v2.json) ghi rõ giới hạn. Xác suất hiệu chỉnh chỉ ứng với cách lấy mẫu cặp synthetic; không chứng nhận độ chính xác hay xác suất cho dữ liệu thực tế.

Chỉ khi request reassess xác nhận `model_scope=synthetic-benchmark` mới áp dụng mô hình này. Phiên nhập mới tự đánh giá luật; mô hình mặc định không áp dụng cho dữ liệu chưa xác nhận phạm vi. Model/LLM không thay đổi quyền Accept, không tạo cannot-link và không tự ghi quyết định.

## LLM

`POST /workflow/pair-advice` tạo job riêng, tối đa 100 cặp mỗi đợt và chịu ngân sách tổng/per-run hiện có. Bỏ qua cặp đã duyệt, bị chặn, quá ít dữ liệu, mẫu luật rõ ràng hoặc mô hình đã phân định rõ. Mâu thuẫn giữa mô hình và luật có thể đưa vào đối chiếu.

Payload chỉ có tên/địa chỉ cần đối chiếu, token tạm và trạng thái so sánh; không gửi giá trị đầy đủ điện thoại/email/giấy tờ/ngày sinh, raw_fields hay nhãn. LLM trả một trong ba đề xuất cùng bằng chứng field/state và mã lý do/giới hạn. Backend đối chiếu state với payload và render lý do tiếng Việt từ mã đã kiểm tra. Văn bản tự do của provider không được dùng để khẳng định thông tin đã xác minh. Provider/model/request/prompt/schema/input hash được lưu riêng trong advice và artifact kiểm toán. Gọi lỗi, hết ngân sách hoặc thiếu căn cứ không đổi quyết định.

## Phiên cũ và cache

`POST /workflow/reassess` tính lại đúng tập candidate đã có. Artifact đánh giá cũ bất biến; baseline, Accept/Deny, revision từng cặp, cannot-link và lịch sử được giữ. Con trỏ assessment và cache hiệu lực được công bố trong cùng transaction. Có kiểm tra revision, nguồn dữ liệu, profile, mã đánh giá, tập cặp và quyền sở hữu job trước công bố. Accept cũ gặp ràng buộc mới được báo conflict riêng.

Finalize chụp snapshot phiên bản đánh giá hiệu lực, overlay quyết định và cannot-link hiện hành. Clustering tiếp tục kiểm tra liên kết gián tiếp và toàn bộ nhóm. Đánh giá mới yêu cầu người duyệt kể cả mẫu A rõ ràng.

TanStack Query dùng assessment identity và advice revision riêng cho trang cặp. Chuyển Group giữ trang/bộ lọc/vị trí và dùng cache; dữ liệu sau job advice không dùng lẫn với cache cũ. Danh sách giữ 10 cặp/trang.

## Triển khai và vận hành

Chỉ sửa `docker-compose.yml` hiện có. Backend/worker dùng `PAIR_ASSESSMENT_AUTO_ENABLED=true`, `PAIR_REVIEW_MODEL_PATH=/app/data/models/pair-review-v2/manifest.json`, `LLM_PAIR_ASSESSMENT_ENABLED=true`; các điều kiện provider/retention/key/budget cũ vẫn phải đạt. Migration additive `0029_pair_assessment` chạy rõ ràng trước đổi image; không xóa lịch sử khi downgrade.

Reassessment đọc projection ID/endpoints, ghi cập nhật theo lô và báo tiến độ mỗi 2.000 cặp để giảm round-trip đến database. Worker giữ lease/heartbeat và publication fence; có thể retry sau gián đoạn mà không ghi đè quyết định.

## Đối chiếu phiên TC4 đang chạy

Phiên `3449137c-8f4d-41cc-9152-fa12768aa355` có 79.364 cặp. Phân loại mới: A 34, C 19, D 61.672, bị chặn 17.639; không còn cặp chưa phân nhóm. 15.369 Accept trước đó được giữ nguyên cùng baseline, revision quyết định, cannot-link và lịch sử kiểm toán.

Có 9.221 Accept cũ thuộc `PERSON_POLICY_OUT_OF_SCOPE`: ngoài phạm vi đánh giá người của chính sách hiện tại. Đây là cảnh báo phải đối chiếu trước khi tạo nhóm, không phải kết luận tự động rằng hai tổ chức khác nhau. Việc mở rộng đối sánh tổ chức cần hợp đồng riêng; mô hình synthetic người và LLM không được bỏ qua chặn này.

Lượt canary giới hạn 3 cặp trên phiên này đã gọi Gemini thành công: 2 đề xuất có khả năng khác người, 1 thiếu căn cứ; schema và field/state đều được kiểm tra trước khi lưu riêng. Không thay đổi Accept/Deny. 78.320 cặp rõ ràng, bị chặn hoặc đã duyệt được bỏ qua; 1.041 cặp còn đủ điều kiện được giữ cho các lượt sau theo giới hạn và ngân sách. Đây là kiểm tra tích hợp provider, không phải kiểm định độ chính xác của LLM.

[Đối chiếu sau đánh giá và LLM](../benchmark/live-pair-assessment-20261012.json) đạt đủ 7 kiểm tra fingerprint/phủ cặp. Kiểm tra trình duyệt sau khi job hoàn tất đạt 10 cặp/trang, không còn option chưa phân nhóm, công bố phạm vi synthetic, dùng lại cache Group và không có lỗi JavaScript. Hồi quy cuối: 114 kiểm tra frontend, 88 kiểm tra backend workflow/LLM và TypeScript đều đạt.
