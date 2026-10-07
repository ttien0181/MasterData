"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, commitUploadSession, createUploadSession, deleteUploadSession,
  getUploadSession, getUploadSessions, renameUploadSession, replaceUploadSessionFile,
  uploadResolutionUploadPart, validateUploadSession } from "@/lib/api";
import type { UploadSession, UploadSessionFile } from "@/lib/contracts";
import { ShellIcon } from "./ShellIcons";

const CHUNK_BYTES = 8 * 1024 * 1024;

function formatBytes(bytes: number) {
  if (!bytes) return "0 byte";
  const units = ["byte", "KB", "MB", "GB", "TB", "PB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** index;
  return `${value.toLocaleString("vi-VN", { maximumFractionDigits: index < 2 ? 0 : 1 })} ${units[index]}`;
}

async function sha256(blob: Blob) {
  const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export default function ResolutionIntake({ onRunCreated, userId, onPendingChange, onDismiss }: {
  onRunCreated: (created: Awaited<ReturnType<typeof commitUploadSession>>) => Promise<void>;
  userId?: string;
  onPendingChange?: (label: string) => void;
  onDismiss?: () => void;
}) {
  const [drafts, setDrafts] = useState<UploadSession[]>([]);
  const [session, setSession] = useState<UploadSession | null>(null);
  const [name, setName] = useState("");
  const [dragging, setDragging] = useState(false);
  const [working, setBusy] = useState(false);
  const [restoring, setRestoring] = useState(!!userId);
  const [restoreFailed, setRestoreFailed] = useState(false);
  const [restoreAttempt, setRestoreAttempt] = useState(0);
  const busy = working || restoring;
  const uploadInFlight = useRef(false);
  const [activity, setActivity] = useState<"uploading" | "validating" | "starting" | null>(null);
  const [progress, setProgress] = useState<{ fileId: string; current: number } | null>(null);
  const [message, setMessage] = useState("");
  const storageKey = userId ? `masterone.upload-session.v1.${userId}` : null;
  const rememberUpload = useCallback((id: string) => {
    if (!storageKey) return;
    try { localStorage.setItem(storageKey, id); } catch { /* Server-side drafts remain recoverable. */ }
  }, [storageKey]);
  const forgetUpload = useCallback((id: string) => {
    if (!storageKey) return;
    try { if (localStorage.getItem(storageKey) === id) localStorage.removeItem(storageKey); } catch { /* Optional hint. */ }
  }, [storageKey]);

  const refreshDrafts = useCallback(async () => {
    try { setDrafts((await getUploadSessions()).items); }
    catch { /* Drafts can still be worked with when the list call is temporarily unavailable. */ }
  }, []);

  useEffect(() => {
    if (!storageKey) { void refreshDrafts(); return; }
    let cancelled = false;
    setRestoring(true); setRestoreFailed(false);
    async function restore() {
      let saved: string | null = null;
      try { saved = localStorage.getItem(storageKey!); } catch { /* Use the server list instead. */ }
      try {
        const result = await getUploadSessions();
        if (cancelled) return;
        setDrafts(result.items);
        const id = saved || result.items[0]?.session_id;
        if (id) {
          const restored = await getUploadSession(id);
          if (cancelled) return;
          setSession(restored); setName(restored.name || ""); rememberUpload(id);
          setMessage(restored.status === "COMMITTED" ? "Phiên đã được tạo. Bấm tiếp tục để mở hoặc bắt đầu xử lý."
            : restored.status === "READY" ? "Đã khôi phục dữ liệu. Bạn có thể bắt đầu phân giải."
            : "Đã khôi phục tiến độ. Chọn lại file để tiếp tục phần chưa tải xong.");
        }
      } catch (error) {
        if (cancelled) return;
        if (error instanceof ApiError && [403, 404].includes(error.status)) {
          if (saved) forgetUpload(saved);
        } else {
          setRestoreFailed(true);
          setMessage("Chưa khôi phục được dữ liệu đang chuẩn bị. Hãy thử lại.");
        }
      } finally { if (!cancelled) setRestoring(false); }
    }
    void restore();
    return () => { cancelled = true; };
  }, [storageKey, restoreAttempt, refreshDrafts, rememberUpload, forgetUpload]);

  useEffect(() => {
    onPendingChange?.(activity === "starting" ? "Đang tạo phiên phân giải" : activity === "uploading" ? "Đang tải dữ liệu"
      : activity === "validating" ? "Đang kiểm tra dữ liệu" : session ? "Có dữ liệu đang chuẩn bị" : "");
  }, [activity, session, onPendingChange]);

  async function transferFile(active: UploadSession, target: UploadSessionFile, file: File) {
    let currentSession = active;
    let currentTarget = target.replacement ?? target;
    const fileMatches = currentTarget.filename === file.name && currentTarget.expected_size === file.size;
    let parts = currentTarget.parts ?? [];
    if (!fileMatches) {
      currentSession = await replaceUploadSessionFile(active.session_id, target.file_id, file);
      const original = currentSession.files.find((item) => item.file_id === target.file_id)!;
      currentTarget = original.replacement!;
      parts = currentTarget.parts;
      setSession(currentSession);
    }

    const uploadedByNumber = new Map(parts.map((part) => [part.part_number, part.sha256]));
    let mustReset = false;
    for (const partNumber of uploadedByNumber.keys()) {
      const start = partNumber * CHUNK_BYTES;
      const actual = await sha256(file.slice(start, Math.min(start + CHUNK_BYTES, file.size)));
      if (actual !== uploadedByNumber.get(partNumber)) { mustReset = true; break; }
    }
    if (mustReset) {
      currentSession = await replaceUploadSessionFile(currentSession.session_id, target.file_id, file);
      const original = currentSession.files.find((item) => item.file_id === target.file_id)!;
      currentTarget = original.replacement!;
      uploadedByNumber.clear();
      setSession(currentSession);
    }

    let completed = currentTarget.received_size;
    for (let start = 0, partNumber = 0; start < file.size; start += CHUNK_BYTES, partNumber += 1) {
      const blob = file.slice(start, Math.min(start + CHUNK_BYTES, file.size));
      const existingHash = uploadedByNumber.get(partNumber);
      if (existingHash) {
        const localHash = await sha256(blob);
        if (localHash === existingHash) continue;
      }
      const digest = await sha256(blob);
      setProgress({ fileId: target.file_id, current: completed });
      await uploadResolutionUploadPart(currentSession.session_id, currentTarget.file_id, partNumber, blob, digest,
        (loaded) => setProgress({ fileId: target.file_id, current: completed + Math.min(loaded, blob.size) }));
      completed += blob.size;
      setProgress({ fileId: target.file_id, current: completed });
    }
    currentSession = await getUploadSession(currentSession.session_id);
    setSession(currentSession);
    setDrafts((items) => [currentSession, ...items.filter((item) => item.session_id !== currentSession.session_id)]);
    return currentSession;
  }

  async function validateIfComplete(updated: UploadSession) {
    if (updated.files.every((file) => {
      const candidate = file.replacement ?? file;
      return candidate.received_size === candidate.expected_size;
    })) {
      setActivity("validating");
      const checked = await validateUploadSession(updated.session_id);
      setSession(checked);
      setDrafts((items) => [checked, ...items.filter((item) => item.session_id !== checked.session_id)]);
      const hasFileError = checked.files.some((file) => (file.replacement ?? file).validation_error);
      setMessage(checked.status !== "READY" && !checked.error_message && !hasFileError ? "Có lỗi khi kiểm tra file." : "");
      return checked;
    }
    return updated;
  }

  async function uploadFiles(incoming: File[]) {
    if (!incoming.length || busy || uploadInFlight.current || session) return;
    if (incoming.some((file) => !file.name.toLowerCase().endsWith(".csv"))) {
      setMessage("Chỉ hỗ trợ file CSV. Hãy chọn lại các file cần phân giải."); return;
    }
    const files = incoming.filter((file, index) => incoming.findIndex((candidate) =>
      candidate.name === file.name && candidate.size === file.size && candidate.lastModified === file.lastModified) === index);
    if (files.length > 10) { setMessage("Mỗi phiên hiện hỗ trợ tối đa 10 file CSV."); return; }
    uploadInFlight.current = true;
    let draftId: string | null = null;
    setBusy(true); setActivity("uploading"); setMessage("");
    try {
      let created = await createUploadSession(name, files);
      draftId = created.session_id;
      rememberUpload(draftId);
      setSession(created);
      setDrafts((items) => [created, ...items.filter((item) => item.session_id !== created.session_id)]);
      for (let index = 0; index < created.files.length; index += 1) {
        created = await transferFile(created, created.files[index], files[index]);
      }
      await validateIfComplete(created);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Không thể tải dữ liệu lên. Hãy chọn lại file để thử lại.");
      if (draftId) {
        try {
          const latest = await getUploadSession(draftId);
          setSession(latest);
          setDrafts((items) => [latest, ...items.filter((item) => item.session_id !== latest.session_id)]);
        } catch { /* The next draft refresh can recover the server-side progress. */ }
      }
      await refreshDrafts();
    } finally { uploadInFlight.current = false; setBusy(false); setActivity(null); setProgress(null); }
  }

  async function openDraft(sessionId: string) {
    setBusy(true); setMessage("");
    try {
      const opened = await getUploadSession(sessionId);
      rememberUpload(sessionId);
      setSession(opened); setName(opened.name || "");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Không mở được dữ liệu đang chuẩn bị."); }
    finally { setBusy(false); }
  }

  async function chooseFileForDraft(target: UploadSessionFile, file?: File) {
    if (!session || !file || busy) return;
    if (!file.name.toLowerCase().endsWith(".csv")) { setMessage("Chỉ hỗ trợ file CSV."); return; }
    setBusy(true); setActivity("uploading"); setMessage("");
    try {
      const updated = await transferFile(session, target, file);
      await validateIfComplete(updated);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Không tải được file."); }
    finally { setBusy(false); setActivity(null); setProgress(null); }
  }

  async function saveName() {
    if (!session || busy) return;
    setBusy(true);
    try {
      const updated = await renameUploadSession(session.session_id, name);
      setSession(updated); setMessage("Đã lưu tên phiên.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Không lưu được tên phiên."); }
    finally { setBusy(false); }
  }

  async function removeDraft() {
    if (!session || busy) return;
    setBusy(true);
    try {
      await deleteUploadSession(session.session_id);
      forgetUpload(session.session_id);
      setDrafts((items) => items.filter((item) => item.session_id !== session.session_id));
      setSession(null); setName(""); setMessage("Đã xóa dữ liệu đang chuẩn bị.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Không xóa được dữ liệu đang chuẩn bị."); }
    finally { setBusy(false); }
  }

  async function commitDraft() {
    if (!session || !["READY", "COMMITTED"].includes(session.status) || busy) return;
    setBusy(true); setActivity("starting"); setMessage("");
    try {
      if (session.status !== "COMMITTED" && name.trim() !== (session.name || "")) await renameUploadSession(session.session_id, name.trim());
      const created = await commitUploadSession(session.session_id);
      await onRunCreated(created);
      forgetUpload(session.session_id);
      setDrafts((items) => items.filter((item) => item.session_id !== session.session_id));
      setSession(null); setName(""); setMessage(`Đã tạo phiên với ${created.profile.row_count.toLocaleString("vi-VN")} bản ghi.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Không thể tạo phiên phân giải.");
      try { setSession(await getUploadSession(session.session_id)); } catch { /* Keep the last known input and retry hint. */ }
    }
    finally { setBusy(false); setActivity(null); }
  }

  const complete = !!session && session.files.every((file) => {
    const candidate = file.replacement ?? file;
    return candidate.received_size === candidate.expected_size;
  });
  const ready = session?.status === "READY" || session?.status === "COMMITTED";
  const actionLabel = restoring ? "Đang khôi phục dữ liệu…" : activity === "starting" ? "Đang bắt đầu phân giải…"
    : activity === "validating" ? "Đang kiểm tra dữ liệu…"
    : activity === "uploading" ? "Chờ tải lên hoàn tất"
    : !session ? "Chọn file để bắt đầu" : session.status === "COMMITTED" ? "Tiếp tục phiên phân giải" : "Bắt đầu phân giải";

  return <section className="resolution-intake upload-session-intake" aria-label="Dữ liệu đang chuẩn bị">
    <div className="upload-intake-body">
      <label className="upload-session-name"><span>Tên phiên <small>Không bắt buộc</small></span>
        <div><input disabled={busy || session?.status === "COMMITTED"} maxLength={255} value={name} onChange={(event) => setName(event.target.value)} placeholder="Mặc định dùng tên file" />
          {session && name.trim() !== (session.name || "") && <button className="upload-text-action" type="button" disabled={busy} onClick={() => void saveName()}>Lưu tên</button>}
        </div>
      </label>
      <div className="upload-source"><span className="upload-field-label">Nguồn dữ liệu</span><span className="upload-source-chip"><ShellIcon name="file" size={15} /> File CSV</span></div>
      <div className="upload-files-heading"><span className="upload-field-label">File dữ liệu</span>{session && <small>{session.files.length} file · {formatBytes(session.total_bytes)}</small>}</div>
      {!session && <>
        <label className={`resolution-dropzone ${dragging ? "dragging" : ""}`} aria-disabled={busy}
          onDragOver={(event) => { event.preventDefault(); if (!busy) setDragging(true); }}
          onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }}
          onDrop={(event) => { event.preventDefault(); setDragging(false); void uploadFiles(Array.from(event.dataTransfer.files)); }}>
          <span className="drop-icon"><ShellIcon name="import" size={25} /></span>
          <strong>{busy ? "Đang chuẩn bị tải lên…" : "Kéo thả file vào đây"}</strong>
          <span className="upload-drop-hint">hoặc <b>chọn file</b></span><span className="upload-format">CSV</span>
          <input aria-label="Chọn các file CSV" type="file" accept=".csv,text/csv" multiple disabled={busy} onChange={(event) => { const files = Array.from(event.target.files ?? []); event.target.value = ""; void uploadFiles(files); }} />
        </label>
        <p className="upload-help">Chọn cùng lúc tối đa 10 file. Tự tải lên và kiểm tra.<br />Không giới hạn dung lượng từng file.</p>
        <details className="upload-schema-help"><summary>Định dạng CSV cần có</summary><p>Mỗi dòng là một bản ghi. Cột source xác định nguồn CRM, ERP hoặc BILL.</p><div className="intake-schema" aria-label="Các cột CSV">{["record_id", "source", "name", "phone", "email", "address"].map((column) => <code key={column}>{column}</code>)}</div></details>
        {drafts.length > 0 && <details className="upload-drafts"><summary>Dữ liệu đang chuẩn bị <span>{drafts.length}</span></summary>
          {drafts.map((draft) => <button key={draft.session_id} type="button" disabled={busy} onClick={() => void openDraft(draft.session_id)}>
            <span>{draft.name || draft.files.map((file) => file.filename).join(", ")}</span><small>{draft.status === "READY" ? "Tiếp tục →" : "Tiếp tục tải →"}</small>
          </button>)}
        </details>}
      </>}
      {session && <>
        <div className="upload-session-files">{session.files.map((file) => {
          const candidate = file.replacement ?? file;
          const current = progress?.fileId === file.file_id ? progress.current : candidate.received_size;
          const pct = candidate.expected_size ? Math.min(100, current / candidate.expected_size * 100) : 0;
          const error = candidate.validation_error || (!file.replacement ? file.validation_error : null);
          const valid = candidate.status === "READY" && !file.replacement && activity !== "uploading";
          const fileBusy = activity === "uploading" && progress?.fileId === file.file_id;
          return <article className={`upload-session-file${error ? " is-error" : valid ? " is-ready" : ""}`} key={file.file_id}>
            <div className="upload-session-file-head">
              <span className="upload-file-icon"><ShellIcon name={error ? "warning" : valid ? "check" : "file"} size={20} /></span>
              <div className="upload-file-info"><strong title={candidate.filename}>{candidate.filename}</strong><small>{error ? "Không thể dùng file này" : valid ? `Tải lên hoàn tất · ${formatBytes(candidate.expected_size)}` : formatBytes(candidate.expected_size)}</small></div>
              <label className="upload-replace-control" aria-disabled={busy || session.status === "COMMITTED"}><ShellIcon name="replace" size={14} />{error ? "Đổi file" : valid ? "Đổi file" : "Tiếp tục"}
                <input aria-label={`Đổi hoặc tiếp tục tải ${candidate.filename}`} type="file" accept=".csv,text/csv" disabled={busy || session.status === "COMMITTED"} onChange={(event) => { const next = event.target.files?.[0]; event.target.value = ""; void chooseFileForDraft(file, next); }} />
              </label>
            </div>
            {!valid && !error && <><div className="upload-file-progress" role="progressbar" aria-label={`Tiến độ ${candidate.filename}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}><span style={{ width: `${pct}%` }} /></div><div className="upload-file-transfer"><b>{Math.round(pct)}%</b><small>{formatBytes(current)} / {formatBytes(candidate.expected_size)}{fileBusy ? " · Đang tải" : ""}</small></div></>}
            {valid && <div className="upload-file-facts">{candidate.row_count !== null && <span>{candidate.row_count.toLocaleString("vi-VN")} bản ghi</span>}<span className="is-valid"><ShellIcon name="check" size={14} /> Dữ liệu hợp lệ</span></div>}
            {error && <p className="upload-file-error" role="alert"><ShellIcon name="warning" size={15} />{error}</p>}
            {file.replacement && <p className="upload-replacement-note">Đang thay thế <b>{file.filename}</b>. File cũ được giữ đến khi file mới hợp lệ.</p>}
          </article>;
        })}</div>
        {session.error_message && !session.files.some((file) => (file.replacement ?? file).validation_error === session.error_message) && <p className="upload-file-error" role="alert">{session.error_message}</p>}
        {activity === "validating" && <p className="upload-info-note" role="status">Đang kiểm tra dữ liệu trong các file CSV…</p>}
        {session.status === "READY" && !busy && <p className="upload-info-note">Chưa bắt đầu phân giải. Bạn có thể đổi file trước khi xử lý.</p>}
        {!ready && complete && !busy && <button type="button" className="upload-recheck" onClick={async () => { setBusy(true); setMessage(""); try { await validateIfComplete(session); } catch (error) { setMessage(error instanceof Error ? error.message : "Không kiểm tra được file."); } finally { setBusy(false); setActivity(null); } }}>Kiểm tra lại dữ liệu</button>}
        <div className="upload-secondary-actions"><button type="button" className="upload-text-action" onClick={() => { if (onDismiss) onDismiss(); else { setSession(null); setMessage(""); } }}>Để sau</button><button type="button" className="upload-text-action danger" disabled={busy || session.status === "COMMITTED"} onClick={() => void removeDraft()}>Xóa dữ liệu đang chuẩn bị</button></div>
      </>}
      {message && <div className="upload-session-message" role="status">{message}</div>}
      {restoreFailed && <button type="button" className="upload-recheck" disabled={busy} onClick={() => setRestoreAttempt((attempt) => attempt + 1)}>Thử khôi phục lại</button>}
    </div>
    <footer className="upload-intake-footer">
      <button type="button" className="button primary" disabled={busy || !ready} onClick={() => void commitDraft()}>{actionLabel}</button>
      <small>{activity === "uploading" ? "Có thể tiếp tục tải nếu mất kết nối" : activity === "validating" ? "Chờ kiểm tra xong để tiếp tục" : session?.status === "ERROR" ? "Sửa lỗi hoặc đổi file để tiếp tục" : "Phiên được tạo khi bạn bấm bắt đầu phân giải"}</small>
    </footer>
  </section>;
}
