"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  commitUploadPart,
  commitUploadSession,
  createUploadSession,
  deleteUploadSession,
  getUploadSession,
  getUploadSessions,
  presignUploadParts,
  putPartToStorage,
  renameUploadSession,
  replaceUploadSessionFile,
  uploadResolutionUploadPart,
  validateUploadSession,
} from "@/lib/api";
import type { RunCommitProfile, UploadSession, UploadSessionFile } from "@/lib/contracts";
import { ShellIcon } from "./ShellIcons";

type CreatedRun = { run_id: string; status: string; profile: RunCommitProfile };

const CHUNK_BYTES = 8 * 1024 * 1024;
// Direct R2 uploads run in parallel; the proxy path stays sequential because its bytes share one API connection.
const DIRECT_CONCURRENCY = 3;
const PART_ATTEMPTS = 3;
const VALIDATION_POLL_MS = 1500;
const MAX_VALIDATION_POLLS = 120;

function formatBytes(bytes: number) {
  if (!bytes) return "0 byte";
  const units = ["byte", "KB", "MB", "GB", "TB", "PB"];
  const index = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  const value = bytes / 1024 ** index;
  return `${value.toLocaleString("vi-VN", { maximumFractionDigits: index < 2 ? 0 : 1 })} ${units[index]}`;
}

async function sha256(blob: Blob) {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    await blob.arrayBuffer(),
  );
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

function withoutKey(items: Record<string, string>, key: string) {
  const next = { ...items };
  delete next[key];
  return next;
}

function validationOutcome(status: UploadSession["status"]) {
  return status === "READY"
    ? "Kiểm tra xong: dữ liệu hợp lệ. Bạn có thể bắt đầu xử lý."
    : "Kiểm tra xong: có file chưa hợp lệ. Xem lỗi ở từng file để sửa.";
}

function isRetryable(error: unknown) {
  // A rejected checksum or size would fail the same way again. Expired URLs (403), throttling,
  // 5xx and dropped connections are worth another attempt with a fresh URL.
  if (error instanceof ApiError)
    return error.status === 403 || error.status === 429 || error.status >= 500;
  return true;
}

async function sendPart(
  sessionId: string,
  fileId: string,
  mode: "direct" | "proxy",
  partNumber: number,
  blob: Blob,
  onProgress: (loaded: number) => void,
) {
  const digest = await sha256(blob);
  if (mode === "proxy") {
    await uploadResolutionUploadPart(sessionId, fileId, partNumber, blob, digest, onProgress);
    return;
  }
  for (let attempt = 1; ; attempt += 1) {
    try {
      const { parts } = await presignUploadParts(sessionId, fileId, [
        { part_number: partNumber, byte_size: blob.size, sha256: digest },
      ]);
      await putPartToStorage(parts[0], blob, onProgress);
      await commitUploadPart(sessionId, fileId, partNumber, blob.size, digest);
      return;
    } catch (error) {
      if (attempt >= PART_ATTEMPTS || !isRetryable(error)) throw error;
      onProgress(0);
      await new Promise((resolve) => setTimeout(resolve, 400 * attempt));
    }
  }
}

export default function ResolutionIntake({
  onRunCreated,
  userId,
  onPendingChange,
  onDismiss,
}: {
  onRunCreated: (created: CreatedRun) => Promise<void>;
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
  // Validation is a server-side state of the draft, so it survives reloads and is never tied to a button's lifetime.
  const validating = session?.status === "VALIDATING";
  const uploadInFlight = useRef(false);
  const [uploadErrors, setUploadErrors] = useState<Record<string, string>>({});
  const [activity, setActivity] = useState<
    "uploading" | "validating" | "committing" | "starting" | null
  >(null);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [pendingFileId, setPendingFileId] = useState<string | null>(null);
  const [progress, setProgress] = useState<{
    fileId: string;
    current: number;
  } | null>(null);
  const [message, setMessage] = useState("");
  const storageKey = userId ? `masterone.upload-session.v1.${userId}` : null;
  const rememberUpload = useCallback(
    (id: string) => {
      if (!storageKey) return;
      try {
        localStorage.setItem(storageKey, id);
      } catch {
        /* Server-side drafts remain recoverable. */
      }
    },
    [storageKey],
  );
  const forgetUpload = useCallback(
    (id: string) => {
      if (!storageKey) return;
      try {
        if (localStorage.getItem(storageKey) === id)
          localStorage.removeItem(storageKey);
      } catch {
        /* Optional hint. */
      }
    },
    [storageKey],
  );

  const refreshDrafts = useCallback(async () => {
    try {
      setDrafts((await getUploadSessions()).items);
    } catch {
      /* Drafts can still be worked with when the list call is temporarily unavailable. */
    }
  }, []);

  useEffect(() => {
    if (!storageKey) {
      void refreshDrafts();
      return;
    }
    let cancelled = false;
    setRestoring(true);
    setRestoreFailed(false);
    async function restore() {
      let saved: string | null = null;
      try {
        saved = localStorage.getItem(storageKey!);
      } catch {
        /* Use the server list instead. */
      }
      try {
        const result = await getUploadSessions();
        if (cancelled) return;
        setDrafts(result.items);
        const id = saved || result.items[0]?.session_id;
        if (id) {
          const restored = await getUploadSession(id);
          if (cancelled) return;
          setSession(restored);
          setName(restored.name || "");
          if (restored.status === "COMMITTING") {
            setActivity("committing");
          }
          rememberUpload(id);
          setMessage(
            restored.status === "COMMITTED"
              ? "Đợt xử lý đã được tạo. Bấm tiếp tục để mở hoặc bắt đầu xử lý."
              : restored.status === "READY"
                ? "Đã khôi phục dữ liệu. Bạn có thể bắt đầu xử lý."
                : restored.status === "COMMITTING"
                  ? "Đợt xử lý đang được tạo. Trang sẽ cập nhật khi xong."
                  : "Đã khôi phục tiến độ. Chọn lại file để tiếp tục phần chưa tải xong.",
          );
        }
      } catch (error) {
        if (cancelled) return;
        if (error instanceof ApiError && [403, 404].includes(error.status)) {
          if (saved) forgetUpload(saved);
        } else {
          setRestoreFailed(true);
          setMessage("Chưa khôi phục được dữ liệu đang chuẩn bị. Hãy thử lại.");
        }
      } finally {
        if (!cancelled) setRestoring(false);
      }
    }
    void restore();
    return () => {
      cancelled = true;
    };
  }, [storageKey, restoreAttempt, refreshDrafts, rememberUpload, forgetUpload]);

  useEffect(() => {
    onPendingChange?.(
      activity === "starting" || activity === "committing"
        ? "Đang tạo đợt xử lý"
        : activity === "uploading"
          ? "Đang tải dữ liệu"
          : validating
            ? "Đang kiểm tra dữ liệu"
            : session
              ? "Có dữ liệu đang chuẩn bị"
              : "",
    );
  }, [activity, validating, session, onPendingChange]);

  async function transferFile(
    active: UploadSession,
    target: UploadSessionFile,
    file: File,
  ) {
    setUploadErrors((items) => withoutKey(items, target.file_id));
    let currentSession = active;
    let currentTarget = target.replacement ?? target;
    const fileMatches =
      currentTarget.filename === file.name &&
      currentTarget.expected_size === file.size;
    let parts = currentTarget.parts ?? [];
    if (!fileMatches) {
      currentSession = await replaceUploadSessionFile(
        active.session_id,
        target.file_id,
        file,
      );
      const original = currentSession.files.find(
        (item) => item.file_id === target.file_id,
      )!;
      currentTarget = original.replacement!;
      parts = currentTarget.parts;
      setSession(currentSession);
    }

    const uploadedByNumber = new Map(
      parts.map((part) => [part.part_number, part.sha256]),
    );
    let mustReset = false;
    for (const partNumber of uploadedByNumber.keys()) {
      const start = partNumber * CHUNK_BYTES;
      const actual = await sha256(
        file.slice(start, Math.min(start + CHUNK_BYTES, file.size)),
      );
      if (actual !== uploadedByNumber.get(partNumber)) {
        mustReset = true;
        break;
      }
    }
    if (mustReset) {
      currentSession = await replaceUploadSessionFile(
        currentSession.session_id,
        target.file_id,
        file,
      );
      const original = currentSession.files.find(
        (item) => item.file_id === target.file_id,
      )!;
      currentTarget = original.replacement!;
      uploadedByNumber.clear();
      setSession(currentSession);
    }

    // Every part the server already holds was verified against the local bytes above, so only the rest are sent.
    const partCount = Math.ceil(file.size / CHUNK_BYTES);
    const pending = Array.from({ length: partCount }, (_, index) => index).filter(
      (partNumber) => !uploadedByNumber.has(partNumber),
    );
    const mode = currentSession.upload_mode;
    const sessionId = currentSession.session_id;
    const fileId = currentTarget.file_id;
    let completed = currentTarget.received_size;
    const inFlight = new Map<number, number>();
    const reportProgress = () => {
      const loaded = [...inFlight.values()].reduce((sum, value) => sum + value, 0);
      setProgress({ fileId: target.file_id, current: completed + loaded });
    };
    let next = 0;
    let failure: unknown = null;
    const worker = async () => {
      while (failure === null && next < pending.length) {
        const partNumber = pending[next++];
        const start = partNumber * CHUNK_BYTES;
        const blob = file.slice(start, Math.min(start + CHUNK_BYTES, file.size));
        inFlight.set(partNumber, 0);
        reportProgress();
        try {
          await sendPart(sessionId, fileId, mode, partNumber, blob, (loaded) => {
            inFlight.set(partNumber, Math.min(loaded, blob.size));
            reportProgress();
          });
        } catch (error) {
          if (failure === null) failure = error;
          return;
        }
        inFlight.delete(partNumber);
        completed += blob.size;
        reportProgress();
      }
    };
    const concurrency = mode === "direct" ? DIRECT_CONCURRENCY : 1;
    await Promise.all(
      Array.from({ length: Math.min(concurrency, pending.length) }, worker),
    );
    if (failure !== null) throw failure;
    currentSession = await getUploadSession(currentSession.session_id);
    setSession(currentSession);
    setDrafts((items) => [
      currentSession,
      ...items.filter((item) => item.session_id !== currentSession.session_id),
    ]);
    return currentSession;
  }

  async function validateIfComplete(updated: UploadSession) {
    if (updated.status === "VALIDATING") return updated;
    if (
      updated.files.every((file) => {
        const candidate = file.replacement ?? file;
        return candidate.received_size === candidate.expected_size;
      })
    ) {
      const checked = await validateUploadSession(updated.session_id);
      setSession(checked);
      setDrafts((items) => [
        checked,
        ...items.filter((item) => item.session_id !== checked.session_id),
      ]);
      return checked;
    }
    return updated;
  }

  // Validate and commit run as background jobs. Follow the draft for as long as the job holds it,
  // and report the outcome when it leaves VALIDATING. No attempt cap: large files can take minutes.
  const jobSessionId = session?.session_id;
  const jobStatus = session?.status;
  useEffect(() => {
    if (!jobSessionId || (jobStatus !== "VALIDATING" && jobStatus !== "COMMITTING")) return;
    let stopped = false;
    let timer = 0;
    const schedule = (delay: number) => {
      timer = window.setTimeout(poll, delay);
    };
    const poll = async () => {
      try {
        const latest = await getUploadSession(jobSessionId);
        if (stopped) return;
        setSession(latest);
        setDrafts((items) => [
          latest,
          ...items.filter((item) => item.session_id !== latest.session_id),
        ]);
        if (latest.status === jobStatus) {
          schedule(VALIDATION_POLL_MS);
          return;
        }
        if (jobStatus === "VALIDATING") setMessage(validationOutcome(latest.status));
      } catch {
        // A dropped request should not end the watch; retry more slowly.
        if (!stopped) schedule(VALIDATION_POLL_MS * 2);
      }
    };
    schedule(VALIDATION_POLL_MS);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [jobSessionId, jobStatus]);

  async function uploadFiles(incoming: File[]) {
    if (!incoming.length || busy || uploadInFlight.current || session) return;
    if (incoming.some((file) => !file.name.toLowerCase().endsWith(".csv"))) {
      setMessage("Chỉ hỗ trợ file CSV. Hãy chọn lại các file cần xử lý.");
      return;
    }
    const files = incoming.filter(
      (file, index) =>
        incoming.findIndex(
          (candidate) =>
            candidate.name === file.name &&
            candidate.size === file.size &&
            candidate.lastModified === file.lastModified,
        ) === index,
    );
    if (files.length > 10) {
      setMessage("Mỗi đợt xử lý hiện hỗ trợ tối đa 10 file CSV.");
      return;
    }
    uploadInFlight.current = true;
    let draftId: string | null = null;
    setBusy(true);
    setActivity("uploading");
    setMessage("");
    try {
      let created = await createUploadSession(name, files);
      draftId = created.session_id;
      rememberUpload(draftId);
      setSession(created);
      setDrafts((items) => [
        created,
        ...items.filter((item) => item.session_id !== created.session_id),
      ]);
      // One failed file must not stop the others: upload everything, then say exactly what is left.
      const unfinished: string[] = [];
      for (let index = 0; index < created.files.length; index += 1) {
        const target = created.files[index];
        try {
          created = await transferFile(created, target, files[index]);
        } catch (error) {
          unfinished.push(files[index].name);
          setUploadErrors((items) => ({
            ...items,
            [target.file_id]: error instanceof Error ? error.message : "Không tải được file.",
          }));
          created = await getUploadSession(created.session_id).catch(() => created);
          setSession(created);
        }
      }
      if (unfinished.length) {
        setMessage(
          `Chưa tải xong ${unfinished.length} file: ${unfinished.join(", ")}. ` +
            `Bấm "Tiếp tục" ở file đó để tải lại phần còn thiếu.`,
        );
        return;
      }
      await validateIfComplete(created);
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Không thể tải dữ liệu lên. Hãy chọn lại file để thử lại.",
      );
      if (draftId) {
        try {
          const latest = await getUploadSession(draftId);
          setSession(latest);
          setDrafts((items) => [
            latest,
            ...items.filter((item) => item.session_id !== latest.session_id),
          ]);
        } catch {
          /* The next draft refresh can recover the server-side progress. */
        }
      }
      await refreshDrafts();
    } finally {
      uploadInFlight.current = false;
      setBusy(false);
      setActivity(null);
      setProgress(null);
    }
  }

  async function openDraft(sessionId: string) {
    if (busy) return;
    setBusy(true);
    setPendingAction(`draft:${sessionId}`);
    setMessage("");
    try {
      const opened = await getUploadSession(sessionId);
      rememberUpload(sessionId);
      setSession(opened);
      setName(opened.name || "");
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Không mở được dữ liệu đang chuẩn bị.",
      );
    } finally {
      setBusy(false);
      setPendingAction(null);
    }
  }

  async function chooseFileForDraft(target: UploadSessionFile, file?: File) {
    if (!session || !file || busy) return;
    if (!file.name.toLowerCase().endsWith(".csv")) {
      setMessage("Chỉ hỗ trợ file CSV.");
      return;
    }
    setBusy(true);
    setActivity("uploading");
    setPendingFileId(target.file_id);
    setMessage("");
    try {
      const updated = await transferFile(session, target, file);
      await validateIfComplete(updated);
    } catch (error) {
      const detail = error instanceof Error ? error.message : "Không tải được file.";
      setUploadErrors((items) => ({ ...items, [target.file_id]: detail }));
      setMessage(`Chưa tải xong file ${target.filename}. Bấm "Tiếp tục" để thử lại.`);
    } finally {
      setBusy(false);
      setActivity(null);
      setPendingFileId(null);
      setProgress(null);
    }
  }

  async function saveName() {
    if (!session || busy) return;
    setBusy(true);
    setPendingAction("save-name");
    try {
      const updated = await renameUploadSession(session.session_id, name);
      setSession(updated);
      setMessage("Đã lưu tên đợt xử lý.");
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Không lưu được tên đợt xử lý.",
      );
    } finally {
      setBusy(false);
      setPendingAction(null);
    }
  }

  async function removeDraft() {
    if (!session || busy) return;
    setBusy(true);
    setPendingAction("remove-draft");
    try {
      await deleteUploadSession(session.session_id);
      forgetUpload(session.session_id);
      setDrafts((items) =>
        items.filter((item) => item.session_id !== session.session_id),
      );
      setSession(null);
      setName("");
      setMessage("Đã xóa dữ liệu đang chuẩn bị.");
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Không xóa được dữ liệu đang chuẩn bị.",
      );
    } finally {
      setBusy(false);
      setPendingAction(null);
    }
  }

  // The commit job runs in the background: poll the draft until it records the run or an error.
  async function waitForCommit(sessionId: string): Promise<CreatedRun> {
    for (let attempt = 0; attempt < MAX_VALIDATION_POLLS; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, VALIDATION_POLL_MS));
      const latest = await getUploadSession(sessionId);
      if (latest.status === "COMMITTED" && latest.committed_run_id && latest.committed_profile) {
        setSession(latest);
        return { run_id: latest.committed_run_id, status: "UPLOADED", profile: latest.committed_profile };
      }
      if (latest.status !== "COMMITTING") {
        throw new Error(latest.error_message || "Không thể tạo đợt xử lý. Hãy thử lại.");
      }
    }
    throw new Error("Đợt xử lý vẫn đang được tạo. Hãy tải lại trang sau ít phút.");
  }

  async function commitDraft() {
    if (!session || !["READY", "COMMITTED"].includes(session.status) || busy)
      return;
    setBusy(true);
    setActivity("starting");
    setMessage("");
    try {
      if (
        session.status !== "COMMITTED" &&
        name.trim() !== (session.name || "")
      )
        await renameUploadSession(session.session_id, name.trim());
      const accepted = await commitUploadSession(session.session_id);
      const created: CreatedRun =
        accepted.run_id && accepted.profile
          ? { run_id: accepted.run_id, status: accepted.status, profile: accepted.profile }
          : await waitForCommit(session.session_id);
      await onRunCreated(created);
      forgetUpload(session.session_id);
      setDrafts((items) =>
        items.filter((item) => item.session_id !== session.session_id),
      );
      setSession(null);
      setName("");
      setMessage(
        `Đã tạo đợt xử lý với ${created.profile.row_count.toLocaleString("vi-VN")} dòng thông tin.`,
      );
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Không thể tạo đợt xử lý.",
      );
      try {
        setSession(await getUploadSession(session.session_id));
      } catch {
        /* Keep the last known input and retry hint. */
      }
    } finally {
      setBusy(false);
      setActivity(null);
    }
  }

  const complete =
    !!session &&
    session.files.every((file) => {
      const candidate = file.replacement ?? file;
      return candidate.received_size === candidate.expected_size;
    });
  const ready = session?.status === "READY" || session?.status === "COMMITTED";
  const actionLabel = restoring
    ? "Đang khôi phục dữ liệu…"
    : activity === "starting" || activity === "committing"
      ? "Đang bắt đầu xử lý…"
      : validating
        ? "Đang kiểm tra dữ liệu…"
        : activity === "uploading"
          ? "Chờ tải lên hoàn tất"
          : !session
            ? "Chọn file để bắt đầu"
            : session.status === "COMMITTED"
              ? "Tiếp tục đợt xử lý"
              : "Bắt đầu xử lý";
  const mainWaiting = restoring || validating || activity === "uploading" ||
    activity === "committing" || activity === "starting";

  return (
    <section
      className="resolution-intake upload-session-intake"
      aria-label="Dữ liệu đang chuẩn bị"
    >
      <div className="upload-intake-body">
        <label className="upload-session-name">
          <span>
            Tên đợt xử lý <small>Không bắt buộc</small>
          </span>
          <div>
            <input
              disabled={busy || session?.status === "COMMITTED"}
              maxLength={255}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Mặc định dùng tên file"
            />
            {session && name.trim() !== (session.name || "") && (
              <button
                className="upload-text-action"
                type="button"
                disabled={busy}
                aria-busy={pendingAction === "save-name"}
                onClick={() => void saveName()}
              >
                {pendingAction === "save-name" ? <><span className="button-spinner" aria-hidden="true" />Đang lưu…</> : "Lưu tên"}
              </button>
            )}
          </div>
        </label>
        <div className="upload-source">
          <span className="upload-field-label">Nguồn dữ liệu</span>
          <span className="upload-source-chip">
            <ShellIcon name="file" size={15} /> File CSV
          </span>
        </div>
        <div className="upload-files-heading">
          <span className="upload-field-label">File dữ liệu</span>
          {session && (
            <small>
              {session.files.length} file · {formatBytes(session.total_bytes)}
            </small>
          )}
        </div>
        {!session && (
          <>
            <label
              className={`resolution-dropzone ${dragging ? "dragging" : ""}`}
              aria-disabled={busy}
              onDragOver={(event) => {
                event.preventDefault();
                if (!busy) setDragging(true);
              }}
              onDragLeave={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node))
                  setDragging(false);
              }}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                void uploadFiles(Array.from(event.dataTransfer.files));
              }}
            >
              <span className="drop-icon">
                <ShellIcon name="import" size={25} />
              </span>
              <strong>
                {busy ? "Đang chuẩn bị tải lên…" : "Kéo thả file vào đây"}
              </strong>
              <span className="upload-drop-hint">
                hoặc <b>chọn file</b>
              </span>
              <span className="upload-format">CSV</span>
              <input
                aria-label="Chọn các file CSV"
                type="file"
                accept=".csv,text/csv"
                multiple
                disabled={busy}
                onChange={(event) => {
                  const files = Array.from(event.target.files ?? []);
                  event.target.value = "";
                  void uploadFiles(files);
                }}
              />
            </label>
            <p className="upload-help">
              Chọn cùng lúc tối đa 10 file. Tự tải lên và kiểm tra.
              <br />
              Không giới hạn dung lượng từng file.
            </p>
            <details className="upload-schema-help">
              <summary>Định dạng CSV cần có</summary>
              <p>
                Mỗi dòng CSV chứa thông tin từ một nguồn. Cột source xác định nguồn CRM, ERP hoặc
                BILL.
              </p>
              <div className="intake-schema" aria-label="Các cột CSV">
                {[
                  "record_id",
                  "source",
                  "name",
                  "phone",
                  "email",
                  "address",
                ].map((column) => (
                  <code key={column}>{column}</code>
                ))}
              </div>
            </details>
            {drafts.length > 0 && (
              <details className="upload-drafts">
                <summary>
                  Dữ liệu đang chuẩn bị <span>{drafts.length}</span>
                </summary>
                {drafts.map((draft) => (
                  <button
                    key={draft.session_id}
                    type="button"
                    disabled={busy}
                    aria-busy={pendingAction === `draft:${draft.session_id}`}
                    onClick={() => void openDraft(draft.session_id)}
                  >
                    <span>
                      {draft.name ||
                        draft.files.map((file) => file.filename).join(", ")}
                    </span>
                    <small>
                      {pendingAction === `draft:${draft.session_id}`
                        ? <><span className="button-spinner" aria-hidden="true" />Đang mở…</>
                        : draft.status === "READY"
                        ? "Tiếp tục →"
                        : "Tiếp tục tải →"}
                    </small>
                  </button>
                ))}
              </details>
            )}
          </>
        )}
        {session && (
          <>
            <div className="upload-session-files">
              {session.files.map((file) => {
                const candidate = file.replacement ?? file;
                const current =
                  progress?.fileId === file.file_id
                    ? progress.current
                    : candidate.received_size;
                const pct = candidate.expected_size
                  ? Math.min(100, (current / candidate.expected_size) * 100)
                  : 0;
                const error =
                  candidate.validation_error ||
                  (!file.replacement ? file.validation_error : null);
                const valid =
                  candidate.status === "READY" &&
                  !file.replacement &&
                  activity !== "uploading";
                const fileBusy =
                  activity === "uploading" &&
                  (pendingFileId === file.file_id || progress?.fileId === file.file_id);
                const uploadError = uploadErrors[file.file_id];
                const failed = !!error || !!uploadError;
                return (
                  <article
                    className={`upload-session-file${failed ? " is-error" : valid ? " is-ready" : ""}`}
                    key={file.file_id}
                  >
                    <div className="upload-session-file-head">
                      <span className="upload-file-icon">
                        <ShellIcon
                          name={failed ? "warning" : valid ? "check" : "file"}
                          size={20}
                        />
                      </span>
                      <div className="upload-file-info">
                        <strong title={candidate.filename}>
                          {candidate.filename}
                        </strong>
                        <small>
                          {error
                            ? "Không thể dùng file này"
                            : uploadError
                              ? "Chưa tải xong"
                              : valid
                              ? `Tải lên hoàn tất · ${formatBytes(candidate.expected_size)}`
                              : formatBytes(candidate.expected_size)}
                        </small>
                      </div>
                      <label
                        className="upload-replace-control"
                        aria-disabled={busy || session.status === "COMMITTED"}
                        aria-busy={fileBusy}
                      >
                        {fileBusy ? <><span className="button-spinner" aria-hidden="true" />Đang tải…</> : <><ShellIcon name="replace" size={14} />{error ? "Đổi file" : valid ? "Đổi file" : "Tiếp tục"}</>}
                        <input
                          aria-label={`Đổi hoặc tiếp tục tải ${candidate.filename}`}
                          type="file"
                          accept=".csv,text/csv"
                          disabled={busy || session.status === "COMMITTED"}
                          onChange={(event) => {
                            const next = event.target.files?.[0];
                            event.target.value = "";
                            void chooseFileForDraft(file, next);
                          }}
                        />
                      </label>
                    </div>
                    {!valid && !error && (
                      <>
                        <div
                          className="upload-file-progress"
                          role="progressbar"
                          aria-label={`Tiến độ ${candidate.filename}`}
                          aria-valuemin={0}
                          aria-valuemax={100}
                          aria-valuenow={Math.round(pct)}
                        >
                          <span style={{ width: `${pct}%` }} />
                        </div>
                        <div className="upload-file-transfer">
                          <b>{Math.round(pct)}%</b>
                          <small>
                            {formatBytes(current)} /{" "}
                            {formatBytes(candidate.expected_size)}
                            {fileBusy ? " · Đang tải" : ""}
                          </small>
                        </div>
                      </>
                    )}
                    {valid && (
                      <div className="upload-file-facts">
                        {candidate.row_count !== null && (
                          <span>
                            {candidate.row_count.toLocaleString("vi-VN")} dòng thông tin
                          </span>
                        )}
                        <span className="is-valid">
                          <ShellIcon name="check" size={14} /> Dữ liệu hợp lệ
                        </span>
                      </div>
                    )}
                    {error && (
                      <p className="upload-file-error" role="alert">
                        <ShellIcon name="warning" size={15} />
                        {error}
                      </p>
                    )}
                    {!error && uploadError && (
                      <p className="upload-file-error" role="alert">
                        <ShellIcon name="warning" size={15} />
                        Tải lên gián đoạn: {uploadError}. Bấm &quot;Tiếp tục&quot; để tải lại phần còn thiếu.
                      </p>
                    )}
                    {file.replacement && (
                      <p className="upload-replacement-note">
                        Đang thay thế <b>{file.filename}</b>. File cũ được giữ
                        đến khi file mới hợp lệ.
                      </p>
                    )}
                  </article>
                );
              })}
            </div>
            {session.error_message &&
              !session.files.some(
                (file) =>
                  (file.replacement ?? file).validation_error ===
                  session.error_message,
              ) && (
                <p className="upload-file-error" role="alert">
                  {session.error_message}
                </p>
              )}
            {validating && (
              <p className="upload-info-note" role="status">
                Đang kiểm tra dữ liệu trong các file CSV. File lớn có thể mất vài phút; trang sẽ tự cập nhật.
              </p>
            )}
            {session.status === "READY" && !busy && (
              <p className="upload-info-note">
                Chưa bắt đầu xử lý. Bạn có thể đổi file trước khi xử lý.
              </p>
            )}
            {!ready && complete && (!busy || pendingAction === "recheck") && (
              <button
                type="button"
                className="upload-recheck"
                disabled={busy}
                aria-busy={pendingAction === "recheck"}
                onClick={async () => {
                  setBusy(true);
                  setPendingAction("recheck");
                  setMessage("");
                  try {
                    await validateIfComplete(session);
                  } catch (error) {
                    setMessage(
                      error instanceof Error
                        ? error.message
                        : "Không kiểm tra được file.",
                    );
                  } finally {
                    setBusy(false);
                    setActivity(null);
                    setPendingAction(null);
                  }
                }}
              >
                {pendingAction === "recheck" ? <><span className="button-spinner" aria-hidden="true" />Đang kiểm tra…</> : "Kiểm tra lại dữ liệu"}
              </button>
            )}
            <div className="upload-secondary-actions">
              <button
                type="button"
                className="upload-text-action"
                onClick={() => {
                  if (onDismiss) onDismiss();
                  else {
                    setSession(null);
                    setMessage("");
                  }
                }}
              >
                Để sau
              </button>
              <button
                type="button"
                className="upload-text-action danger"
                disabled={busy || session.status === "COMMITTED"}
                aria-busy={pendingAction === "remove-draft"}
                onClick={() => void removeDraft()}
              >
                {pendingAction === "remove-draft" ? <><span className="button-spinner" aria-hidden="true" />Đang xóa…</> : "Xóa dữ liệu đang chuẩn bị"}
              </button>
            </div>
          </>
        )}
        {message && (
          <div className="upload-session-message" role="status">
            {message}
          </div>
        )}
        {(restoreFailed || (restoring && restoreAttempt > 0)) && (
          <button
            type="button"
            className="upload-recheck"
            disabled={busy}
            aria-busy={restoring}
            onClick={() => {
              setRestoring(true);
              setRestoreFailed(false);
              setRestoreAttempt((attempt) => attempt + 1);
            }}
          >
            {restoring ? <><span className="button-spinner" aria-hidden="true" />Đang khôi phục…</> : "Thử khôi phục lại"}
          </button>
        )}
      </div>
      <footer className="upload-intake-footer">
        <button
          type="button"
          className="button primary"
          disabled={busy || !ready}
          aria-busy={mainWaiting}
          onClick={() => void commitDraft()}
        >
          {mainWaiting && <span className="button-spinner" aria-hidden="true" />}
          {actionLabel}
        </button>
        <small>
          {activity === "uploading"
            ? "Có thể tiếp tục tải nếu mất kết nối"
            : validating
              ? "Chờ kiểm tra xong để tiếp tục"
              : activity === "committing"
                ? "Đợt xử lý đang được tạo, có thể đợi hoặc quay lại sau"
              : session?.status === "ERROR"
                ? "Sửa lỗi hoặc đổi file để tiếp tục"
                : "Đợt xử lý được tạo khi bạn bấm bắt đầu xử lý"}
        </small>
      </footer>
    </section>
  );
}
