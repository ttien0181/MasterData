"use client";

import { useEffect, useState } from "react";
import type { AuthUser } from "@/lib/api";
import { runStatusLabel } from "@/lib/sessionView";
import ThemeToggle from "./ThemeToggle";
import { ShellIcon } from "./ShellIcons";
import MoMark from "./MoMark";

const roleLabels: Record<string, string> = {
  VIEWER: "Người xem", USER: "Người dùng", STEWARD: "Người duyệt dữ liệu",
  POLICY_ADMIN: "Quản trị chính sách", AUDITOR: "Kiểm toán viên",
};

export default function ShellTopBar({ currentLabel, sessionLabel, runStatus, user, onLogout, onOpenSessions }: {
  currentLabel: string;
  sessionLabel?: string;
  runStatus?: string;
  user: AuthUser;
  onLogout: () => void;
  onOpenSessions: () => void;
}) {
  const [accountOpen, setAccountOpen] = useState(false);
  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) { if (event.key === "Escape") setAccountOpen(false); }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, []);

  return <header className="app-header">
    <div className="header-leading">
      <a className="topbar-brand" href="/app" aria-label="MasterOne, về trang làm việc"><MoMark size={38} /><span className="topbar-brand-copy"><strong>Master<span>One</span></strong><small>DỮ LIỆU CHỦ</small></span></a>
      <button className="session-context session-chip sessions-open-button" type="button" onClick={onOpenSessions} title={sessionLabel || currentLabel}>
        <span className="session-context-copy"><span className="session-context-name">{sessionLabel || currentLabel}</span>{sessionLabel && <small>{currentLabel}</small>}</span>
        {runStatus && <span className="session-status" data-status={runStatus.toLowerCase()}><i aria-hidden="true" />{runStatusLabel(runStatus)}</span>}
        <ShellIcon name="chevron" size={14} />
      </button>
    </div>
    <div className="header-actions">
      <button type="button" className="header-session-search" onClick={onOpenSessions} aria-label="Tìm đợt xử lý"><ShellIcon name="search" size={18} /><span>Tìm đợt xử lý</span></button>
      <ThemeToggle />
      <div className="header-divider" />
      <div className="account-control">
        <button className="account-trigger" type="button" aria-label={`Tài khoản ${user.email}`} aria-expanded={accountOpen} onClick={() => setAccountOpen((open) => !open)}>
          <span className="account-avatar">{user.email.slice(0, 2).toUpperCase()}</span><span className="account-user-copy"><span className="account-email-short">{user.email.split("@")[0]}</span><small>{roleLabels[user.role] || "Người dùng"}</small></span>
          <ShellIcon name="chevron" size={14} />
        </button>
        {accountOpen && <div className="account-menu"><span className="account-email">{user.email}</span><span className="account-role">{roleLabels[user.role] || "Người dùng"}</span><button type="button" onClick={onLogout}><ShellIcon name="logout" size={16} /> Đăng xuất</button></div>}
      </div>
    </div>
  </header>;
}
