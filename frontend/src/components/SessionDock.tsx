"use client";

import type { Stage } from "./AppShell";
import { ShellIcon, type ShellIconName } from "./ShellIcons";

const decisionStages: { id: Stage; label: string }[] = [
  { id: "no_merge", label: "Không gộp" },
  { id: "review", label: "Cần xem xét" },
  { id: "auto", label: "Chờ gộp" },
];

export default function SessionDock({ stage, canReadAudit, canEditPolicy, onNavigate }: {
  stage: Stage;
  pendingCount: number;
  mergeCount: number;
  canReadAudit: boolean;
  canEditPolicy?: boolean;
  onNavigate: (stage: Stage) => void;
}) {
  const extraStages = [
    ...(canReadAudit ? [{ id: "audit" as const, label: "Lịch sử thao tác" }] : []),
    ...(canEditPolicy ? [{ id: "policy" as const, label: "Chính sách" }] : []),
  ];
  return <nav className="app-sidebar workspace-dock" aria-label="Các bước và màn hình của đợt xử lý">
    <button type="button" className={`dock-step ${stage === "overview" ? "active" : ""}`} aria-current={stage === "overview" ? "page" : undefined} onClick={() => onNavigate("overview")}>
      <span className="dock-step-marker" aria-hidden="true">1</span><span>Tổng quan</span>
    </button>
    <span className="dock-step-connector" aria-hidden="true" />
    <div className={`dock-step-group ${decisionStages.some((item) => item.id === stage) ? "is-current" : ""}`} role="group" aria-label="Bước 2: Xử lý đề xuất">
      <span className="dock-step-group-title"><span className="dock-step-group-number">2</span> Xử lý đề xuất</span>
      <div className="dock-step-options">
        {decisionStages.map((item) => <button key={item.id} type="button" className={`dock-step-option dock-step-option-${item.id} ${stage === item.id ? "active" : ""}`} aria-current={stage === item.id ? "page" : undefined} onClick={() => onNavigate(item.id)}>
          <ShellIcon name={item.id as ShellIconName} size={15} /><span>{item.label}</span>
        </button>)}
      </div>
    </div>
    <span className="dock-step-connector" aria-hidden="true" />
    <button type="button" className={`dock-step ${stage === "history" ? "active" : ""}`} aria-current={stage === "history" ? "page" : undefined} onClick={() => onNavigate("history")}>
      <span className="dock-step-marker" aria-hidden="true">3</span><span>Lịch sử xuất</span>
    </button>
    {extraStages.length > 0 && <div className="dock-step-extra">
      {extraStages.map((item) => <button key={item.id} type="button" className={`dock-extra-link ${stage === item.id ? "active" : ""}`} aria-current={stage === item.id ? "page" : undefined} onClick={() => onNavigate(item.id)}>
        <ShellIcon name={item.id as ShellIconName} size={15} /><span>{item.label}</span>
      </button>)}
    </div>}
  </nav>;
}
