# Bằng chứng kiểm thử manual — MasterOne MVP

Ngày thực hiện: **04/10/2026**. Môi trường: frontend Next.js local, API FastAPI local, SQLite; dữ liệu demo `masterone_test/crm.csv`, `erp.csv`, `bill.csv` (mỗi file 7.000 dòng). Đây là **5 ca quan sát từ thao tác trên sản phẩm** do thành viên nhóm chụp lại; số liệu CSV bên dưới được đọc lại từ file đã xuất.

| ID | Mục tiêu và thao tác | Mong đợi | Kết quả thực tế quan sát được | Kết luận | Bằng chứng |
|---|---|---|---|---|---|
| TC01 | Vào Tổng quan bằng phiên đã đăng nhập, trước khi nạp CSV. | Hiện không gian làm việc của tài khoản và trạng thái chưa có run. | Tổng quan hiển thị tài khoản, `0` Cần review, `0` Chờ gộp và lời mời nạp dữ liệu. Ảnh không ghi lại thao tác nhập mật khẩu, nên ca này chỉ xác nhận trạng thái **sau đăng nhập**. | Đạt trong phạm vi quan sát | [Ảnh TC01](Screenshot%202026-10-04%20222655.png) |
| TC02 | Nạp ba CSV CRM, ERP, BILL và chờ pipeline hoàn tất. | Run xử lý đủ 21.000 bản ghi, hiện kết quả phân giải. | Ảnh Tổng quan ghi run `4191b12c…`, **21.000 bản ghi / 3 file**, tiến độ 100%, **5.992** item Chờ gộp và **1.747** item Cần review; biểu đồ ghi **7.739** cụm. SQLite local của run này ghi mỗi file 7.000 dòng. | Đạt | [Ảnh TC02](Screenshot%202026-10-04%20222917.png) |
| TC03 | Mở danh sách Chờ gộp. | Thấy item được hệ thống đề xuất và có thể mở để so sánh. | Danh sách hiển thị **5.992 item**; các item mẫu có nhãn **Tự động**, số bản ghi trong cụm (ví dụ 4) và hành động **Xem so sánh**. Đây là bằng chứng hiển thị danh sách, chưa xác nhận giá trị của từng trường trong popup. | Đạt | [Ảnh TC03](Screenshot%202026-10-04%20223006.png) |
| TC04 | Mở Cần review, chọn hai item rồi đưa sang Chờ gộp. | Hàng đợi review giảm 2 và giao diện xác nhận thao tác. | Ảnh Tổng quan trước thao tác ghi **1.747 item Cần review**; ảnh sau ghi **1.745 item** và thông báo **“Đã đưa 2 item sang Chờ gộp.”** Con số Chờ gộp trên ảnh sau vẫn là 5.992; thao tác chuyển chưa chứng minh hai item đã đủ điều kiện xuất CSV. | Đạt cho thao tác chuyển khỏi review | [Trước: Tổng quan](Screenshot%202026-10-04%20222917.png) · [Sau](Screenshot%202026-10-04%20223856.png) |
| TC05 | Kiểm tra điều kiện xuất, hoàn tất lựa chọn, xem trước và xuất CSV. | Chặn khi còn trường chưa chọn; sau khi xử lý phải tạo được CSV với các bản ghi nguồn được bao phủ. | Ảnh đầu báo **2 item cần xử lý** và chỉ rõ các trường chưa chọn. Ảnh sau cho xem trước **7.628 dòng**: **5.992** cụm Chờ gộp và **1.636** bản ghi giữ riêng; **1.745** item chưa review được cảnh báo là giữ riêng. File CSV thực tế có **7.628 dòng**, gồm **5.992 MERGED** và **1.636 UNMERGED**; tổng `record_count` và số ID nguồn duy nhất đều bằng **21.000**. | Đạt | [Bị chặn](Screenshot%202026-10-04%20223921.png) · [Xem trước](Screenshot%202026-10-04%20224055.png) · [CSV kết quả](masterone-7628.csv) |

## Đối chiếu file đầu ra

Đọc `masterone-7628.csv` bằng `csv.DictReader` với UTF-8 BOM:

| Kiểm tra | Giá trị thực tế |
|---|---:|
| Dòng dữ liệu | 7.628 |
| `master_record_id` duy nhất | 7.628 |
| Dòng `MERGED` | 5.992 |
| Dòng `UNMERGED` | 1.636 |
| Tổng `record_count` | 21.000 |
| ID nguồn duy nhất trong `source_record_ids` | 21.000 |

SHA-256 của file: `ec96ce01e5b01187ec2662757ee532a7aafaec50736160f3e6983461b3a489f2`. File này khớp bản CSV snapshot đã lưu trong SQLite local của run `ae77e7d5…` (7.628 dòng). Có thể kiểm tra lại bằng `Get-FileHash eval/results/masterone-7628.csv -Algorithm SHA256`. File `masterone.csv` hiện có trong thư mục là một lần xuất khác và không dùng cho báo cáo này.

## Giới hạn của bộ bằng chứng

Ảnh Tổng quan ở TC02 ghi run `4191b12c…`, còn CSV cuối được đối chiếu với run `ae77e7d5…`. Hai run dùng cùng ba file đầu vào nhưng **không phải một run duy nhất**; vì vậy không dùng bộ ảnh này để khẳng định một lần chạy liên tục từ TC01 đến TC05. Ảnh chưa ghi màn hình Lịch sử sau khi xuất, chi tiết lựa chọn trong popup, đường **Không gộp**, hoặc phản hồi Gemini. Các nội dung đó không được tính là đã kiểm thử manual trong báo cáo này.
