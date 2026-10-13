import { backendHeaders } from "./backendHeaders";

export type UploadProgress = { loaded: number; total: number; percent: number };

export function putToSignedUrl(
  url: string,
  file: File,
  onProgress: (progress: UploadProgress) => void,
  contentType = file.type || "text/csv",
): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("PUT", url);
    request.setRequestHeader(
      "Content-Type",
      contentType,
    );
    request.upload.onprogress = (event) => {
      if (event.lengthComputable)
        onProgress({
          loaded: event.loaded,
          total: event.total,
          percent: Math.round((event.loaded / event.total) * 100),
        });
    };
    request.onload = () =>
      request.status >= 200 && request.status < 300
        ? resolve()
        : reject(new Error(`R2 upload failed (${request.status})`));
    request.onerror = () =>
      reject(new Error("Network error while uploading to R2"));
    request.onabort = () => reject(new Error("Upload cancelled"));
    request.send(file);
  });
}

export type R2Upload = { object_name: string; filename: string };
export type R2MergeResult = {
  job_id: string;
  output_object_key: string;
  rows_written: number;
  columns: string[];
  sources: { object_key: string; source: string; rows: number }[];
};

export async function mergeR2Files(objectKeys: string[], csrfToken: string): Promise<R2MergeResult> {
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  const response = await fetch(`${apiBase}/api/v1/large-imports/merge`, {
    method: "POST",
    credentials: "include",
    headers: backendHeaders({
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
    }),
    body: JSON.stringify({ object_keys: objectKeys, cleanup_sources: true }),
  });
  if (!response.ok) {
    const result = await response.json().catch(() => null);
    const detail = typeof result?.detail === "string" ? result.detail : `CSV merge failed (${response.status})`;
    throw new Error(detail);
  }
  return response.json() as Promise<R2MergeResult>;
}

export async function uploadFilesToR2(
  files: File[],
  csrfToken: string,
  onProgress: (file: File, progress: UploadProgress) => void,
): Promise<R2Upload[]> {
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  const uploads: R2Upload[] = [];

  for (const file of files) {
    const contentType = file.type || "text/csv";
    const signedResponse = await fetch(`${apiBase}/api/v1/large-imports/upload-url`, {
      method: "POST",
      credentials: "include",
      headers: backendHeaders({
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      }),
      body: JSON.stringify({ filename: file.name, content_type: contentType }),
    });
    if (!signedResponse.ok) {
      const result = await signedResponse.json().catch(() => null);
      const detail = typeof result?.detail === "string" ? result.detail : `Không xin được signed URL (${signedResponse.status})`;
      throw new Error(`${file.name}: ${detail}`);
    }

    const { object_name, upload_url } = await signedResponse.json();
    await putToSignedUrl(upload_url, file, (progress) => onProgress(file, progress), contentType);

    const completeResponse = await fetch(`${apiBase}/api/v1/large-imports/complete`, {
      method: "POST",
      credentials: "include",
      headers: backendHeaders({ "Content-Type": "application/json", "X-CSRF-Token": csrfToken }),
      body: JSON.stringify({ object_name }),
    });
    if (!completeResponse.ok) {
      const result = await completeResponse.json().catch(() => null);
      const detail = typeof result?.detail === "string" ? result.detail : `Không xác nhận được upload (${completeResponse.status})`;
      throw new Error(`${file.name}: ${detail}`);
    }
    uploads.push({ object_name, filename: file.name });
  }

  return uploads;
}
