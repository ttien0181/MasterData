"use client";

import { useState } from "react";
import { putToSignedUrl, type UploadProgress } from "@/lib/largeUpload";

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
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken,
          },
          body: JSON.stringify({
            filename: file.name,
            content_type: file.type || "text/csv",
          }),
        },
      );
      if (!urlResponse.ok) throw new Error("Chưa thể bắt đầu tải tệp. Hãy thử lại.");
      const { object_name, upload_url } = await urlResponse.json();
      await putToSignedUrl(upload_url, file, setProgress);
      const complete = await fetch(`${apiBase}/api/v1/large-imports/complete`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({ object_name }),
      });
      if (!complete.ok)
        throw new Error("Tải dữ liệu xong nhưng chưa khởi chạy xử lý");
      setMessage(
        "Đã tải và lưu tệp thành công.",
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Tải dữ liệu thất bại");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Tải dữ liệu tệp lớn">
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
        {busy ? "Đang tải dữ liệu…" : "Tải tệp lên"}
      </button>
      {progress && (
        <progress
          value={progress.percent}
          max="100"
          aria-label={`Đã tải dữ liệu ${progress.percent}%`}
        />
      )}
      {progress && <span>{progress.percent}%</span>}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
