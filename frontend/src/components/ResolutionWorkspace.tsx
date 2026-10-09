"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import AppShell, { type Stage } from "@/components/AppShell";
import PairReviewFlow from "./PairReviewFlow";
import SessionAnalytics from "./SessionAnalytics";
import { PageHeader } from "@/components/DashboardPrimitives";
import ResolutionFlow from "@/components/ResolutionFlow";
import { ApiError, bulkResolutionRuns, getResolutionOverview, getResolutionRuns, renameResolutionRun,
  setCsrfToken, startResolutionRun, type AuthUser, type RunListOptions } from "@/lib/api";
import type { ResolutionOverview, ResolutionRunHistory } from "@/lib/contracts";
import ResolutionIntake from "./ResolutionIntake";
import { can } from "@/lib/permissions";
import { runPhaseLabel, runStatusLabel, toSessionViewModel } from "@/lib/sessionView";
import SessionsHub from "./SessionsHub";
import AuditTrail from "./AuditTrail";
import WorkspaceDrawer from "./WorkspaceDrawer";
import PolicyAdmin from "./PolicyAdmin";

function activeRunKey(userId: string) { return `masterone.resolution-run.v1.${userId}`; }
const RUNS_PAGE_SIZE = 25;
const ACTIVE_RUNS_PAGE_SIZE = 5;
const stages: Stage[] = ["overview", "import", "auto", "review", "no_merge", "history", "audit", "policy"];
function readLocation() {
  const params = new URLSearchParams(window.location.search);
  const requestedStage = params.get("view") as Stage | null;
  return { runId: params.get("run") || "", stage: requestedStage && stages.includes(requestedStage) ? requestedStage : "overview" };
}
function allowedStage(stage: Stage, user: AuthUser): Stage {
  if (stage === "import" && !can(user, "create_run")) return "overview";
  if (stage === "audit" && !can(user, "read_audit")) return "overview";
  if (stage === "policy" && !can(user, "edit_policy") && !can(user, "simulate_policy")) return "overview";
  return stage;
}
function writeLocation(runId: string, stage: Stage, mode: "push" | "replace" = "push") {
  const url = new URL(window.location.href);
  runId ? url.searchParams.set("run", runId) : url.searchParams.delete("run");
  stage === "overview" ? url.searchParams.delete("view") : url.searchParams.set("view", stage);
  window.history[mode === "push" ? "pushState" : "replaceState"]({}, "", `${url.pathname}${url.search}${url.hash}`);
}

export default function ResolutionWorkspace({ user, onLogout }: { user: AuthUser; onLogout: () => Promise<void> }) {
  const [stage, setStage] = useState<Stage>("overview");
  const [runId, setRunId] = useState("");
  const activeRunRef = useRef("");
  const overviewRequestRef = useRef(0);
  const runsRequestRef = useRef(0);
  const activeRunsRequestRef = useRef(0);
  const [overview, setOverview] = useState<ResolutionOverview | null>(null);
  const [overviewStale, setOverviewStale] = useState(false);
  const [runHistory, setRunHistory] = useState<ResolutionRunHistory["items"]>([]);
  const [activeRuns, setActiveRuns] = useState<ResolutionRunHistory["items"]>([]);
  const [activeRunsTotal, setActiveRunsTotal] = useState(0);
  const [activeRunsStale, setActiveRunsStale] = useState(false);
  const [runTotal, setRunTotal] = useState(0);
  const [runsNextCursor, setRunsNextCursor] = useState<string | null>(null);
  const [workspacePanel, setWorkspacePanel] = useState<"upload" | "sessions" | null>(null);
  const [uploadPending, setUploadPending] = useState("");
  const [panelExpanded, setPanelExpanded] = useState(false);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runsError, setRunsError] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessageState] = useState("");
  const [messageVersion, setMessageVersion] = useState(0);
  const session = overview ? toSessionViewModel(overview, user, runHistory.find((run) => run.run_id === runId)) : null;
  const estimatedEntityCount = overview?.estimated_entity_count ?? overview?.counts.separate ?? 0;
  const duplicateRecordCount = overview?.duplicate_record_count
    ?? Math.max(0, (overview?.record_count ?? 0) - estimatedEntityCount);

  function openDonutSegment(event: React.MouseEvent<HTMLButtonElement>) {
    if (!overview) return;
    if (event.detail === 0) { navigateStage("auto"); return; }
    const { left, top, width, height } = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - left - width / 2;
    const y = event.clientY - top - height / 2;
    const angle = (Math.atan2(x, -y) + Math.PI * 2) % (Math.PI * 2);
    const total = overview.counts.auto + overview.counts.review;
    navigateStage(total > 0 && angle / (Math.PI * 2) >= overview.counts.auto / total ? "review" : "auto");
  }

  const setMessage = useCallback((next: string) => {
    setMessageState(next);
    setMessageVersion((version) => version + 1);
  }, []);

  const navigateStage = useCallback((next: Stage, mode: "push" | "replace" = "push") => {
    setStage(next);
    writeLocation(activeRunRef.current, next, mode);
  }, []);

  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => setMessageState(""), 6000);
    return () => window.clearTimeout(timer);
  }, [message, messageVersion]);

  const refreshOverview = useCallback(async (id: string) => {
    const requestId = ++overviewRequestRef.current;
    const result = await getResolutionOverview(id);
    if (requestId === overviewRequestRef.current && activeRunRef.current === id) {
      setOverview(result);
      setOverviewStale(false);
    }
    return result;
  }, []);
  const refreshRuns = useCallback(async (offset = 0, options: RunListOptions = {}) => {
    const requestId = ++runsRequestRef.current;
    setRunsLoading(true); setRunsError("");
    try {
      const result = await getResolutionRuns(RUNS_PAGE_SIZE, offset, options);
      if (requestId === runsRequestRef.current) {
        setRunsNextCursor(result.next_cursor ?? null); setRunHistory(result.items); setRunTotal(result.total);
      }
      return result;
    } catch (error) {
      if (requestId === runsRequestRef.current) setRunsError(error instanceof Error ? error.message : "Không tải được danh sách phiên.");
      throw error;
    } finally { if (requestId === runsRequestRef.current) setRunsLoading(false); }
  }, []);

  const refreshActiveRuns = useCallback(async () => {
    const requestId = ++activeRunsRequestRef.current;
    try {
      const result = await getResolutionRuns(ACTIVE_RUNS_PAGE_SIZE, 0, { status: "QUEUED,RUNNING" });
      if (requestId === activeRunsRequestRef.current) {
        setActiveRuns(result.items);
        setActiveRunsTotal(result.total);
        setActiveRunsStale(false);
      }
      return result;
    } catch (error) {
      if (requestId === activeRunsRequestRef.current) setActiveRunsStale(true);
      throw error;
    }
  }, []);

  useEffect(() => {
    setCsrfToken(user.csrf_token);
    let cancelled = false;
    const initialLocation = readLocation();
    const location = { ...initialLocation, stage: allowedStage(initialLocation.stage, user) };
    const isInitialLocationCurrent = () => {
      const current = readLocation();
      return current.runId === initialLocation.runId && current.stage === initialLocation.stage;
    };
    setStage(location.stage);
    async function initialize() {
      let list: ResolutionRunHistory["items"] = [];
      try { list = (await refreshRuns(0)).items; } catch { /* The Hub keeps a retryable error. */ }
      void refreshActiveRuns().catch(() => undefined);
      if (cancelled || !isInitialLocationCurrent()) return;

      let requested = location.runId;
      let requestedIsValid = false;
      if (requested) {
        activeRunRef.current = requested; setRunId(requested);
        try { await refreshOverview(requested); requestedIsValid = true; }
        catch (error) {
          if (error instanceof ApiError && (error.status === 404 || error.status === 403)) {
            requested = ""; activeRunRef.current = ""; setRunId(""); setOverview(null);
            setMessage("Phiên trong đường dẫn không tồn tại hoặc bạn không có quyền xem.");
          } else {
            if (!cancelled) setMessage(error instanceof Error ? error.message : "Không tải được phiên trong đường dẫn.");
            return;
          }
        }
      }
      if (cancelled || !isInitialLocationCurrent() || (location.runId && requestedIsValid && activeRunRef.current !== requested)) return;
      if (requestedIsValid) {
        try { window.localStorage.setItem(activeRunKey(user.id), requested); } catch { /* The URL remains authoritative. */ }
        writeLocation(requested, location.stage, "replace");
        return;
      }

      let saved = "";
      if (!location.runId) {
        try { saved = window.localStorage.getItem(activeRunKey(user.id)) || ""; } catch { /* Storage is optional. */ }
      }
      const candidates = [...new Set([
        list.find((run) => run.status === "RUNNING" || run.status === "QUEUED")?.run_id || "",
        saved,
        list[0]?.run_id || "",
      ].filter(Boolean))];
      for (const candidateId of candidates) {
        if (cancelled || !isInitialLocationCurrent()) return;
        activeRunRef.current = candidateId; setRunId(candidateId);
        try {
          await refreshOverview(candidateId);
          if (!cancelled && activeRunRef.current === candidateId) {
            try { window.localStorage.setItem(activeRunKey(user.id), candidateId); } catch { /* Optional restore hint. */ }
            writeLocation(candidateId, location.stage, "replace");
          }
          return;
        } catch (error) {
          if (error instanceof ApiError && (error.status === 404 || error.status === 403)) {
            if (candidateId === saved) {
              try { window.localStorage.removeItem(activeRunKey(user.id)); } catch { /* Storage is optional. */ }
            }
            continue;
          }
          if (!cancelled && activeRunRef.current === candidateId) setMessage(error instanceof Error ? error.message : "Không tải được phiên gần nhất.");
          return;
        }
      }
      if (!cancelled && isInitialLocationCurrent()) {
        activeRunRef.current = ""; setRunId(""); setOverview(null); writeLocation("", location.stage, "replace");
      }
    }
    void initialize();
    function onPopState() {
      const next = readLocation();
      next.stage = allowedStage(next.stage, user);
      setStage(next.stage);
      if (next.runId === activeRunRef.current) return;
      activeRunRef.current = next.runId; overviewRequestRef.current += 1;
      setRunId(next.runId); setOverview(null); setOverviewStale(false);
      if (!next.runId) return;
      if (next.runId) void refreshOverview(next.runId).catch((error: unknown) => {
        if (activeRunRef.current === next.runId) setMessage(error instanceof Error ? error.message : "Không tải được phiên.");
      });
    }
    window.addEventListener("popstate", onPopState);
    return () => { cancelled = true; window.removeEventListener("popstate", onPopState); };
  }, [refreshActiveRuns, refreshOverview, refreshRuns, user]);

  useEffect(() => {
    if (!runId || !overview || !["QUEUED", "RUNNING"].includes(overview.status)) return;
    let stopped = false;
    let timer = 0;
    let delay = 1500;
    const poll = async () => {
      try {
        const latest = await refreshOverview(runId);
        if (stopped) return;
        delay = 1500;
        if (!["QUEUED", "RUNNING"].includes(latest.status)) return;
      } catch {
        if (!stopped && activeRunRef.current === runId) setOverviewStale(true);
        delay = Math.min(delay * 2, 30000);
      }
      if (!stopped) timer = window.setTimeout(() => void poll(), delay);
    };
    timer = window.setTimeout(() => void poll(), delay);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [overview?.status, refreshOverview, runId]);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    let inFlight = false;
    let delay = activeRunsTotal > 0 ? 5000 : 30000;
    const schedule = () => {
      if (stopped || document.visibilityState !== "visible") return;
      timer = window.setTimeout(() => void poll(), delay);
    };
    const poll = async () => {
      if (stopped || inFlight || document.visibilityState !== "visible") return;
      inFlight = true;
      try {
        const result = await refreshActiveRuns();
        delay = result.total > 0 ? 5000 : 30000;
      } catch {
        delay = Math.min(delay * 2, 60000);
      } finally {
        inFlight = false;
      }
      schedule();
    };
    const refreshWhenVisible = () => {
      if (document.visibilityState !== "visible") {
        if (timer !== undefined) window.clearTimeout(timer);
        timer = undefined;
        return;
      }
      if (timer !== undefined) window.clearTimeout(timer);
      timer = undefined;
      void poll();
    };
    document.addEventListener("visibilitychange", refreshWhenVisible);
    window.addEventListener("focus", refreshWhenVisible);
    schedule();
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
      window.removeEventListener("focus", refreshWhenVisible);
    };
  }, [activeRunsTotal, refreshActiveRuns]);

  async function handleRunCreated(created: { run_id: string; profile: { row_count: number; file_count: number } }) {
    if (!can(user, "create_run") || !can(user, "start_run")) return;
    try {
      activeRunRef.current = created.run_id; overviewRequestRef.current += 1;
      setRunId(created.run_id); setOverview(null); setOverviewStale(false);
      try { window.localStorage.setItem(activeRunKey(user.id), created.run_id); } catch { /* URL remains authoritative. */ }
      writeLocation(created.run_id, "overview");
      const existing = await getResolutionOverview(created.run_id);
      if (existing.status === "UPLOADED") {
        try { await startResolutionRun(created.run_id); }
        catch (error) {
          if (!(error instanceof ApiError) || error.status !== 409) throw error;
          const latest = await getResolutionOverview(created.run_id);
          if (!["QUEUED", "RUNNING", "COMPLETED"].includes(latest.status)) throw error;
        }
      }
      await refreshOverview(created.run_id);
      await refreshRuns(0);
      await refreshActiveRuns().catch(() => undefined);
      navigateStage("overview", "replace");
      setWorkspacePanel((panel) => panel === "upload" ? null : panel);
      setMessage(`Đã nạp ${created.profile.row_count.toLocaleString("vi-VN")} bản ghi từ ${created.profile.file_count} file.`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Không thể bắt đầu phiên vừa tạo."); throw error; }
  }

  async function retryRun() {
    if (!runId || !(session?.actions.start || session?.actions.retry)) return;
    setBusy(true);
    try {
      await startResolutionRun(runId);
      await refreshOverview(runId);
      await refreshActiveRuns().catch(() => undefined);
      setMessage("Đã đưa lần chạy vào hàng đợi.");
    }
    catch (error) { setMessage(error instanceof Error ? error.message : "Không chạy lại được pipeline"); }
    finally { setBusy(false); }
  }

  async function switchRun(nextRunId: string) {
    if (nextRunId === activeRunRef.current) { setWorkspacePanel(null); return; }
    activeRunRef.current = nextRunId; overviewRequestRef.current += 1;
    setRunId(nextRunId); setOverview(null); setOverviewStale(false); setWorkspacePanel(null);
    try { window.localStorage.setItem(activeRunKey(user.id), nextRunId); } catch { /* URL remains authoritative. */ }
    writeLocation(nextRunId, stage);
    try { await refreshOverview(nextRunId); }
    catch (error) {
      if (activeRunRef.current === nextRunId) {
        if (error instanceof ApiError && (error.status === 404 || error.status === 403)) setMessage("Phiên không tồn tại hoặc bạn không có quyền xem.");
        else setMessage(error instanceof Error ? error.message : "Không tải được phiên");
      }
    }
  }

  const closeWorkspacePanel = useCallback(() => {
    setWorkspacePanel(null);
    setPanelExpanded(false);
    const triggerClass = workspacePanel === "upload" ? ".upload-open-button" : ".sessions-open-button";
    window.requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(triggerClass)?.focus());
  }, [workspacePanel]);
  const openUploadPanel = useCallback(() => {
    if (!can(user, "create_run")) return;
    setPanelExpanded(false);
    setWorkspacePanel("upload");
  }, [user]);
  const openSessionsPanel = useCallback(() => {
    setPanelExpanded(false);
    setWorkspacePanel("sessions");
    void refreshRuns(0).catch(() => undefined);
  }, [refreshRuns]);
  useEffect(() => {
    if (stage === "import") openUploadPanel();
  }, [stage, openUploadPanel]);

  const selectedRun = runHistory.find((run) => run.run_id === runId);
  const sessionLabel = selectedRun?.name || selectedRun?.files.join(", ") || (runId ? `Phiên ${runId.slice(0, 8)}` : undefined);

  return <AppShell stage={stage} pendingCount={overview?.counts.review_pairs ?? overview?.counts.review ?? 0}
    mergeCount={overview?.counts.auto ?? 0}
    totalCount={overview?.record_count ?? 0}
    runStatus={overview?.status} sessionLabel={sessionLabel} activeRuns={activeRuns} activeTotal={activeRunsTotal}
    activeStale={activeRunsStale} onOpenRun={(id) => void switchRun(id)} user={user} onNavigate={(next) => {
      if (next === "import") openUploadPanel();
      else navigateStage(next);
    }}
    onOpenSessions={openSessionsPanel} uploadPending={uploadPending}
    onLogout={() => void onLogout().catch(() => setMessage("Không thể đăng xuất lúc này."))}>
    <main className="container workspace resolution-workspace" id="top">
      {stage !== "overview" && <PageHeader stage={stage} />}
      {!can(user, "create_run") && <p role="note">Tài khoản {user.role || "chưa xác định quyền"} đang ở chế độ chỉ xem. Liên hệ người quản trị để được cấp quyền thao tác.</p>}
      {message && <div className="notice toast" role="status"><span className="toast-icon" aria-hidden="true">i</span><span className="toast-message">{message}</span><button type="button" aria-label="Đóng thông báo" onClick={() => setMessage("")}>×</button></div>}
      {stage === "overview" && <section className="resolution-overview">
        <div className="resolution-overview-head">
          <div><h2>{overview ? "Tổng quan phiên" : "Bắt đầu từ dữ liệu của bạn"}</h2>
            <p>{overview ? `${sessionLabel} · ${overview.file_count} file` : "Nạp các file CSV để tìm bản ghi trùng và hợp nhất dữ liệu."}</p></div>
          {can(user, "create_run") && <button className="button primary btn btn-primary upload-open-button" type="button" onClick={openUploadPanel}>＋ Nạp dữ liệu</button>}
        </div>
        {overview && <>
          {overviewStale && <div className="run-stale" role="status"><span>Chưa kết nối được để cập nhật phiên; đang giữ trạng thái gần nhất.</span><button type="button" onClick={() => void refreshOverview(runId).catch(() => setOverviewStale(true))}>Thử cập nhật</button></div>}
          {overview.status !== "COMPLETED" && <div className="run-progress card"><div><span>{session?.statusLabel}</span><strong>{runPhaseLabel(overview.phase)}</strong></div>
            {session?.processing && <p role="status">Đang chờ kết quả xử lý. Chưa có phần trăm tiến độ chi tiết.</p>}
            <small>{overview.record_count.toLocaleString("vi-VN")} bản ghi đầu vào</small>
            {session?.errorCode && <p>Mã lỗi: {session.errorCode}</p>}
            {(session?.actions.start || session?.actions.retry) && <button className="button outline" type="button" disabled={busy} onClick={() => void retryRun()}>{session.actions.start ? "Bắt đầu xử lý" : "Chạy lại"}</button>}
          </div>}
          {overview.status === "COMPLETED" && <section className="overview-dashboard">
            <div className="overview-dashboard-kpis">
              <div className="card overview-kpi"><strong>{overview.record_count.toLocaleString("vi-VN")}</strong><span>Bản ghi</span><small>Từ {overview.file_count} file dữ liệu</small></div>
              <div className="card overview-kpi"><strong>{estimatedEntityCount.toLocaleString("vi-VN")}</strong><span>Thực thể ước tính</span><small>Sau các đề xuất gộp hiện có</small></div>
              <div className="card overview-kpi overview-kpi-highlight"><strong>{duplicateRecordCount.toLocaleString("vi-VN")}</strong><span>Bản ghi trùng dự kiến loại bỏ</span><small>Nếu áp dụng đề xuất gộp</small></div>
              <button className="card overview-kpi overview-kpi-review" type="button" onClick={() => navigateStage("review")}><strong>{(overview.counts.review_pairs ?? overview.counts.review).toLocaleString("vi-VN")}</strong><span>Cần xem xét</span><small>Cặp bản ghi chờ xác nhận →</small></button>
            </div>
            <SessionAnalytics runId={runId} revision={`${overview.counts.auto}:${overview.counts.review_pairs ?? overview.counts.review}:${overview.counts.rejected}`} />
          </section>}
          {overview.status === "COMPLETED" && <div className="resolution-overview-distribution card">
            <div><h3>Phân bố công việc</h3><p>Các mục đang chờ quyết định</p>
              <div className="resolution-donut-legend"><button type="button" onClick={() => navigateStage("auto")}><i className="waiting" />Chờ gộp <b>{overview.counts.auto.toLocaleString("vi-VN")}</b></button><button type="button" onClick={() => navigateStage("review")}><i className="review" />Cần review <b>{overview.counts.review.toLocaleString("vi-VN")}</b></button></div>
            </div>
            <div className="resolution-donut" style={{ "--waiting-share": `${overview.counts.auto + overview.counts.review ? overview.counts.auto / (overview.counts.auto + overview.counts.review) * 100 : 0}%` } as React.CSSProperties}>
              <button type="button" className="resolution-donut-chart" aria-label={`Biểu đồ: Chờ gộp ${overview.counts.auto} cụm, Cần review ${overview.counts.review} item; bấm phần màu để mở danh sách`} onClick={openDonutSegment}><span><strong>{(overview.counts.auto + overview.counts.review).toLocaleString("vi-VN")}</strong><small>mục công việc</small></span></button>
            </div>
          </div>}
          {overview.status === "COMPLETED" && <div className="stat-grid resolution-stats">
            <div className="card stat-card resolution-stat"><span className="stat-value">{overview.record_count.toLocaleString("vi-VN")}</span><span className="stat-label">Bản ghi đầu vào</span><span className="stat-change">{overview.file_count} file dữ liệu</span></div>
            <button className="card stat-card resolution-stat stat-primary" type="button" onClick={() => navigateStage("auto")}><span className="stat-value">{overview.counts.auto.toLocaleString("vi-VN")}</span><span className="stat-label">Cụm chờ gộp</span><span className="stat-change">Xem danh sách →</span></button>
            <button className="card stat-card resolution-stat stat-review" type="button" onClick={() => navigateStage("review")}><span className="stat-value">{overview.counts.review.toLocaleString("vi-VN")}</span><span className="stat-label">Mục cần review</span><span className="stat-change">Mở hàng đợi →</span></button>
          </div>}
          {overview.status === "COMPLETED" && <section className="card source-summary resolution-signal-summary" aria-label="Kết quả đối chiếu dữ liệu"><div><p className="section-kicker">ĐỐI CHIẾU DỮ LIỆU</p><h3>Các dấu hiệu tìm thấy</h3><small>{overview.candidate_count.toLocaleString("vi-VN")} cặp được đối chiếu · {overview.counts.insufficient.toLocaleString("vi-VN")} cặp chưa đủ thông tin để xác nhận</small></div><div className="source-summary-values">
            {([ ["A", overview.counts.group_a, "Thông tin khớp rõ", "Tên và thông tin liên hệ giống nhau"], ["C", overview.counts.group_c, "Cần thêm thông tin", "Tên chưa đủ rõ để xác nhận"], ["D", overview.counts.group_d, "Thông tin mâu thuẫn", "Tên khác nhau rõ rệt"] ] as const).map(([group, count, label, description]) => <span className="source-count" key={group}><b>{label}</b><strong>{count.toLocaleString("vi-VN")}</strong><small>{description}</small></span>)}
          </div></section>}
          {runHistory.length > 1 && <section className="card recent-runs"><div className="recent-runs-title"><div><p className="section-kicker">Lần nạp dữ liệu</p><h3>Các lần nạp gần đây</h3></div><span className="muted-copy">Chọn một lần nạp để xem dữ liệu của nó</span></div>
            {runHistory.slice(0, 5).map((run) => <button type="button" className={`recent-run-row ${run.run_id === runId ? "current" : ""}`} key={run.run_id} onClick={() => void switchRun(run.run_id)}>
              <span><b>{run.name || run.files.join(", ") || "Phiên phân giải"}</b><small>{new Date(run.created_at).toLocaleString("vi-VN")}</small></span><span>{run.record_count.toLocaleString("vi-VN")} bản ghi</span><span>{runStatusLabel(run.status)}</span>
            </button>)}
          </section>}
          <section className="card source-summary"><div><p className="section-kicker">Toàn bộ bản ghi đầu vào</p><h3>Phân bố nguồn dữ liệu</h3><small>{overview.record_count.toLocaleString("vi-VN")} bản ghi từ {overview.file_count} file</small></div><div className="source-summary-values">
            {Object.entries(overview.source_counts).map(([source, count]) => <span className="source-count" key={source}><b>{source}</b><strong>{count.toLocaleString("vi-VN")}</strong><span className="source-share" aria-label={`${source}: ${count.toLocaleString("vi-VN")} bản ghi`}><span style={{ width: `${overview.record_count ? count / overview.record_count * 100 : 0}%` }} /></span></span>)}
          </div></section>
        </>}
      </section>}

      {stage === "import" && can(user, "create_run") && <button type="button" className="button outline" onClick={openUploadPanel}>Tiếp tục chuẩn bị dữ liệu</button>}

      {stage === "audit" && can(user, "read_audit") && (runId
        ? <AuditTrail key={runId} runId={runId} />
        : <div className="card empty-state"><b>Chưa chọn phiên</b><p>Mở một phiên từ Hub để xem các thao tác đã ghi nhận.</p></div>)}

      {stage === "policy" && (can(user, "edit_policy") || can(user, "simulate_policy")) &&
        <PolicyAdmin user={user} runId={runId || null} />}

      {["auto", "history"].includes(stage) && runId && <ResolutionFlow key={runId} user={user} runId={runId} stage={stage} onChanged={() => refreshOverview(runId)} notify={setMessage} />}
      {(stage === "review" || stage === "no_merge") && runId && <PairReviewFlow key={`${runId}:${stage}`} user={user} runId={runId} rejected={stage === "no_merge"} onChanged={() => refreshOverview(runId)} notify={setMessage} />}
    </main>
    {can(user, "create_run") && <WorkspaceDrawer title="Nạp dữ liệu" subtitle="Tạo phiên phân giải mới" side="left"
      open={workspacePanel === "upload"} expanded={panelExpanded} onToggleExpanded={() => setPanelExpanded((expanded) => !expanded)} onClose={closeWorkspacePanel}>
      <ResolutionIntake key={user.id} userId={user.id} onRunCreated={handleRunCreated} onPendingChange={setUploadPending} onDismiss={closeWorkspacePanel} />
    </WorkspaceDrawer>}
    {workspacePanel === "sessions" && <WorkspaceDrawer title="Lịch sử phiên"
      side="right" subtitle={`${runTotal.toLocaleString("vi-VN")} phiên · ${activeRunsTotal} đang chạy`}
      expanded={panelExpanded} onToggleExpanded={() => setPanelExpanded((expanded) => !expanded)} onClose={closeWorkspacePanel}>
      <SessionsHub items={runHistory} total={runTotal} nextCursor={runsNextCursor} loading={runsLoading} error={runsError}
      onPage={(cursor, options) => void refreshRuns(0, { ...options, cursor }).catch(() => undefined)}
      onRetry={(options) => void refreshRuns(0, options).catch(() => undefined)}
      onOpen={(id) => void switchRun(id)} onClose={closeWorkspacePanel} canManage={can(user, "manage_run")}
      onRename={(id, nextName) => renameResolutionRun(id, nextName).then(() => undefined)}
      onBulk={(operation, ids) => bulkResolutionRuns(operation, ids).then(() => undefined)} embedded onExpand={panelExpanded ? undefined : () => setPanelExpanded(true)} />
    </WorkspaceDrawer>}
  </AppShell>;
}
