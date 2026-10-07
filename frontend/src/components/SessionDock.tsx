"use client";

import type { Stage } from "./AppShell";
import { ShellIcon, type ShellIconName } from "./ShellIcons";

const stages: { id: Stage; label: string }[] = [
  { id: "overview", label: "Tổng quan" },
  { id: "auto", label: "Tự động gộp" },
  { id: "review", label: "Cần xem xét" },
  { id: "no_merge", label: "Không gộp" },
  { id: "history", label: "Lịch sử xuất" },
];

export default function SessionDock({ stage, canReadAudit, onNavigate }: {
  stage: Stage;
  pendingCount: number;
  mergeCount: number;
  canReadAudit: boolean;
  onNavigate: (stage: Stage) => void;
}) {
  return <nav className="app-sidebar workspace-dock" aria-label="Góc nhìn phiên hiện tại">
    {[...stages, ...(canReadAudit ? [{ id: "audit" as const, label: "Audit" }] : [])].map((item) => <button key={item.id} type="button" className={`sidebar-link dock-link ${stage === item.id ? "active" : ""}`} aria-current={stage === item.id ? "page" : undefined} onClick={() => onNavigate(item.id)}>
      <span className="sidebar-link-icon"><ShellIcon name={item.id as ShellIconName} /></span>
      <span className="sidebar-link-label">{item.label}</span>
    </button>)}
  </nav>;
}
