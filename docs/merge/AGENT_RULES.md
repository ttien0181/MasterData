# Quy tắc bắt buộc cho mọi phiên migration tiếp theo

Đọc file này, `STATUS.md`, `CONTRACTS.md`, `BASELINE.md` và `PROJECT_MAP.md` trước khi làm việc. Đây là quy tắc của chương trình migration, không phải giấy phép triển khai toàn bộ backlog trong một phiên.

## Phạm vi và quyền sở hữu

1. Chỉ sửa P-119: `C:\VinUni\Dự án nhóm\P-119`. OneMaster tại `C:\VinUni\Dự án nhóm\onemaster-workspace` chỉ đọc. Xác minh lại bằng `git rev-parse --show-toplevel`; không giả định đường dẫn còn đúng.
2. Kiểm tra `git status --short` và staged diff ở cả hai repository. Không xóa, reset, checkout đè, clean hoặc stage các thay đổi có sẵn của người dùng.
3. Không thay đổi frontend nếu không bắt buộc. Nếu thật sự bắt buộc, giải thích nhu cầu và phạm vi, tuân thủ `frontend/AGENTS.md`, bảo toàn giao diện và có test tương ứng; không tự redesign.
4. Không làm mất hoặc ghi đè quyết định review, golden override, membership/history, preview/export, negative edge hoặc audit. Retry không được reset quyết định người dùng thành UNDECIDED.
5. Không đổi nghiệp vụ ngầm khi tối ưu hiệu năng: candidate coverage, corpus frequency, score semantics, policy, thứ tự union, định nghĩa metric và đơn vị đếm đều là hợp đồng.
6. Không nuốt exception. Ghi nhận nguyên nhân có cấu trúc, không lộ PII/credential; fallback phải rõ ràng, có phạm vi và test. Không mở rộng việc sửa toàn bộ exception legacy ngoài task.
7. Mọi thay đổi phải có test phù hợp. Documentation-only cần kiểm tra tính chính xác, links/path và `git diff --check`. Code phải có test hành vi hoặc contract; migration DB cần kiểm thử bảo toàn dữ liệu.
8. Giữ backward compatibility hoặc cung cấp migration rõ ràng: schema/version/backfill/rollback và kiểm chứng dữ liệu. Không đổi ID hoặc enum chỉ để vừa implementation tham chiếu.
9. Feature flag mặc định tắt đối với đường chạy mới chưa kiểm định. Chạy shadow/equivalence trước khi đổi default; giữ đường cũ có khả năng rollback.
10. Không tự nhận test PASS nếu chưa thực thi. Báo command, môi trường, kết quả, fail/skip/blocker và report. Không coi test skip, collection error, smoke nhỏ hoặc tên "memory test" là bằng chứng production.
11. Nếu task vượt phạm vi, ghi nhận trong Pending/Dependencies thay vì tự triển khai nhiều module không liên quan. Chỉ thực hiện Next task được giao/cho phép.
12. Không chạy test/migration trên DB doanh nghiệp hoặc dịch vụ R2/LLM thật nếu chưa có phạm vi rõ ràng. Không dump `.env`, DSN hay dữ liệu người dùng vào tài liệu/log/commit.
13. Không import trực tiếp source OneMaster bằng đường dẫn bên ngoài vào runtime P-119. Chuyển giao có chọn lọc, dependency và license/provenance rõ ràng, test trong P-119.
14. Không spawn sub-agent nếu người dùng hoặc instruction áp dụng chưa cho phép delegation.

## Chu trình của một task

- Đọc luồng chạy thực và contract; không coi comment/README là bằng chứng implementation.
- Chạy baseline bằng `scripts/run_merge_baseline.py` trong môi trường riêng. Không sửa assertion/test cũ để làm xanh một thay đổi nghiệp vụ ngoài phạm vi.
- Phân biệt local-ready, durable checkpoint và publication. Không union theo thứ tự batch hoàn thành; không công bố generation chưa đủ dữ liệu.
- Stage/commit chỉ các file mình thay đổi bằng đường dẫn cụ thể; không `git add .`.
- Cập nhật `STATUS.md` đủ: Completed / Modified files / Tests / Pending / Dependencies / Commit / Next task.
- Báo cáo final phải nêu thay đổi, test thật, rủi ro còn lại, commit và bước tiếp theo.
