"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell, { type Stage } from "@/components/AppShell";
import { PageHeader } from "@/components/DashboardPrimitives";
import ResolutionFlow from "@/components/ResolutionFlow";
import { ApiError, getResolutionOverview, getResolutionRuns, setCsrfToken,
  startResolutionRun, uploadResolutionFiles, type AuthUser } from "@/lib/api";
import type { ResolutionOverview, ResolutionRunHistory } from "@/lib/contracts";

function activeRunKey(userId: string) { return `masterone.resolution-run.v1.${userId}`; }

export default function ResolutionWorkspace({ user, onLogout }: { user: AuthUser; onLogout: () => Promise<void> }) {
  const [stage, setStage] = useState<Stage>("overview");
  const [runId, setRunId] = useState("");
  const [overview, setOverview] = useState<ResolutionOverview | null>(null);
  const [runHistory, setRunHistory] = useState<ResolutionRunHistory["items"]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessageState] = useState("");
  const [messageVersion, setMessageVersion] = useState(0);
  const [polling, setPolling] = useState(false);

  const setMessage = useCallback((next: string) => {
    setMessageState(next);
    setMessageVersion((version) => version + 1);
  }, []);

  function addFiles(incoming: File[]) {
    const csvFiles = incoming.filter((file) => file.name.toLowerCase().endsWith(".csv"));
    if (csvFiles.length !== incoming.length) setMessage("Chỉ hỗ trợ file CSV.");
    const next = [...files];
    for (const file of csvFiles) {
      if (!next.some((selected) => selected.name === file.name && selected.size === file.size && selected.lastModified === file.lastModified)) next.push(file);
    }
    if (next.length > 10) { setMessage("Mỗi lần nạp tối đa 10 file CSV."); return; }
    setFiles(next);
  }

  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => setMessageState(""), 6000);
    return () => window.clearTimeout(timer);
  }, [message, messageVersion]);

  const refreshOverview = useCallback(async (id: string) => {
    const result = await getResolutionOverview(id);
    setOverview(result);
    return result;
  }, []);
  const refreshRuns = useCallback(async () => {
    const result = await getResolutionRuns();
    setRunHistory(result.items);
  }, []);

  useEffect(() => {
    setCsrfToken(user.csrf_token);
    void refreshRuns().catch(() => undefined);
    const saved = window.localStorage.getItem(activeRunKey(user.id));
    if (!saved) return;
    setRunId(saved);
    void refreshOverview(saved).catch((error: unknown) => {
      if (error instanceof ApiError && error.status === 404) window.localStorage.removeItem(activeRunKey(user.id));
      else setMessage(error instanceof Error ? error.message : "Không tải được lần chạy gần nhất");
    });
  }, [refreshOverview, refreshRuns, user]);

  useEffect(() => {
    if (!runId || !overview || ["COMPLETED", "FAILED", "RETRYABLE_FAILED", "UPLOADED"].includes(overview.status)) return;
    setPolling(true);
    const timer = window.setInterval(() => { void refreshOverview(runId).then((result) => {
      if (["COMPLETED", "FAILED", "RETRYABLE_FAILED"].includes(result.status)) setPolling(false);
    }).catch(() => setPolling(false)); }, 900);
    return () => window.clearInterval(timer);
  }, [overview?.status, refreshOverview, runId]);

  async function handleUpload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!files.length) return;
    setBusy(true); setMessage("");
    try {
      const created = await uploadResolutionFiles(files);
      window.localStorage.setItem(activeRunKey(user.id), created.run_id);
      setRunId(created.run_id);
      await startResolutionRun(created.run_id);
      await refreshOverview(created.run_id);
      await refreshRuns();
      setStage("overview"); setFiles([]);
      setMessage(`Đã nạp ${created.profile.row_count.toLocaleString("vi-VN")} bản ghi từ ${created.profile.file_count} file.`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Không nạp được dữ liệu"); }
    finally { setBusy(false); }
  }

  async function retryRun() {
    if (!runId) return;
    setBusy(true);
    try { await startResolutionRun(runId); await refreshOverview(runId); setMessage("Đã đưa lần chạy vào hàng đợi."); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Không chạy lại được pipeline"); }
    finally { setBusy(false); }
  }

  async function switchRun(nextRunId: string) {
    setRunId(nextRunId);
    window.localStorage.setItem(activeRunKey(user.id), nextRunId);
    try { await refreshOverview(nextRunId); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Không tải được lần chạy"); }
  }

  return <AppShell stage={stage} pendingCount={overview?.counts.review ?? 0}
    mergeCount={overview?.counts.auto ?? 0} rejectedCount={overview?.counts.rejected ?? 0}
    processedCount={overview?.processed_count ?? 0} totalCount={overview?.total_count ?? 0}
    user={user} onNavigate={setStage}
    onLogout={() => void onLogout().catch(() => setMessage("Không thể đăng xuất lúc này."))}>
    <main className="container workspace resolution-workspace" id="top">
      <PageHeader stage={stage} />
      {message && <div className="notice toast" role="status"><span className="toast-icon" aria-hidden="true">i</span><span className="toast-message">{message}</span><button type="button" aria-label="Đóng thông báo" onClick={() => setMessage("")}>×</button></div>}
      {stage === "overview" && <section className="resolution-overview">
        <div className="resolution-overview-head">
          <div><p className="section-kicker">Dữ liệu khách hàng · lần nạp hiện tại</p><h2>{overview ? "Trạng thái phân giải" : "Bắt đầu từ dữ liệu của bạn"}</h2>
            <p>{overview ? `Run ${runId.slice(0, 8)} · ${overview.record_count.toLocaleString("vi-VN")} bản ghi · ${overview.file_count} file` : "Nạp một hoặc nhiều file CSV. Từng dòng có thể đến từ CRM, ERP hoặc BILL."}</p></div>
          <button className="button primary btn btn-primary" type="button" onClick={() => setStage("import")}>＋ Nạp dữ liệu</button>
        </div>
        {overview && <>
          {overview.status !== "COMPLETED" && <div className="run-progress card"><div><span>{overview.status === "RETRYABLE_FAILED" ? "Lần chạy bị gián đoạn" : polling ? "Đang phân giải toàn bộ dữ liệu" : "Đang chờ xử lý"}</span><strong>{overview.phase}</strong></div>
            <div className="progress-track"><span style={{ width: `${overview.total_count ? Math.round(overview.processed_count / overview.total_count * 100) : 0}%` }} /></div>
            <small>{overview.processed_count.toLocaleString("vi-VN")} / {overview.total_count.toLocaleString("vi-VN")} bản ghi</small>
            {(overview.status === "RETRYABLE_FAILED" || overview.status === "FAILED") && <button className="button outline" type="button" disabled={busy} onClick={() => void retryRun()}>Chạy lại</button>}
          </div>}
          <div className="stat-grid resolution-stats">
            <button className="card stat-card resolution-stat" type="button" onClick={() => setStage("auto")}><span className="stat-value">{overview.counts.auto.toLocaleString("vi-VN")}</span><span className="stat-label">Item sẽ gộp</span><span className="stat-change">Xem danh sách Gộp →</span></button>
            <button className="card stat-card resolution-stat" type="button" onClick={() => setStage("rejected")}><span className="stat-value">{overview.counts.rejected.toLocaleString("vi-VN")}</span><span className="stat-label">Cặp không gộp</span><span className="stat-change">Xem danh sách Không gộp →</span></button>
            <button className="card stat-card resolution-stat" type="button" onClick={() => setStage("review")}><span className="stat-value">{overview.counts.review.toLocaleString("vi-VN")}</span><span className="stat-label">Trường hợp cần xử lý</span><span className="stat-change">Mở hàng đợi review →</span></button>
            <button className="card stat-card resolution-stat" type="button" onClick={() => setStage("history")}><span className="stat-value">{overview.history_count.toLocaleString("vi-VN")}</span><span className="stat-label">Bản CSV đã xuất</span><span className="stat-change">Xem lịch sử →</span></button>
          </div>
          {runHistory.length > 1 && <section className="card recent-runs"><div className="recent-runs-title"><div><p className="section-kicker">Lần nạp dữ liệu</p><h3>Các lần nạp gần đây</h3></div><span className="muted-copy">Chọn một lần nạp để xem dữ liệu của nó</span></div>
            {runHistory.slice(0, 5).map((run) => <button type="button" className={`recent-run-row ${run.run_id === runId ? "current" : ""}`} key={run.run_id} onClick={() => void switchRun(run.run_id)}>
              <span><b>{run.files.join(", ") || "Lần nạp dữ liệu"}</b><small>{new Date(run.created_at).toLocaleString("vi-VN")}</small></span><span>{run.record_count.toLocaleString("vi-VN")} bản ghi</span><span>{run.status === "COMPLETED" ? "Đã xử lý" : run.status === "FAILED" ? "Thất bại" : "Đang xử lý"}</span>
            </button>)}
          </section>}
          <section className="card source-summary"><div><p className="section-kicker">Toàn bộ bản ghi đầu vào</p><h3>Phân bố nguồn dữ liệu</h3><small>{overview.record_count.toLocaleString("vi-VN")} bản ghi từ {overview.file_count} file</small></div><div className="source-summary-values">
            {Object.entries(overview.source_counts).map(([source, count]) => <span className="source-count" key={source}><b>{source}</b><strong>{count.toLocaleString("vi-VN")}</strong><span className="source-share" aria-label={`${source}: ${count.toLocaleString("vi-VN")} bản ghi`}><span style={{ width: `${overview.record_count ? count / overview.record_count * 100 : 0}%` }} /></span></span>)}
          </div></section>
        </>}
      </section>}

      {stage === "import" && <section className="card card-body resolution-intake">
        <div className="intake-copy"><div><p className="section-kicker">Dữ liệu đầu vào</p><h2>Phân giải mọi bản ghi trong một lần nạp</h2><p>Chọn một hoặc nhiều CSV cùng schema. Hệ thống đọc cột <code>source_system</code> trên từng dòng và dò trùng trên tất cả nguồn.</p></div></div>
        <form onSubmit={(event) => void handleUpload(event)}>
          <label className={`resolution-dropzone ${dragging ? "dragging" : ""}`}
            onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
            onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }}
            onDrop={(event) => { event.preventDefault(); setDragging(false); addFiles(Array.from(event.dataTransfer.files)); }}>
            <span className="drop-icon">＋</span><strong>{files.length ? `${files.length} file đã chọn` : "Thả file CSV vào đây hoặc chọn từ máy"}</strong><small>Chọn nhiều file; có thể bổ sung file trước khi nạp.</small>
            <input type="file" accept=".csv,text/csv" multiple onChange={(event) => { addFiles(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
          </label>
          <div className="intake-schema" aria-label="Các cột CSV"><span>Các cột CSV</span>{["id", "source_system", "full_name", "phone_number", "email_address", "raw_address"].map((column) => <code key={column}>{column}</code>)}</div>
          {files.length > 0 && <ul className="file-list">{files.map((file) => <li key={`${file.name}-${file.size}-${file.lastModified}`}><span>{file.name}</span><small>{(file.size / 1024).toFixed(0)} KB</small><button type="button" aria-label={`Bỏ file ${file.name}`} onClick={() => setFiles((current) => current.filter((selected) => selected !== file))}>Bỏ file ×</button></li>)}</ul>}
          <div className="intake-footer"><span>Tối đa 10 file mỗi lần nạp · 5 MiB và 5.000 dòng mỗi file.</span><button className="button primary btn btn-primary" type="submit" disabled={!files.length || busy}>{busy ? "Đang nạp…" : "Nạp và phân giải →"}</button></div>
        </form>
      </section>}

      {["auto", "rejected", "review", "history"].includes(stage) && <ResolutionFlow runId={runId} stage={stage} onChanged={() => refreshOverview(runId)} notify={setMessage} />}
    </main>
  </AppShell>;
}
