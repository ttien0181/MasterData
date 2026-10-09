# Kiểm tra CP09 — Review ba cột

## Đã triển khai

Màn review giữ đồng thời ba vùng: hàng đợi và bộ lọc, đối chiếu dữ liệu nguồn, giá trị đang chọn kèm hành động. Chọn item tải đúng chi tiết theo API hiện hữu; các lựa chọn vẫn gắn với `source_record_id`. Panel quyết định hiển thị trạng thái chọn, giải thích bằng chứng, lưu lựa chọn từng phần, Không gộp hoặc đưa sang Chờ gộp. Viewer chỉ xem và không thể thực thi action. Các nhánh cụm xung đột/tách an toàn và export blocker vẫn giữ nguyên.

UI không hiển thị điểm score như xác suất. Khi đóng panel có lựa chọn chưa lưu, nút đóng được gắn nhãn “Hủy thay đổi”; thao tác trả focus về item vừa chọn. Trên màn hẹp, các cột xếp dọc.

## Kiểm tra tự động

Từ `frontend`:

```powershell
npx vitest run src/components/workspace.test.tsx
npm test -- --run
npm run lint
npm run build
```

Kết quả lần chạy này: kiểm thử `workspace.test.tsx` 20/20 pass; toàn bộ frontend 8 file / 37 test pass; TypeScript lint và Next production build pass.

## Kiểm tra thủ công

1. Mở một run có item Cần review; xác nhận hàng đợi, so sánh và panel quyết định hiện cùng lúc.
2. Chọn các item khác nhau; trường bằng nhau được gom, trường khác nhau cho chọn nguồn và hiển thị source ID.
3. Lưu một phần rồi mở lại item; xác nhận lựa chọn còn đúng. Thử hủy thay đổi chưa lưu và dùng Escape/keyboard.
4. Duyệt, Không gộp, chọn hàng loạt, mở cụm có `can_merge=false` và tách cụm an toàn; kiểm tra các giới hạn quyền server-side.
5. Thử light/dark và viewport desktop/tablet/mobile; không có thanh cuộn ngang và action không bị dock che.

Chưa chạy browser E2E hoặc pixel comparison với mockup; phần này nằm trong GĐ7.
