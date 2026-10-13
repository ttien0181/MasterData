"use client";

import { useState } from "react";
import { putToSignedUrl, type UploadProgress } from "@/lib/largeUpload";
import { backendHeaders } from "@/lib/backendHeaders";

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function LargeFileUpload({ csrfToken }: { csrfToken: string }) {
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function upload() {
    if (!file) return;
    setBusy(true);
    setMessage("");
    try {
      const urlResponse = await fetch(
        `${apiBase}/api/v1/large-imports/upload-url`,
        {
          method: "POST",
          credentials: "include",
          headers: backendHeaders({
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken,
          }),
          body: JSON.stringify({
            filename: file.name,
            content_type: file.type || "text/csv",
          }),
        },
      );
      if (!urlResponse.ok) throw new Error("Không lấy được quyền upload");
      const { object_name, upload_url } = await urlResponse.json();
      await putToSignedUrl(upload_url, file, setProgress);
      const complete = await fetch(`${apiBase}/api/v1/large-imports/complete`, {
        method: "POST",
        credentials: "include",
        headers: backendHeaders({
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        }),
        body: JSON.stringify({ object_name }),
      });
      if (!complete.ok)
        throw new Error("Upload xong nhưng chưa khởi chạy xử lý");
      setMessage(
        "Đã lưu file thành công trên Cloudflare R2.",
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload thất bại");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Upload file lớn">
      <input
        type="file"
        accept=".csv,text/csv"
        onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        disabled={busy}
      />
      <button
        type="button"
        onClick={() => void upload()}
        disabled={!file || busy}
      >
        {busy ? "Đang upload…" : "Upload trực tiếp lên R2"}
      </button>
      {progress && (
        <progress
          value={progress.percent}
          max="100"
          aria-label={`Đã upload ${progress.percent}%`}
        />
      )}
      {progress && <span>{progress.percent}%</span>}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
