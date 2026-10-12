# CORS cho bucket QA

Trong Cloudflare: **R2 Object Storage → masterone-qa → Settings → CORS Policy →
Add/Edit → JSON → Save**. Giữ các rule hiện có và thêm rule bên dưới. Bucket vẫn
private; ứng dụng dùng presigned URL.

```json
[
  {
    "AllowedOrigins": ["http://127.0.0.1:13018", "http://localhost:13018"],
    "AllowedMethods": ["GET", "HEAD", "PUT"],
    "AllowedHeaders": ["content-type", "x-amz-checksum-sha256"],
    "ExposeHeaders": ["ETag", "x-amz-checksum-sha256"],
    "MaxAgeSeconds": 300
  }
]
```

CORS là cấu hình, không phải API key. Credential có quyền đọc/ghi object có thể
không có quyền sửa CORS. Dashboard JSON dùng định dạng trên; Wrangler dùng schema
khác. Xem [tài liệu Cloudflare](https://developers.cloudflare.com/r2/buckets/cors/).
Thêm origin HTTPS staging chính xác khi đã có target; không dùng wildcard để che
lỗi origin. Lưu rule chưa đủ chứng minh PASS: cần browser PUT thật, không proxy,
validate/commit và đối soát checksum/restart/download.

2026-10-11: người dùng xác nhận đã lưu cấu hình; kiểm chứng được ghi ở handoff S11.
