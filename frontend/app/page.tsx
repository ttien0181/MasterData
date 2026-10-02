"use client";

import { useEffect, useMemo, useState } from "react";
import { clearActiveImport, readActiveImport, saveActiveImport, setActiveImportUser } from "@/lib/activeImport";
import { ApiError, decideCandidate, getCandidates, getDraft, getImport, getMe, logout, saveDraft, setCsrfToken, uploadFiles, type AuthUser } from "@/lib/api";
import { useRouter } from "next/navigation";
import type { Candidate, DraftField, ImportSummary, Source } from "@/lib/contracts";
import AppShell, { Icon, type Stage } from "@/components/AppShell";
import { fields } from "@/lib/comparison";
import { mergeR2Files, uploadFilesToR2 } from "@/lib/largeUpload";
import type { UploadProgress } from "@/lib/largeUpload";
import { FileDropzone, KpiCard, PageHeader } from "@/components/DashboardPrimitives";
import { ReviewDetail, ReviewQueue } from "@/components/ReviewPanels";
import { FieldSourcePicker, GoldenRecordPreview } from "@/components/GoldenPanels";

export default function Home() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    void getMe().then((found) => { setCsrfToken(found.csrf_token); setActiveImportUser(found.id); setUser(found); })
      .catch(() => router.replace("/login")).finally(() => setLoading(false));
  }, [router]);
  if (loading || !user) return <main className="auth-page"><div className="auth-card auth-loading" role="status"><span className="loading-symbol">M</span><span className="loading-line" /><span className="loading-line short" /><p>Đang kiểm tra phiên đăng nhập…</p></div></main>;
  return <Workspace user={user} onLogout={async () => {
    await logout();
    clearActiveImport(); setActiveImportUser(""); setCsrfToken(""); router.replace("/login");
  }} />;
}

function Workspace({ user, onLogout }: { user: AuthUser; onLogout: () => Promise<void> }) {
  const [stage, setStage] = useState<Stage>("import");
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [crm, setCrm] = useState<File | null>(null);
  const [erp, setErp] = useState<File | null>(null);
  const [bill, setBill] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<{ filename: string; progress: UploadProgress } | null>(null);
  const [message, setMessage] = useState("");
  const [draft, setDraft] = useState<Record<string, DraftField>>({});
  const [draftSaved, setDraftSaved] = useState(false);
  const active = useMemo(() => candidates.find((item) => item.id === selected) ?? null, [candidates, selected]);
  const sourceTypes: Source[] = ["CRM", "ERP", "BILL"];
  const totalRows = summary ? Object.values(summary.profile).reduce((total, item) => total + (item?.row_count ?? 0), 0) : 0;
  const pendingCount = candidates.filter((item) => item.decision === "pending").length;
  const acceptedCount = candidates.filter((item) => item.decision === "accepted").length;
  const processedCount = candidates.filter((item) => item.decision === "accepted" || item.decision === "rejected").length;
  const validationIssues = summary ? Object.values(summary.profile).reduce((total, item) => total + (item?.validation_issue_count ?? 0), 0) : 0;

  function navigateStage(next: Stage) {
    setStage(next);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function loadImport(id: string) {
    try {
      const imported = await getImport(id);
      const suggestions = await getCandidates(id);
      setSummary(imported);
      setCandidates(suggestions);
      setSelected((current) => current ?? suggestions[0]?.id ?? null);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        clearActiveImport();
        setSummary(null);
        setCandidates([]);
        setMessage("Import đã bị xoá khỏi máy chủ. Hãy tải lại các file để tiếp tục.");
      } else setMessage(error instanceof Error ? error.message : "Không thể kết nối API");
    }
  }

  useEffect(() => {
    const id = readActiveImport();
    if (id) void loadImport(id);
    // Restore once when the workspace opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (stage !== "golden" || !active || active.decision !== "accepted") return;
    void getDraft(active.id).then((result) => { setDraft(result.fields); setDraftSaved(result.state === "draft"); }).catch((error: unknown) => {
      setMessage(error instanceof Error ? error.message : "Không tải được bản nháp");
    });
  }, [active, stage]);

  async function handleUpload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!crm || !erp) return;
    setBusy(true); setMessage("");
    try {
      const selectedFiles = [crm, erp, bill].filter((file): file is File => file !== null);
      const r2Uploads = await uploadFilesToR2(selectedFiles, user.csrf_token, (file, progress) => {
        setUploadProgress({ filename: file.name, progress });
        setMessage(`Đang tải ${file.name} lên Cloudflare R2: ${progress.percent}%`);
      });
      setUploadProgress(null);
      setMessage("Đã tải file nguồn lên R2. Đang gộp CSV và chuẩn hóa thứ tự cột…");
      const merged = await mergeR2Files(r2Uploads.map((item) => item.object_name), user.csrf_token);
      const result = await uploadFiles(crm, erp, bill);
      saveActiveImport(result.import_id);
      const imported = await getImport(result.import_id);
      const suggestions = await getCandidates(result.import_id);
      setSummary(imported); setCandidates(suggestions); setSelected(suggestions[0]?.id ?? null); setStage("review");
      setMessage(`Đã gộp ${merged.rows_written.toLocaleString("vi-VN")} dòng, ${merged.columns.length} cột vào R2: ${merged.output_object_key}`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Không thể tải dữ liệu"); }
    finally { setBusy(false); setUploadProgress(null); }
  }

  async function handleDecision(decision: "accepted" | "rejected" | "needs_review") {
    if (!active) return;
    setBusy(true);
    try {
      await decideCandidate(active.id, decision);
      setCandidates((items) => items.map((item) => item.id === active.id ? { ...item, decision } : item));
      if (decision === "accepted") {
        setDraft({});
        setDraftSaved(false);
        setStage("golden");
      }
      setMessage(decision === "accepted" ? "Đã chấp nhận. Bạn có thể chọn nguồn cho từng trường." : "Quyết định đã được lưu vào lịch sử.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Không lưu được quyết định"); }
    finally { setBusy(false); }
  }

  async function handleSaveDraft() {
    if (!active) return;
    setBusy(true);
    try { await saveDraft(active.id, draft); setDraftSaved(true); setMessage("Đã lưu Golden Record nháp và audit. Dữ liệu nguồn chưa bị thay đổi."); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Không lưu được bản nháp"); }
    finally { setBusy(false); }
  }

  const requiredGoldenFields = active ? fields.filter((field) => active.left.raw_fields[field] || active.right.raw_fields[field]) : [];
  const draftComplete = requiredGoldenFields.length > 0 && requiredGoldenFields.every((field) => draft[field]);

  function chooseField(field: string, source: Candidate["left"]) {
    const value = source.raw_fields[field];
    if (value) { setDraft((current) => ({ ...current, [field]: { value, source_record_id: source.id } })); setDraftSaved(false); }
  }

  return (
    <AppShell stage={stage} pendingCount={pendingCount} processedCount={processedCount} totalCount={candidates.length} user={user} onNavigate={navigateStage} onLogout={() => void onLogout().catch(() => setMessage("Không thể đăng xuất lúc này. Hãy thử lại."))}>
      <main className="container workspace" id="top">
        <PageHeader stage={stage} />

        {/* Global status toast */}
        {message && (
          <div className="notice toast" role="status">
            <span aria-hidden="true">↳</span>
            <span className="toast-message">{message}</span>
            <button type="button" aria-label="Đóng thông báo" onClick={() => setMessage("")}>×</button>
          </div>
        )}

        {/* STAGE 1: DATA INTAKE */}
        {stage === "import" && (
          <section className="card card-body import-panel">
            {/* Stat Cards */}
            <div className="stat-grid">
              <KpiCard icon={<Icon name="database" size={20} />} value={totalRows.toLocaleString("vi-VN")} label="Bản ghi đã nạp" detail="Từ CRM, ERP và BILL" />
              <KpiCard icon={<Icon name="scan" size={20} />} value={candidates.length} label="Cặp gợi ý trùng lặp" detail={candidates.length > 0 ? "Sẵn sàng duyệt" : "Chưa có gợi ý"} />
              <KpiCard icon={<Icon name="layers" size={20} />} value={pendingCount} label="Cần Data Steward duyệt" detail={`${acceptedCount} đã chấp nhận`} tone="up" />
              <KpiCard icon={<Icon name="alert" size={20} />} value={validationIssues} label="Lỗi validation" detail="Kiểm tra UTF-8 & header" />
            </div>

            {/* File Upload Grid */}
            <form onSubmit={handleUpload}>
              <div className="upload-grid file-grid">
                {([
                  { source: "CRM", file: crm, setFile: setCrm, orderLabel: "Nguồn thứ nhất", title: "CRM System", hint: "Dữ liệu khách hàng từ CRM" },
                  { source: "ERP", file: erp, setFile: setErp, orderLabel: "Nguồn thứ hai", title: "ERP System", hint: "Dữ liệu khách hàng từ ERP" },
                  { source: "BILL", file: bill, setFile: setBill, orderLabel: "Nguồn thứ ba · tùy chọn", title: "Billing System", hint: "Dữ liệu hóa đơn và thanh toán" },
                ] satisfies { source: Source; file: File | null; setFile: (file: File | null) => void; orderLabel: string; title: string; hint: string }[]).map(({ source, file, setFile, orderLabel, title, hint }) => (
                  <FileDropzone key={source} source={source} file={file} onChange={setFile} orderLabel={orderLabel} title={title} hint={hint} />
                ))}
              </div>

              {/* Requirement & Submit Bar */}
              <div className="req-bar form-foot">
                <span>ID nguồn: <code>record_id</code> hoặc <code>source_record_id</code>; cần có <code>name</code> · BILL tùy chọn · Tối đa 10.000 dòng và 10 MiB/file</span>
                <button className="button primary btn btn-primary" type="submit" disabled={!crm || !erp || busy}>
                  {busy ? "Đang tải, gộp & phân tích…" : "Tải lên, gộp & phân tích"}
                  <span>→</span>
                </button>
              </div>
              {uploadProgress && <div className="upload-progress-row" role="status">
                <progress value={uploadProgress.progress.percent} max="100" aria-label={`Đang tải ${uploadProgress.filename} lên R2`} />
                <span>{uploadProgress.filename}: {uploadProgress.progress.percent}%</span>
              </div>}
            </form>

            {/* Profile Strip */}
            {summary && (
              <div className="profile-strip">
                {sourceTypes.filter((source) => summary.profile[source]).map((source) => (
                  <div key={source}>
                    <b>{source} Profiling</b>
                    <span>{summary.profile[source]?.row_count ?? 0} dòng nạp · {summary.profile[source]?.validation_issue_count ?? 0} lỗi validation</span>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {/* STAGE 2: REVIEW QUEUE & COMPARISON MATRIX TABLE */}
        {stage === "review" && (
          <>
          <div className="review-overview" role="progressbar" aria-label="Tiến độ xử lý cặp hồ sơ" aria-valuemin={0} aria-valuemax={candidates.length || 1} aria-valuenow={processedCount}>
            <div><span>TIẾN ĐỘ DUYỆT</span><strong>{processedCount}/{candidates.length} cặp đã xử lý</strong></div>
            <div className="review-overview-track"><span style={{ width: `${candidates.length ? processedCount / candidates.length * 100 : 0}%` }} /></div>
          </div>
          <div className="review-layout">
            <ReviewQueue candidates={candidates} selected={selected} pendingCount={pendingCount} acceptedCount={acceptedCount} onSelect={(id) => { setSelected(id); setStage("review"); }} />
            {active ? <ReviewDetail candidate={active} busy={busy} onDecision={handleDecision} /> : (
              <section className="card detail-panel empty-state">
                <span aria-hidden="true">↖</span>
                <b>Chọn một hồ sơ từ hàng đợi</b>
                <p>Nhấp vào ứng viên bên trái để xem bảng so sánh chi tiết và ra quyết định.</p>
              </section>
            )}
          </div>
          </>
        )}
        {/* STAGE 3: GOLDEN RECORD */}
        {stage === "golden" && (
          <section className="card card-body golden-panel">
            {!active || active.decision !== "accepted" ? (
              <div className="empty-state">
                <span>↗</span>
                <b>Chọn một cặp đã chấp nhận</b>
                <p>Quay lại bước Duyệt hồ sơ để chấp nhận ít nhất một ứng viên trước khi tạo bản ghi chuẩn.</p>
                <button className="button outline btn btn-secondary" onClick={() => setStage("review")}>
                  Mở hàng đợi duyệt
                </button>
              </div>
            ) : (
              <>
                <div className="golden-layout golden-builder">
                  <FieldSourcePicker candidate={active} draft={draft} requiredFields={requiredGoldenFields} onChoose={chooseField} />
                  <GoldenRecordPreview candidate={active} draft={draft} requiredFields={requiredGoldenFields} saved={draftSaved} />
                </div>
                {/* Golden Action Bar */}
                <div className="golden-action-bar golden-foot">
                  <span>◉ Lưu bản nháp sẽ ghi audit. Dữ liệu nguồn vẫn nguyên vẹn.</span>
                  <button
                    className="button primary btn btn-primary"
                    disabled={busy || !draftComplete}
                    onClick={() => void handleSaveDraft()}
                  >
                    {busy ? "Đang lưu…" : "Lưu Golden Record nháp"}
                    <span>→</span>
                  </button>
                </div>
              </>
            )}
          </section>
        )}

        {/* Global Page Footer */}
        <footer className="page-footer page-foot">
          <span>MASTERONE · CUSTOMER DATA OPERATIONS COMMAND CENTER</span>
          <span>Matcher hiện tại chỉ dùng tín hiệu khớp chính xác. Kết quả cần con người duyệt.</span>
        </footer>
      </main>
    </AppShell>
  );
}
