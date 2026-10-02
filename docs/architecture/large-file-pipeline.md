# Large-file upload and streaming CSV merge on Cloudflare R2

The browser requests a short-lived S3-compatible presigned URL from FastAPI,
uploads the file directly to Cloudflare R2, then notifies the API that the
upload is complete. The API does not receive the file bytes. The frontend then
sends the object keys to `/large-imports/merge`.

```text
Browser -> FastAPI: request upload URL
FastAPI -> Browser: R2 presigned PUT URL
Browser -> Cloudflare R2: PUT file with progress
Browser -> FastAPI: complete upload with object name
Browser -> FastAPI: merge object keys
FastAPI -> Cloudflare R2: stream download, align rows, stream upload
FastAPI -> Cloudflare R2: delete sources after successful upload
```

The backend reads each object's CSV header from R2 before processing its rows.
Columns present in every input are placed first; remaining columns are appended
once in input-file order. Rows are aligned by header name, and absent columns
are emitted as empty values. `source_record_id` is normalized to `record_id`.
The resulting column list is returned by the merge endpoint. Temporary disk
files are used, so complete source or output files are never loaded into RAM.

Configure R2 CORS for the frontend origin and give the API token access only
to the configured bucket. Objects are stored below `incoming/` with a generated
UUID and a sanitized filename.
