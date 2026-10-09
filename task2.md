# Large file upload

1. The frontend requests a presigned upload URL from the backend.
2. The backend creates a short-lived S3-compatible Cloudflare R2 URL.
3. The browser uploads the file directly to R2 with progress reporting.
4. The frontend notifies the backend after the upload completes.

This design keeps file bytes out of the API process and stores uploaded files
under the `incoming/` prefix in the configured R2 bucket.
