"use client";

import { useEffect, useState } from "react";
import type { AuthUser } from "@/lib/api";
import { runStatusLabel } from "@/lib/sessionView";
import ThemeToggle from "./ThemeToggle";
import { ShellIcon } from "./ShellIcons";
import {useMasterBot} from './MasterBotProvider';
import botStyles from './MasterBot.module.css';
import {userRoleLabel} from '@/lib/uiLabels';
import MoMark from "./MoMark";

export default function ShellTopBar({ currentLabel, sessionLabel, runStatus, user, onLogout, onOpenSessions }: {
  currentLabel: string;
  sessionLabel?: string;
  runStatus?: string;
  user: AuthUser;
  onLogout: () => void;
  onOpenSessions: () => void;
}) {
  const bot=useMasterBot();
  const [accountOpen, setAccountOpen] = useState(false);
  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) { if (event.key === "Escape") setAccountOpen(false); }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, []);

  return <header className="app-header">
    <div className="header-leading">
      <a className="topbar-brand" href="/app" aria-label="MasterOne — về tổng quan"><MoMark size={38} /><span className="topbar-brand-copy"><strong>Master<span>One</span></strong><small>DỮ LIỆU CHỦ</small></span></a>
      <button className="session-context session-chip sessions-open-button" type="button" onClick={onOpenSessions} title={sessionLabel || currentLabel}>
        <span className="session-context-copy"><span className="session-context-name">{sessionLabel || currentLabel}</span>{sessionLabel && <small>{currentLabel}</small>}</span>
        {runStatus && <span className="session-status" data-status={runStatus.toLowerCase()}><i aria-hidden="true" />{runStatusLabel(runStatus)}</span>}
        <ShellIcon name="chevron" size={14} />
      </button>
      <button className={botStyles.startGuide} type="button" aria-label="Chạy hướng dẫn" title="Chạy lại hướng dẫn màn hình này" onClick={()=>window.dispatchEvent(new Event('masterone:start-guide'))}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden="true"><path d="m8 5 11 7-11 7z"/></svg><span>Chạy hướng dẫn</span>
      </button>
    </div>
    <div className="header-actions">
      <button type="button" className="header-session-search" data-demo="session-search" onClick={onOpenSessions} aria-label="Tìm phiên"><ShellIcon name="search" size={18} /><span>Tìm phiên</span></button>
      {bot&&<button type="button" className={botStyles.guideToggle} aria-label="Master Bot giới thiệu" aria-pressed={bot.introEnabled} title={bot.introEnabled?'Tắt Master Bot giới thiệu':'Bật Master Bot giới thiệu'} onClick={bot.toggleIntro}><MoMark size={20}/><span>Giới thiệu</span><i aria-hidden="true"/></button>}
      <ThemeToggle />
      <div className="header-divider" />
      <div className="account-control">
        <button className="account-trigger" type="button" aria-label={`Tài khoản ${user.email}`} aria-expanded={accountOpen} onClick={() => setAccountOpen((open) => !open)}>
          <span className="account-avatar">{user.demo?'DE':user.email.slice(0, 2).toUpperCase()}</span><span className="account-user-copy"><span className="account-email-short">{user.demo?'Khách trải nghiệm':user.email.split("@")[0]}</span><small>{userRoleLabel(user.role)}</small></span>
          <ShellIcon name="chevron" size={14} />
        </button>
        {accountOpen && <div className="account-menu"><span className="account-email">{user.demo?'Khách trải nghiệm':user.email}</span><span className="account-role">{userRoleLabel(user.role)}</span><button type="button" onClick={onLogout}><ShellIcon name="logout" size={16} /> Đăng xuất</button></div>}
      </div>
    </div>
  </header>;
}
