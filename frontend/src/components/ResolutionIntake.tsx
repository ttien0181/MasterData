"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import EmbeddingOption from './EmbeddingOption';
import {
  ApiError,
  addUploadSessionFiles,
  commitUploadPart,
  commitUploadSession,
  createUploadSession,
  deleteUploadSession,
  getUploadSession,
  getUploadSessions,
  getSampleCatalog,
  requestResponse,
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
    ? "Kiểm tra xong: dữ liệu hợp lệ. Bạn có thể bắt đầu phân giải."
    : "Kiểm tra xong: có tệp chưa hợp lệ. Xem lỗi ở từng tệp để sửa.";
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
    let storageNetworkFailure = false;
    try {
      const { parts } = await presignUploadParts(sessionId, fileId, [
        { part_number: partNumber, byte_size: blob.size, sha256: digest },
      ]);
      try { await putPartToStorage(parts[0], blob, onProgress); }
      catch (error) {
        storageNetworkFailure = !(error instanceof ApiError) && !(error instanceof DOMException && error.name === 'AbortError');
        throw error;
      }
      await commitUploadPart(sessionId, fileId, partNumber, blob.size, digest);
      return;
    } catch (error) {
      if (storageNetworkFailure && attempt >= PART_ATTEMPTS) {
        // Same owned session/part/checksum; the BE already supports R2 staging through this transport.
        onProgress(0);
        await uploadResolutionUploadPart(sessionId, fileId, partNumber, blob, digest, onProgress);
        return;
      }
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
  workflowVersion = 1,
  initialSessionId,
  onSessionReady,
}: {
  onRunCreated: (created: CreatedRun) => Promise<void>;
  userId?: string;
  onPendingChange?: (label: string) => void;
  onDismiss?: () => void;
  workflowVersion?: 1 | 2;
  initialSessionId?: string;
  onSessionReady?: (sessionId: string) => void;
}) {
  const [drafts, setDrafts] = useState<UploadSession[]>([]);
  const [session, setSession] = useState<UploadSession | null>(null);
  const [name, setName] = useState("");
  const [useEmbedding, setUseEmbedding] = useState(false);
  // Only a committed choice is persisted by the server. New drafts carry the server default.
  useEffect(()=>{
    if (session && ['COMMITTED', 'COMMITTING'].includes(session.status))
      setUseEmbedding(session.use_embedding ?? false);
  },[session?.session_id,session?.status,session?.use_embedding]);
  const [guideRequested, setGuideRequested] = useState(false);
  const [loadingSamples, setLoadingSamples] = useState(false);
  const sampleRequest = useRef<AbortController | null>(null);
  useEffect(() => () => sampleRequest.current?.abort(), []);
  useEffect(() => {
    if (workflowVersion !== 2) return;
    const startGuide = () => { if (!sampleRequest.current) setGuideRequested(true); };
    window.addEventListener('masterone:start-guide', startGuide);
    window.addEventListener('masterone:prepare-guide-upload', startGuide);
    return () => {
      window.removeEventListener('masterone:start-guide', startGuide);
      window.removeEventListener('masterone:prepare-guide-upload', startGuide);
    };
  }, [workflowVersion]);
  const [dragging, setDragging] = useState(false);
  const [working, setBusy] = useState(false);
  const [restoring, setRestoring] = useState(!!userId);
  const [restoreFailed, setRestoreFailed] = useState(false);
  const [restoreAttempt, setRestoreAttempt] = useState(0);
  const busy = working || restoring || loadingSamples;
  // Validation is a server-side state of the draft, so it survives reloads and is never tied to a button's lifetime.
  const validating = session?.status === "VALIDATING";
  const uploadInFlight = useRef(false);
  const [uploadErrors, setUploadErrors] = useState<Record<string, string>>({});
  const [activity, setActivity] = useState<
    "uploading" | "validating" | "committing" | "starting" | null
  >(null);
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
        const id = initialSessionId === 'new' ? null : initialSessionId || saved || result.items[0]?.session_id;
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
              ? "Phiên đã được tạo. Bấm tiếp tục để mở hoặc bắt đầu xử lý."
              : restored.status === "READY"
                ? "Đã khôi phục dữ liệu. Bạn có thể bắt đầu phân giải."
                : restored.status === "COMMITTING"
                  ? "Phiên đang được tạo. Trang sẽ cập nhật khi xong."
                  : "Đã khôi phục tiến độ. Chọn lại tệp để tiếp tục phần chưa tải xong.",
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
  }, [storageKey, initialSessionId, restoreAttempt, refreshDrafts, rememberUpload, forgetUpload]);

  useEffect(() => {
    onPendingChange?.(
      activity === "starting" || activity === "committing"
        ? "Đang tạo phiên phân giải"
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
    signal?: AbortSignal,
  ) {
    signal?.throwIfAborted();
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
        signal?.throwIfAborted();
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
    signal?.throwIfAborted();
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

  async function uploadFiles(incoming: File[], signal?: AbortSignal) {
    if (signal?.aborted) return;
    if (!incoming.length || busy || uploadInFlight.current || session && ['VALIDATING','COMMITTING','COMMITTED','CANCELLED'].includes(session.status)) return;
    if (incoming.some((file) => !file.name.toLowerCase().endsWith(".csv"))) {
      setMessage("Chỉ hỗ trợ tệp CSV. Hãy chọn lại các tệp cần phân giải.");
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
    if (files.length + (session?.files.length || 0) > 10) {
      setMessage("Mỗi phiên hiện hỗ trợ tối đa 10 tệp CSV.");
      return;
    }
    uploadInFlight.current = true;
    let draftId: string | null = null;
    setBusy(true);
    setActivity("uploading");
    setMessage("");
    try {
      const previousIds = new Set(session?.files.map(file => file.file_id) || []);
      let created = session ? await addUploadSessionFiles(session.session_id, files) : await createUploadSession(name, files);
      draftId = created.session_id;
      rememberUpload(draftId);
      setSession(created);
      setDrafts((items) => [
        created,
        ...items.filter((item) => item.session_id !== created.session_id),
      ]);
      // One failed file must not stop the others: upload everything, then say exactly what is left.
      const unfinished: string[] = [];
      const targets = created.files.filter(file => !previousIds.has(file.file_id));
      for (let index = 0; index < targets.length; index += 1) {
        if (signal?.aborted) return;
        const target = targets[index];
        try {
          created = await transferFile(created, target, files[index], signal);
        } catch (error) {
          if (signal?.aborted) return;
          unfinished.push(files[index].name);
          setUploadErrors((items) => ({
            ...items,
            [target.file_id]: error instanceof Error ? error.message : "Không tải được tệp.",
          }));
          created = await getUploadSession(created.session_id).catch(() => created);
          setSession(created);
        }
      }
      if (unfinished.length) {
        setMessage(
          `Chưa tải xong ${unfinished.length} file: ${unfinished.join(", ")}. ` +
            `Bấm "Tiếp tục" ở tệp đó để tải lại phần còn thiếu.`,
        );
        return;
      }
      if (!signal?.aborted) await validateIfComplete(created);
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Không thể tải dữ liệu lên. Hãy chọn lại tệp để thử lại.",
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

  async function loadSamples() {
    if (busy || session || sampleRequest.current) return;
    const controller = new AbortController();
    sampleRequest.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 30000);
    setLoadingSamples(true);
    setMessage('Đang chuẩn bị bộ dữ liệu mẫu…');
    try {
      const catalog = await getSampleCatalog(controller.signal);
      if (!catalog.files.length || catalog.files.length > 10) throw new Error('Bộ dữ liệu mẫu chưa sẵn sàng.');
      const files = await Promise.all(catalog.files.map(async file => {
        const response = await requestResponse(`/demo/files/${encodeURIComponent(file.name)}`, {signal: controller.signal});
        return new File([await response.blob()], file.name, {type: 'text/csv'});
      }));
      window.clearTimeout(timeout);
      if (!controller.signal.aborted) await uploadFiles(files, controller.signal);
    } catch (error) {
      setMessage(controller.signal.aborted ? 'Chưa tải được dữ liệu mẫu. Bạn có thể thử lại.' : error instanceof Error ? error.message : 'Chưa tải được dữ liệu mẫu. Bạn có thể thử lại.');
    } finally {
      window.clearTimeout(timeout);
      sampleRequest.current = null;
      setLoadingSamples(false);
    }
  }

  useEffect(() => {
    if (!guideRequested || busy) return;
    setGuideRequested(false);
    if (session) {
      setMessage('Đang giữ dữ liệu của bạn. Để thực hành với bộ dữ liệu mẫu, hãy mở trang tải tệp mới rồi chạy hướng dẫn.');
      return;
    }
    void loadSamples();
  });

  async function openDraft(sessionId: string) {
    setBusy(true);
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
    }
  }

  async function chooseFileForDraft(target: UploadSessionFile, file?: File) {
    if (!session || !file || busy) return;
    if (!file.name.toLowerCase().endsWith(".csv")) {
      setMessage("Chỉ hỗ trợ tệp CSV.");
      return;
    }
    setBusy(true);
    setActivity("uploading");
    setMessage("");
    try {
      const updated = await transferFile(session, target, file);
      await validateIfComplete(updated);
    } catch (error) {
      const detail = error instanceof Error ? error.message : "Không tải được tệp.";
      setUploadErrors((items) => ({ ...items, [target.file_id]: detail }));
      setMessage(`Chưa tải xong tệp ${target.filename}. Bấm "Tiếp tục" để thử lại.`);
    } finally {
      setBusy(false);
      setActivity(null);
      setProgress(null);
    }
  }

  async function saveName() {
    if (!session || busy) return;
    setBusy(true);
    try {
      const updated = await renameUploadSession(session.session_id, name);
      setSession(updated);
      setMessage("Đã lưu tên phiên.");
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Không lưu được tên phiên.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function removeDraft() {
    if (!session || busy) return;
    setBusy(true);
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
        throw new Error(latest.error_message || "Không thể tạo phiên. Hãy thử lại.");
      }
    }
    throw new Error("Phiên vẫn đang được tạo. Hãy tải lại trang sau ít phút.");
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
      const accepted = workflowVersion === 2 ? await commitUploadSession(session.session_id, 2, useEmbedding) : await commitUploadSession(session.session_id);
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
        `Đã tạo phiên với ${created.profile.row_count.toLocaleString("vi-VN")} hồ sơ.`,
      );
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Không thể tạo phiên phân giải.",
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

  useEffect(() => {
    if (session?.session_id && !busy && !restoring) onSessionReady?.(session.session_id);
  }, [session?.session_id, busy, restoring, onSessionReady]);

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
      ? "Đang bắt đầu phân giải…"
      : validating
        ? "Đang kiểm tra dữ liệu…"
        : activity === "uploading"
          ? "Chờ tải lên hoàn tất"
          : !session
            ? "Chọn tệp để bắt đầu"
            : session.status === "COMMITTED"
              ? "Tiếp tục phiên phân giải"
              : workflowVersion === 2 ? "Tạo phiên từ dữ liệu đã kiểm tra" : "Bắt đầu phân giải";

  return (
    <section
      className="resolution-intake upload-session-intake"
      data-demo="upload-files"
      aria-label="Dữ liệu đang chuẩn bị"
    >
      <div className="upload-intake-body">
        <label className="upload-session-name">
          <span>
            Tên phiên <small>Không bắt buộc</small>
          </span>
          <div>
            <input
              disabled={busy || session?.status === "COMMITTED"}
              maxLength={255}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Mặc định dùng tên tệp"
            />
            {session && name.trim() !== (session.name || "") && (
              <button
                className="upload-text-action"
                type="button"
                disabled={busy}
                onClick={() => void saveName()}
              >
                Lưu tên
              </button>
            )}
          </div>
        </label>
        <div className="upload-source">
          <span className="upload-field-label">Nguồn dữ liệu</span>
          <span className="upload-source-chip">
            <ShellIcon name="file" size={15} /> Tệp CSV
          </span>
        </div>
        <div className="upload-files-heading">
          <span className="upload-field-label">Tệp dữ liệu</span>
          {session && (
            <small>
              {session.files.length} tệp · {formatBytes(session.total_bytes)}
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
                {busy ? "Đang chuẩn bị tải lên…" : "Kéo thả tệp vào đây"}
              </strong>
              <span className="upload-drop-hint">
                hoặc <b>chọn tệp</b>
              </span>
              <span className="upload-format">CSV</span>
              <input
                aria-label="Chọn các tệp CSV"
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
              Chọn cùng lúc tối đa 10 tệp. Tự tải lên và kiểm tra.
              <br />
              Không giới hạn dung lượng từng tệp.
            </p>
            {workflowVersion === 2 && <button type="button" className="secondary" disabled={busy} onClick={() => void loadSamples()}>
              {loadingSamples ? 'Đang chuẩn bị dữ liệu mẫu…' : 'Dùng bộ dữ liệu mẫu'}
            </button>}
            <details className="upload-schema-help">
              <summary>Định dạng CSV cần có</summary>
              <p>
                Mỗi dòng là một hồ sơ. Cột source xác định nguồn CRM, ERP hoặc
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
                    onClick={() => void openDraft(draft.session_id)}
                  >
                    <span>
                      {draft.name ||
                        draft.files.map((file) => file.filename).join(", ")}
                    </span>
                    <small>
                      {draft.status === "READY"
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
            {!['COMMITTED','CANCELLED'].includes(session.status) && <label className="upload-add-control">Thêm tệp vào dữ liệu đang chuẩn bị
              <input type="file" accept=".csv,text/csv" multiple aria-label="Thêm các tệp CSV" disabled={busy || ['VALIDATING','COMMITTING'].includes(session.status) || session.files.length >= 10}
                onChange={event => { const files = Array.from(event.target.files || []); event.target.value = ''; void uploadFiles(files); }} />
            </label>}
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
                  activity === "uploading" && progress?.fileId === file.file_id;
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
                            ? "Không thể dùng tệp này"
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
                      >
                        <ShellIcon name="replace" size={14} />
                        {error ? "Đổi tệp" : valid ? "Đổi tệp" : "Tiếp tục"}
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
                            {candidate.row_count.toLocaleString("vi-VN")} bản
                            ghi
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
                        Đang thay thế <b>{file.filename}</b>. Tệp cũ được giữ
                        đến khi tệp mới hợp lệ.
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
                Đang kiểm tra dữ liệu trong các tệp CSV. Tệp lớn có thể mất vài phút; trang sẽ tự cập nhật.
              </p>
            )}
            {session.status === "READY" && !busy && (
              <p className="upload-info-note">
                Chưa bắt đầu phân giải. Bạn có thể đổi tệp trước khi xử lý.
              </p>
            )}
            {!ready && complete && !busy && (
              <button
                type="button"
                className="upload-recheck"
                onClick={async () => {
                  setBusy(true);
                  setMessage("");
                  try {
                    await validateIfComplete(session);
                  } catch (error) {
                    setMessage(
                      error instanceof Error
                        ? error.message
                        : "Không kiểm tra được tệp.",
                    );
                  } finally {
                    setBusy(false);
                    setActivity(null);
                  }
                }}
              >
                Kiểm tra lại dữ liệu
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
                onClick={() => void removeDraft()}
              >
                Xóa dữ liệu đang chuẩn bị
              </button>
            </div>
          </>
        )}
        {message && (
          <div className="upload-session-message" role="status">
            {message}
          </div>
        )}
        {restoreFailed && (
          <button
            type="button"
            className="upload-recheck"
            disabled={busy}
            onClick={() => setRestoreAttempt((attempt) => attempt + 1)}
          >
            Thử khôi phục lại
          </button>
        )}
      </div>
      <footer className="upload-intake-footer">
        {workflowVersion===2&&<EmbeddingOption checked={useEmbedding} onChange={setUseEmbedding} disabled={busy||session?.status==='COMMITTED'||session?.status==='COMMITTING'}/>}
        <button
          type="button"
          className="button primary"
          data-demo="upload-confirm"
          disabled={busy || !ready}
          onClick={() => void commitDraft()}
        >
          {actionLabel}
        </button>
      </footer>
    </section>
  );
}
