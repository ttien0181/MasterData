> Cập nhật source/runtime ngày 12/10/2026: xem [DATA_OVERVIEW_20261012.vi.md](DATA_OVERVIEW_20261012.vi.md). Nội dung dưới đây mô tả candidate lịch sử, không phải endpoint/runtime hiện tại.

# Thiết kế lại tổng quan dữ liệu — 11/10/2026

## Giao diện

- Bốn thẻ: tổng bản ghi, số nguồn, số tệp, số bản ghi có ít nhất một cảnh báo.
- Biểu đồ vòng theo nguồn; thanh tỷ lệ có thông tin cho họ tên, điện thoại, email, địa chỉ, ngày sinh và giấy tờ; cảnh báo chất lượng theo loại.
- Các thống kê lấy toàn bộ dữ liệu của phiên. Tỷ lệ có thông tin tính trên trường gốc khác null / không rỗng / không chỉ chứa khoảng trắng; không phải điểm hợp lệ hoặc kết quả phân loại trùng lặp.
- Bảng có các cột STT, mã/nguồn, họ tên, điện thoại, email, địa chỉ, chất lượng. Chuyển gốc/chuẩn hóa và mở chi tiết để xem các trường khác.
- 10 bản ghi/trang, nút đầu/trước/sau/cuối. Khi tải trang thất bại, giữ trang đã tải và cho phép thử lại trang đang yêu cầu.
- Bố cục thích ứng với điện thoại, bảng cuộn ngang riêng, dùng màu của chủ đề sáng/tối.

Tham khảo cách trình bày profiling theo thuộc tính và dữ liệu thiếu của [Ataccama ONE](https://docs.ataccama.com/ataccama-one-agentic/latest/data-quality/understand-profiling-results.html) và [Microsoft Purview](https://learn.microsoft.com/en-us/purview/unified-catalog-data-quality-profiling). Không hiển thị điểm chất lượng hoặc số lượng bản ghi trùng khi chưa có chỉ số được xác định rõ.

## API và tương thích

`GET /api/v1/mdm/runs/{run_id}/data-profile?offset=0&limit=10` mặc định 10, tối đa 100. Giữ tên `samples` cho tương thích client; nội dung lấy từ toàn bộ bảng record của phiên, sắp xếp theo ID, SQL OFFSET/LIMIT. `sample_total` là tổng bản ghi, không còn giới hạn 100 ví dụ.

Profile mới lưu `field_completeness` và `records_with_quality_flags` cùng các thống kê khi PREPARE. Profile cũ được kiểm tra chủ sở hữu và hash trước khi đọc; chỉ dẫn xuất thống kê thiếu bằng luồng 512 dòng, không sửa profile, manifest hoặc input hash, không chạy lại chuẩn hóa hay phân loại. Với profile cũ, việc đọc thống kê vẫn quét các trường gốc/cảnh báo của toàn bộ phiên trên mỗi yêu cầu; profile mới dùng thống kê đã lưu.

## Xác minh

- Backend: 16 kiểm thử profile, publication và workflow API; bao gồm 125 bản ghi, trang vượt 100 mẫu, trường rỗng, thống kê không đổi giữa các trang, tương thích profile cũ, quyền sở hữu và hash hỏng.
- Frontend: 78 kiểm thử; TypeScript và Docker production build thành công.
- Trình duyệt trên phiên QA hiện có: 1.651 bản ghi, 166 trang, trang đầu 10 dòng, trang cuối 1 dòng; chuyển gốc/chuẩn hóa, mở chi tiết, không có lỗi JavaScript, không tràn chiều ngang của trang trên màn hình 390px.
- Chỉ cập nhật backend, worker và frontend của `masterone-workflow-live`; giữ cấu hình runtime và dữ liệu đã nhập.

Bằng chứng nằm tại `artifacts/session-workflow/20261011-workflow-01/data-overview-20261011T060026Z-15252325/`. Ảnh desktop/mobile/dark thay dữ liệu nhận diện trong các ô bằng ví dụ để kiểm tra bố cục; kiểm thử hành vi chạy với dữ liệu API thực trước khi thay nội dung ảnh.
