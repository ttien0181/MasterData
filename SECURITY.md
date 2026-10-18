# Chính sách bảo mật

## Báo cáo lỗ hổng

**Không** mở public issue cho lỗ hổng bảo mật.

Dùng [GitHub Security Advisories](https://github.com/AI20K-Build-Phase-Cohort-4/P-119/security/advisories/new)
để báo cáo riêng tư. Vui lòng mô tả: loại lỗ hổng, file/dòng liên quan, các bước
tái hiện, và tác động bạn đánh giá.

Chúng tôi sẽ phản hồi trong vòng 7 ngày làm việc.

## Nếu bạn lỡ commit một API key

Xoá key khỏi file rồi commit tiếp là **chưa đủ** — giá trị cũ vẫn nằm trong git
history và trong mọi bản clone/fork đã tồn tại. Coi như key đã lộ hoàn toàn.

Làm theo thứ tự này:

1. **Xoay key ngay (rotate).** Đây là bước duy nhất thật sự vô hiệu hoá key cũ.
   Mọi bước còn lại chỉ là dọn dẹp.
2. Thay bằng placeholder trong file, commit.
3. Nếu cần xoá khỏi history, dùng
   [git-filter-repo](https://github.com/newren/git-filter-repo) rồi force-push.
   Lưu ý việc này viết lại history, mọi người phải re-clone.
4. Báo maintainer để kiểm tra fork và cache của GitHub.

## Bảo mật khi phát triển và vận hành

- **Không bao giờ** đặt key thật vào `.env.example`, `.env.qa.example` hay `.env.production.example` — các file này được commit. Key thật đặt trong `.env`, `.env.qa`, `.env.production` (đã có trong `.gitignore`).
- `AI_LOG_API_KEY` là key **riêng của từng đội/thành viên** để ghi log dùng công cụ AI cho chương trình; khác với `GEMINI_API_KEY`, `OPENROUTER_API_KEY` và `EMBEDDING_OPENROUTER_API_KEY` của ứng dụng. Không chia sẻ, không commit.
- Dữ liệu khách hàng thật không được nạp vào bản demo và không được commit. Dữ liệu trong repo (`masterone_test/`, `eval/data/`, dữ liệu demo) là dữ liệu giả lập.
- Ứng dụng chưa có xác minh email, khôi phục mật khẩu, MFA và chống đăng ký tự động; không mở Internet công khai khi chưa bổ sung (xem [docs/OPERATIONS.md](docs/OPERATIONS.md#4-triển-khai-production)).
- Trước khi push, tự kiểm tra nhanh:
  ```bash
  git diff --cached | grep -iE "api[_-]?key|secret|token|password"
  ```
- Thư mục `.ai-log/` chứa nội dung prompt của bạn. Kiểm tra trước khi public một repo có sẵn log — prompt có thể vô tình chứa dữ liệu nhạy cảm.
