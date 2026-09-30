"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import type { AuthUser } from "@/lib/api";
import ThemeToggle from "./ThemeToggle";

export type Stage = "overview" | "import" | "auto" | "rejected" | "review" | "history";

const stages: { id: Stage; label: string }[] = [
  { id: "overview", label: "Tổng quan" },
  { id: "import", label: "Nạp dữ liệu" },
  { id: "rejected", label: "Không gộp" },
  { id: "review", label: "Cần review" },
  { id: "auto", label: "Gộp" },
  { id: "history", label: "Lịch sử" },
];

export function Icon({ name, size = 18 }: { name: "menu" | "close" | "overview" | "import" | "rejected" | "review" | "auto" | "history" | "chevron" | "logout"; size?: number }) {
  const paths = {
    menu: <><path d="M4 7h16M4 12h16M4 17h16" /></>,
    close: <><path d="M5 5l14 14M19 5L5 19" /></>,
    overview: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    import: <><path d="M12 3v12m-4-4 4 4 4-4M4 17v3h16v-3" /></>,
    rejected: <><circle cx="8" cy="12" r="4" /><circle cx="16" cy="12" r="4" /><path d="m4 20 16-16" /></>,
    review: <><path d="M4 8V5a1 1 0 0 1 1-1h3m8 0h3a1 1 0 0 1 1 1v3M4 16v3a1 1 0 0 0 1 1h3m8 0h3a1 1 0 0 0 1-1v-3" /><circle cx="12" cy="12" r="3" /></>,
    auto: <><path d="M3 6h7l4 6 4-6h3M3 18h7l4-6 4 6h3" /><path d="m18 3 3 3-3 3m0 6 3 3-3 3" /></>,
    history: <><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5m4-1v5l3 2" /></>,
    chevron: <><path d="m9 18 6-6-6-6" /></>,
    logout: <><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4m4 4 5 5-5 5m5-5H9" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

export default function AppShell({ stage, pendingCount, mergeCount, rejectedCount, processedCount, totalCount, user, onNavigate, onLogout, children }: {
  stage: Stage;
  pendingCount: number;
  mergeCount: number;
  rejectedCount: number;
  processedCount: number;
  totalCount: number;
  user: AuthUser;
  onNavigate: (stage: Stage) => void;
  onLogout: () => void;
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const mobileToggleRef = useRef<HTMLButtonElement>(null);
  const drawerCloseRef = useRef<HTMLButtonElement>(null);
  const wasDrawerOpen = useRef(false);

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

  function navigate(next: Stage) {
    onNavigate(next);
    setDrawerOpen(false);
  }

  const current = stages.find((item) => item.id === stage) ?? stages[0];
  const progress = totalCount ? Math.round((processedCount / totalCount) * 100) : 0;

  return <div className={`app-shell ${collapsed ? "sidebar-collapsed" : ""} ${drawerOpen ? "drawer-open" : ""}`}>
    {drawerOpen && <button className="drawer-backdrop" type="button" aria-label="Đóng menu" onClick={() => setDrawerOpen(false)} />}
    <aside className="app-sidebar" aria-label="Thanh điều hướng chính" role={drawerOpen ? "dialog" : undefined} aria-modal={drawerOpen ? true : undefined}>
      <div className="sidebar-brand">
        <span className="brand-symbol" aria-hidden="true"><span>M</span></span>
        <span className="brand-wordmark"><span className="brand-title">Master<span>One</span></span><small>DATA OPERATIONS</small></span>
        <button ref={drawerCloseRef} className="sidebar-mobile-close icon-button" type="button" aria-label="Đóng menu" onClick={() => setDrawerOpen(false)}><Icon name="close" /></button>
      </div>
      <div className="sidebar-profile"><span className="sidebar-profile-avatar">{user.email.slice(0, 2).toUpperCase()}</span><span className="sidebar-profile-copy"><strong>{user.email.split("@")[0]}</strong><small>{user.email}</small></span></div>
      <div className="sidebar-mini-stats" aria-label="Thống kê lần nạp hiện tại">
        <span><b>{mergeCount.toLocaleString("vi-VN")}</b><small>Gộp</small></span>
        <span><b>{rejectedCount.toLocaleString("vi-VN")}</b><small>Không gộp</small></span>
        <span><b>{pendingCount.toLocaleString("vi-VN")}</b><small>Cần review</small></span>
      </div>
      <div className="sidebar-group-label">QUY TRÌNH</div>
      <nav className="sidebar-nav" aria-label="Các bước xử lý">
        {stages.map((item) => <button key={item.id} type="button" className={`sidebar-link ${stage === item.id ? "active" : ""}`} aria-current={stage === item.id ? "page" : undefined} title={item.label} onClick={() => navigate(item.id)}>
          <span className="sidebar-link-icon"><Icon name={item.id} /></span>
          <span className="sidebar-link-label">{item.label}</span>
          {item.id === "review" && pendingCount > 0 && <span className="sidebar-count">{pendingCount}</span>}
        </button>)}
      </nav>
      <div className="sidebar-spacer" />
      <div className="sidebar-session">
        <span className="session-overline">Tiến độ pipeline</span>
        <strong>{totalCount ? `${progress}% bản ghi đã xử lý` : "Chưa có lần nạp dữ liệu"}</strong>
        <div className="session-progress" role="progressbar" aria-valuenow={processedCount} aria-valuemin={0} aria-valuemax={totalCount || 1} aria-label="Tiến độ xử lý bản ghi"><span style={{ width: `${progress}%` }} /></div>
        <small>{totalCount ? `${processedCount.toLocaleString("vi-VN")} / ${totalCount.toLocaleString("vi-VN")} bản ghi` : "Nạp CSV để bắt đầu"}</small>
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
          <ThemeToggle />
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
