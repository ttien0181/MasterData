"use client";

import type { ReactNode } from "react";
import type { AuthUser } from "@/lib/api";
import type { ResolutionRunHistory } from "@/lib/contracts";
import { can } from "@/lib/permissions";
import ShellTopBar from "./ShellTopBar";
import SessionDock from "./SessionDock";
import SessionStatusBar from "./SessionStatusBar";
import { ShellIcon } from "./ShellIcons";

export type Stage = "overview" | "import" | "auto" | "review" | "no_merge" | "history" | "audit" | "policy";

export default function AppShell({ stage, pendingCount, mergeCount, totalCount, runStatus, sessionLabel, activeRuns,
  activeTotal, activeStale, onOpenRun, user, onNavigate, onLogout, onOpenSessions, uploadPending, children }: {
  stage: Stage;
  pendingCount: number;
  mergeCount: number;
  totalCount: number;
  runStatus?: string;
  sessionLabel?: string;
  activeRuns: ResolutionRunHistory["items"];
  activeTotal: number;
  activeStale: boolean;
  onOpenRun: (runId: string) => void;
  user: AuthUser;
  onNavigate: (stage: Stage) => void;
  onLogout: () => void;
  onOpenSessions: () => void;
  uploadPending?: string;
  children: ReactNode;
}) {
  const legacyShell = process.env.NEXT_PUBLIC_MASTERONE_SHELL === "legacy";

  return <div className={`app-shell ${legacyShell ? "legacy-shell" : ""}`}>
    <div className="shell-main">
      <ShellTopBar currentLabel={{ overview: "Tổng quan", import: "Nạp dữ liệu", auto: "Chờ gộp", review: "Cặp cần xem xét", no_merge: "Không gộp", history: "Lịch sử xuất", audit: "Lịch sử thao tác", policy: "Chính sách" }[stage]}
        sessionLabel={sessionLabel} runStatus={runStatus} user={user} onLogout={onLogout} onOpenSessions={onOpenSessions} />
      <main className="shell-content">{children}</main>
    </div>
    <SessionDock stage={stage} pendingCount={pendingCount} mergeCount={mergeCount}
      canReadAudit={can(user, "read_audit")} canEditPolicy={can(user, "edit_policy")} onNavigate={onNavigate} />
    <div className="workspace-orbit-actions" aria-label="Thao tác đợt xử lý">
      {can(user, "create_run") && <button className="workspace-orbit-button upload-orbit upload-open-button" type="button" aria-label="Nạp dữ liệu" title={uploadPending || "Nạp dữ liệu"} onClick={() => onNavigate("import")}><ShellIcon name="import" size={21} /><span>{uploadPending ? "Tiếp tục" : "Nạp"}</span>{uploadPending && <i className="upload-pending-dot" aria-label={uploadPending} />}</button>}
      <button className="workspace-orbit-button sessions-orbit sessions-open-button" type="button" aria-label="Lịch sử đợt xử lý" title="Lịch sử đợt xử lý" onClick={onOpenSessions}><ShellIcon name="history" size={21} /><span>Đợt xử lý</span></button>
    </div>
    <SessionStatusBar runStatus={runStatus} totalCount={totalCount} canCreate={can(user, "create_run")}
      activeRuns={activeRuns} activeTotal={activeTotal} activeStale={activeStale} onOpenRun={onOpenRun} />
  </div>;
}
