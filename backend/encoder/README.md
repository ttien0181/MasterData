# Runtime encoder E5 Linux CPU (tùy chọn, không dùng ở cấu hình mặc định)

Thư mục này đóng gói encoder cục bộ `intfloat/multilingual-e5-small` (manifest băm cố định trong `model-manifest.json`, phụ thuộc đã khóa trong `requirements-linux.txt`) để chạy embedding **không qua API**. Cấu hình hiện hành dùng embedding qua OpenRouter, và `docker-compose.yml` luôn đặt `AI_API_ONLY=true`, cấm hoàn toàn đường E5/Splink cục bộ (`API_ONLY_LOCAL_ENCODER_DENIED` khi `EMBEDDING_PROVIDER=e5_local`). Chỉ dùng thư mục này cho thực nghiệm benchmark cũ.

Muốn chạy thử:

1. Đặt `AI_API_ONLY=false` và `EMBEDDING_PROVIDER=e5_local` cho backend/worker.
2. Chuẩn bị trọng số trên máy chủ: `scripts/fetch_m2a_e5.py`, rồi `scripts/prepare_encoder_linux_assets.py`.
3. Tự mount thư mục mô hình (chỉ đọc) vào container và đặt `EMBEDDING_RUNTIME_ROOT` tới runtime đã dựng; Compose hiện tại **không** mount sẵn.
4. Build lại image `backend` và `worker`.

Ràng buộc giữ nguyên: không tải model lúc suy luận, không thay model, không đưa dữ liệu khách hàng hay khóa API vào image; hash của `requirements-linux.txt` được kiểm tra bởi supervisor và encoder worker. Mô hình chỉ được bật qua profile matching hợp lệ của chủ sở hữu; run cũ giữ binding bất biến. Số token ghi nhận gồm tiền tố E5 và token đặc biệt; CPU, hosting, điện là chi phí ngoài đo lường.
