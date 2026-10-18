## Thay đổi gì

<!-- Mô tả ngắn gọn: vấn đề, cách giải quyết, phạm vi ảnh hưởng. -->

## Loại thay đổi

- [ ] `fix` — sửa lỗi
- [ ] `feat` — tính năng mới
- [ ] `perf` / `refactor`
- [ ] `docs` — chỉ tài liệu
- [ ] `chore` / `test`

## Checklist

- [ ] `ruff check backend/src backend/tests backend/migrations` sạch
- [ ] pytest các file liên quan xanh; `npm --prefix frontend test`, `lint`, `build` xanh nếu đụng frontend
- [ ] Nếu có migration: additive, có test, đã ghi vào PR cách nâng cấp
- [ ] Nếu đổi hành vi engine: nằm sau cờ cấu hình, ghim theo run, có test hồi quy
- [ ] Đã cập nhật tài liệu liên quan (`README.md`, `docs/`, `ARCHITECTURE.md`)
- [ ] **Không có API key / token / credential / dữ liệu khách hàng thật** trong diff
