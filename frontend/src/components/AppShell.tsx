"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { AuthUser } from "@/lib/api";

export type Stage = "import" | "review" | "golden";

const stages: { id: Stage; label: string }[] = [
  { id: "import", label: "Nạp dữ liệu" },
  { id: "review", label: "Duyệt hồ sơ" },
  { id: "golden", label: "Golden Record" },
];

export function Icon({ name, size = 18 }: { name: "menu" | "close" | "moon" | "sun" | "database" | "scan" | "layers" | "chevron" | "logout" | "alert"; size?: number }) {
  const paths = {
    menu: <><path d="M4 7h16M4 12h16M4 17h16" /></>,
    close: <><path d="M5 5l14 14M19 5L5 19" /></>,
    moon: <><path d="M20.5 14.1A8.5 8.5 0 0 1 9.9 3.5 8.5 8.5 0 1 0 20.5 14.1Z" /></>,
    sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" /></>,
    database: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3" /></>,
    scan: <><path d="M4 8V5a1 1 0 0 1 1-1h3m8 0h3a1 1 0 0 1 1 1v3M4 16v3a1 1 0 0 0 1 1h3m8 0h3a1 1 0 0 0 1-1v-3" /><path d="M5 12h14" /></>,
    layers: <><path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5" /></>,
    chevron: <><path d="m9 18 6-6-6-6" /></>,
    logout: <><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4m4 4 5 5-5 5m5-5H9" /></>,
    alert: <><path d="m10.3 3.7-8 14A2 2 0 0 0 4 20h16a2 2 0 0 0 1.7-2.3l-8-14a2 2 0 0 0-3.4 0Z" /><path d="M12 9v4m0 4h.01" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

export default function AppShell({ stage, pendingCount, processedCount, totalCount, user, onNavigate, onLogout, children }: {
  stage: Stage;
  pendingCount: number;
  processedCount: number;
  totalCount: number;
  user: AuthUser;
  onNavigate: (stage: Stage) => void;
  onLogout: () => void;
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [dark, setDark] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const mobileToggleRef = useRef<HTMLButtonElement>(null);
  const drawerCloseRef = useRef<HTMLButtonElement>(null);
  const wasDrawerOpen = useRef(false);

  useEffect(() => { setDark(window.localStorage.getItem("masterone.theme") === "dark"); }, []);
  useEffect(() => {
    if (drawerOpen) drawerCloseRef.current?.focus();
    else if (wasDrawerOpen.current) mobileToggleRef.current?.focus();
    wasDrawerOpen.current = drawerOpen;
  }, [drawerOpen]);
  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") { setDrawerOpen(false); setAccountOpen(false); }
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, []);

  function switchTheme() {
    setDark((current) => {
      window.localStorage.setItem("masterone.theme", current ? "light" : "dark");
      return !current;
    });
  }

  function navigate(next: Stage) {
    onNavigate(next);
    setDrawerOpen(false);
  }

  const current = stages.find((item) => item.id === stage) ?? stages[0];
  const progress = totalCount ? Math.round((processedCount / totalCount) * 100) : 0;

  return <div className={`app-shell ${collapsed ? "sidebar-collapsed" : ""} ${drawerOpen ? "drawer-open" : ""}`} data-theme={dark ? "dark" : "light"}>
    {drawerOpen && <button className="drawer-backdrop" type="button" aria-label="Đóng menu" onClick={() => setDrawerOpen(false)} />}
    <aside className="app-sidebar" aria-label="Thanh điều hướng chính" role={drawerOpen ? "dialog" : undefined} aria-modal={drawerOpen ? true : undefined}>
      <div className="sidebar-brand">
        <span className="brand-symbol" aria-hidden="true"><span>M</span></span>
        <span className="brand-wordmark"><span className="brand-title">Master<span>One</span></span><small>DATA OPERATIONS</small></span>
        <button ref={drawerCloseRef} className="sidebar-mobile-close icon-button" type="button" aria-label="Đóng menu" onClick={() => setDrawerOpen(false)}><Icon name="close" /></button>
      </div>
      <div className="sidebar-group-label">QUY TRÌNH</div>
      <nav className="sidebar-nav" aria-label="Các bước xử lý">
        {stages.map((item) => <button key={item.id} type="button" className={`sidebar-link ${stage === item.id ? "active" : ""}`} aria-current={stage === item.id ? "page" : undefined} title={item.label} onClick={() => navigate(item.id)}>
          <span className="sidebar-link-icon"><Icon name={item.id === "import" ? "database" : item.id === "review" ? "scan" : "layers"} /></span>
          <span className="sidebar-link-label">{item.label}</span>
          {item.id === "review" && pendingCount > 0 && <span className="sidebar-count">{pendingCount}</span>}
        </button>)}
      </nav>
      <div className="sidebar-spacer" />
      <div className="sidebar-session">
        <span className="session-overline">PHIÊN LÀM VIỆC</span>
        <strong>{totalCount ? `${processedCount}/${totalCount} cặp đã xử lý` : "Chưa có dữ liệu"}</strong>
        <div className="session-progress" role="progressbar" aria-valuenow={processedCount} aria-valuemin={0} aria-valuemax={totalCount || 1} aria-label="Tiến độ duyệt hồ sơ"><span style={{ width: `${progress}%` }} /></div>
        <small>{totalCount ? `${pendingCount} cặp đang chờ duyệt` : "Nạp CRM và ERP để bắt đầu"}</small>
      </div>
    </aside>

    <div className="shell-main" inert={drawerOpen}>
      <header className="app-header">
        <div className="header-leading">
          <button className="icon-button sidebar-toggle desktop-toggle" type="button" aria-label={collapsed ? "Mở rộng thanh bên" : "Thu gọn thanh bên"} aria-expanded={!collapsed} onClick={() => setCollapsed((value) => !value)}><Icon name="menu" /></button>
          <button ref={mobileToggleRef} className="icon-button sidebar-toggle mobile-toggle" type="button" aria-label={drawerOpen ? "Đóng menu" : "Mở menu"} aria-expanded={drawerOpen} onClick={() => setDrawerOpen((open) => !open)}><Icon name="menu" /></button>
          <div className="header-divider" />
          <div className="header-location"><span>Master data</span><Icon name="chevron" size={14} /><span>Khách hàng</span><Icon name="chevron" size={14} /><strong>{current.label}</strong></div>
        </div>
        <div className="header-actions">
          <span className="prototype-badge"><span className="prototype-dot" /> Bản thử nghiệm</span>
          <button className="icon-button theme-toggle" type="button" aria-label={dark ? "Bật giao diện sáng" : "Bật giao diện tối"} aria-pressed={dark} onClick={switchTheme}><Icon name={dark ? "sun" : "moon"} /></button>
          <div className="header-divider" />
          <div className="account-control">
            <button className="account-trigger" type="button" aria-label={`Tài khoản ${user.email}`} aria-expanded={accountOpen} onClick={() => setAccountOpen((open) => !open)}>
              <span className="account-avatar">{user.email.slice(0, 2).toUpperCase()}</span>
              <Icon name="chevron" size={14} />
            </button>
            {accountOpen && <div className="account-menu"><span className="account-email">{user.email}</span><button type="button" onClick={onLogout}><Icon name="logout" size={16} /> Đăng xuất</button></div>}
          </div>
        </div>
      </header>
      <div className="shell-content">{children}</div>
    </div>
  </div>;
}
