"use client";

import { useCallback, useEffect, useState } from "react";
import AppShell, { type Stage } from "@/components/AppShell";
import { PageHeader } from "@/components/DashboardPrimitives";
import ResolutionFlow from "@/components/ResolutionFlow";
import { ApiError, getResolutionOverview, getResolutionRuns, setCsrfToken,
  startResolutionRun, uploadResolutionFiles, type AuthUser } from "@/lib/api";
import type { ResolutionOverview, ResolutionRunHistory } from "@/lib/contracts";
import SeparateRecordsDialog from "./SeparateRecordsDialog";

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
  const [separateOpen, setSeparateOpen] = useState(false);

  function openDonutSegment(event: React.MouseEvent<HTMLButtonElement>) {
    if (!overview) return;
    if (event.detail === 0) { setStage("auto"); return; }
    const { left, top, width, height } = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - left - width / 2;
    const y = event.clientY - top - height / 2;
    const angle = (Math.atan2(x, -y) + Math.PI * 2) % (Math.PI * 2);
    const total = overview.counts.auto + overview.counts.review;
    setStage(total > 0 && angle / (Math.PI * 2) >= overview.counts.auto / total ? "review" : "auto");
  }

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
    mergeCount={overview?.counts.auto ?? 0}
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
          {overview.status === "COMPLETED" && <div className="resolution-overview-distribution card">
            <div><p className="section-kicker">KẾT QUẢ PHÂN GIẢI</p><h3>Phân bố cụm</h3><p>Bấm vào một phần của biểu đồ để mở danh sách tương ứng.</p>
              <div className="resolution-donut-legend"><button type="button" onClick={() => setStage("auto")}><i className="waiting" />Chờ gộp <b>{overview.counts.auto.toLocaleString("vi-VN")}</b></button><button type="button" onClick={() => setStage("review")}><i className="review" />Cần review <b>{overview.counts.review.toLocaleString("vi-VN")}</b></button></div>
            </div>
            <div className="resolution-donut" style={{ "--waiting-share": `${overview.counts.auto + overview.counts.review ? overview.counts.auto / (overview.counts.auto + overview.counts.review) * 100 : 0}%` } as React.CSSProperties}>
              <button type="button" className="resolution-donut-chart" aria-label={`Biểu đồ: Chờ gộp ${overview.counts.auto} cụm, Cần review ${overview.counts.review} cụm; bấm phần màu để mở danh sách`} onClick={openDonutSegment}><span><strong>{(overview.counts.auto + overview.counts.review).toLocaleString("vi-VN")}</strong><small>cụm</small></span></button>
            </div>
          </div>}
          {overview.status === "COMPLETED" && <div className="stat-grid resolution-stats">
            <button className="card stat-card resolution-stat" type="button" onClick={() => setSeparateOpen(true)}><span className="stat-value">{(overview.counts.separate ?? 0).toLocaleString("vi-VN")}</span><span className="stat-label">Dự kiến giữ riêng</span><span className="stat-change">Xem bản ghi →</span></button>
            <button className="card stat-card resolution-stat" type="button" onClick={() => setStage("history")}><span className="stat-value">{overview.history_count.toLocaleString("vi-VN")}</span><span className="stat-label">Bản CSV đã xuất</span><span className="stat-change">Xem lịch sử →</span></button>
          </div>}
          {overview.status === "COMPLETED" && <section className="card source-summary resolution-signal-summary" aria-label="Kết quả đối chiếu dữ liệu"><div><p className="section-kicker">ĐỐI CHIẾU DỮ LIỆU</p><h3>Các dấu hiệu tìm thấy</h3><small>{overview.candidate_count.toLocaleString("vi-VN")} cặp được đối chiếu · {overview.counts.insufficient.toLocaleString("vi-VN")} cặp chưa đủ thông tin để xác nhận</small></div><div className="source-summary-values">
            {([ ["A", overview.counts.group_a, "Thông tin khớp rõ", "Tên và thông tin liên hệ giống nhau"], ["C", overview.counts.group_c, "Cần thêm thông tin", "Tên chưa đủ rõ để xác nhận"], ["D", overview.counts.group_d, "Thông tin mâu thuẫn", "Tên khác nhau rõ rệt"] ] as const).map(([group, count, label, description]) => <span className="source-count" key={group}><b>{label}</b><strong>{count.toLocaleString("vi-VN")}</strong><small>{description}</small></span>)}
          </div></section>}
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
        <div className="intake-copy"><div><p className="section-kicker">Dữ liệu đầu vào</p><h2>Phân giải mọi bản ghi trong một lần nạp</h2><p>Chọn một hoặc nhiều CSV của Linh. Hệ thống đọc cột <code>source</code> trên từng dòng và dò trùng cả trong cùng nguồn lẫn giữa các nguồn.</p></div></div>
        <form onSubmit={(event) => void handleUpload(event)}>
          <label className={`resolution-dropzone ${dragging ? "dragging" : ""}`}
            onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
            onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }}
            onDrop={(event) => { event.preventDefault(); setDragging(false); addFiles(Array.from(event.dataTransfer.files)); }}>
            <span className="drop-icon">＋</span><strong>{files.length ? `${files.length} file đã chọn` : "Thả file CSV vào đây hoặc chọn từ máy"}</strong><small>Chọn nhiều file; có thể bổ sung file trước khi nạp.</small>
            <input type="file" accept=".csv,text/csv" multiple onChange={(event) => { addFiles(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
          </label>
          <div className="intake-schema" aria-label="Các cột CSV"><span>Các cột CSV</span>{["record_id", "source", "name", "phone", "email", "address"].map((column) => <code key={column}>{column}</code>)}</div>
          {files.length > 0 && <ul className="file-list">{files.map((file) => <li key={`${file.name}-${file.size}-${file.lastModified}`}><span>{file.name}</span><small>{(file.size / 1024).toFixed(0)} KB</small><button type="button" aria-label={`Bỏ file ${file.name}`} onClick={() => setFiles((current) => current.filter((selected) => selected !== file))}>Bỏ file ×</button></li>)}</ul>}
          <div className="intake-footer"><span>Tối đa 10 file mỗi lần nạp · 5 MiB và 10.000 dòng mỗi file.</span><button className="button primary btn btn-primary" type="submit" disabled={!files.length || busy}>{busy ? "Đang nạp…" : "Nạp và phân giải →"}</button></div>
        </form>
      </section>}

      {["auto", "review", "history"].includes(stage) && <ResolutionFlow runId={runId} stage={stage} onChanged={() => refreshOverview(runId)} notify={setMessage} />}
      {separateOpen && runId && <SeparateRecordsDialog runId={runId} onClose={() => setSeparateOpen(false)} />}
    </main>
  </AppShell>;
}
