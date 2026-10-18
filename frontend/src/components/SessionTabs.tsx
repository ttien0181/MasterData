"use client";

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { sessionPath, sessionSections, sessionTabs, tabReadiness, type SessionTab } from '@/lib/workflowNavigation';
import { useStoredState } from './SessionCache';
import { ShellIcon, type ShellIconName } from './ShellIcons';
import styles from './SessionSidebar.module.css';

const icons: Record<SessionTab, ShellIconName> = { data: 'overview', decisions: 'review', clusters: 'auto', golden: 'file' };

export default function SessionTabs({ runId, state, clusterRevision = 0, collapsed = false, onToggle }: {
  runId: string; state: string; clusterRevision?: number; collapsed?: boolean; onToggle?: () => void;
}) {
  const pathname = usePathname();
  const activeTab = sessionTabs.find(tab => pathname === sessionPath(runId, tab.id) || pathname.startsWith(`${sessionPath(runId, tab.id)}/`));
  const [expanded, setExpanded] = useStoredState<Record<string, boolean>>(runId, 'sidebar:expanded', {});
  const [mobileOpen, setMobileOpen] = useState(false);
  const [activeSection, setActiveSection] = useState('');
  const [available, setAvailable] = useState<string[]>([]);
  const root = useRef<HTMLElement>(null);
  const mobileButton = useRef<HTMLButtonElement>(null);
  const bodyId = `session-navigation-${runId}`;

  useEffect(() => {
    if (activeTab && expanded[activeTab.id] === undefined) {
      setExpanded(previous => ({ ...previous, [activeTab.id]: true }));
    }
  }, [activeTab?.id, expanded, setExpanded]);

  useEffect(() => {
    const content = document.querySelector('.workflow-session-content');
    if (!content || !activeTab) return;
    let frame = 0, reachedHash = '';
    const sections = sessionSections[activeTab.id];
    function update() {
      const found = sections.filter(section => document.getElementById(section.id));
      const ids = found.map(section => section.id);
      setAvailable(previous => previous.join() === ids.join() ? previous : ids);
      const hash = window.location.hash.slice(1);
      if (hash && reachedHash !== hash && ids.includes(hash)) {
        reachedHash = hash;
        document.getElementById(hash)?.scrollIntoView({ block: 'start', behavior: 'instant' });
      }
      const offset = window.innerWidth <= 1000 ? 150 : 110;
      const positions = found.map(section => ({ id: section.id, top: document.getElementById(section.id)!.getBoundingClientRect().top }))
        .sort((left, right) => left.top - right.top);
      const atBottom = window.scrollY > 0 && window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2;
      const current = atBottom ? positions.at(-1) : positions.filter(section => section.top <= offset).at(-1);
      setActiveSection(current?.id || positions[0]?.id || '');
    }
    function schedule() { cancelAnimationFrame(frame); frame = requestAnimationFrame(update); }
    function hashChange() { reachedHash = ''; schedule(); }
    const observer = new MutationObserver(schedule);
    observer.observe(content, { childList: true, subtree: true, attributes: true, attributeFilter: ['id'] });
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    window.addEventListener('hashchange', hashChange);
    schedule();
    return () => {
      observer.disconnect(); cancelAnimationFrame(frame);
      window.removeEventListener('scroll', schedule); window.removeEventListener('resize', schedule);
      window.removeEventListener('hashchange', hashChange);
    };
  }, [pathname, activeTab?.id]);

  useEffect(() => {
    if (!mobileOpen) return;
    function keydown(event: KeyboardEvent) {
      if (event.key === 'Escape') { setMobileOpen(false); mobileButton.current?.focus(); }
    }
    function outside(event: PointerEvent) {
      if (root.current && !root.current.contains(event.target as Node)) setMobileOpen(false);
    }
    document.addEventListener('keydown', keydown); document.addEventListener('pointerdown', outside);
    return () => { document.removeEventListener('keydown', keydown); document.removeEventListener('pointerdown', outside); };
  }, [mobileOpen]);

  return <aside ref={root} className={`${styles.sidebar} ${collapsed ? styles.collapsed : ''}`} data-mobile-open={mobileOpen} aria-label="Điều hướng phiên">
    <button ref={mobileButton} type="button" className={styles.mobileToggle} aria-label={`ĐIỀU HƯỚNG PHIÊN: ${activeTab?.label || 'Tiện ích phiên'}`} aria-expanded={mobileOpen} aria-controls={bodyId} onClick={() => setMobileOpen(value => !value)}>
      <span className={styles.mobileIcon}><ShellIcon name={activeTab ? icons[activeTab.id] : 'menu'} size={19} /></span>
      <span><strong>{activeTab?.label || 'Tiện ích phiên'}</strong></span>
      <ShellIcon name={mobileOpen ? 'close' : 'menu'} size={19} />
    </button>
    <div id={bodyId} className={styles.body}>
      <header className={styles.header}>
        <div className={styles.heading}><strong>Phiên xử lý dữ liệu</strong></div>
        <button type="button" className={styles.collapseToggle} onClick={onToggle} aria-label={collapsed ? 'Mở rộng thanh điều hướng' : 'Thu gọn thanh điều hướng'} aria-expanded={!collapsed} title={collapsed ? 'Mở rộng thanh điều hướng' : 'Thu gọn thanh điều hướng'}>
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3" /><path d="M9 4v16" /><path d={collapsed ? 'm13 9 3 3-3 3' : 'm16 9-3 3 3 3'} /></svg>
        </button>
      </header>
      <nav aria-label="Các bước trong phiên" className={styles.navigation}>
        <ol className={styles.steps}>{sessionTabs.map((tab, index) => {
          const gate = tabReadiness(state, tab.id, clusterRevision);
          const navigable = gate.ready || tab.id === 'decisions' && ['DATA_READY', 'DECIDE_RUNNING'].includes(state);
      const active = activeTab?.id === tab.id;
          const open = expanded[tab.id] ?? active;
          const sections = sessionSections[tab.id].filter(section => section.path !== undefined || !active || available.includes(section.id));
          const childrenId = `${bodyId}-${tab.id}`;
          return <li key={tab.id} className={`${styles.step} ${active ? styles.active : ''} ${!gate.ready ? styles.pending : ''}`}>
            <div className={styles.stepRow}>
              <Link data-demo={`step-${tab.id}`} scroll={false} prefetch={false} href={sessionPath(runId, tab.id)} className={styles.pageLink} aria-current={active ? 'page' : undefined} title={`${tab.label}${gate.reason ? ' — ' + gate.reason : ''}`} onClick={() => { setMobileOpen(false); setExpanded(previous => ({ ...previous, [tab.id]: true })); }}>
                <span className={styles.stepIcon}><ShellIcon name={icons[tab.id]} size={20} />{!gate.ready && <i className={styles.pendingDot} />}</span>
                <span className={styles.stepCopy}><span className={styles.stepNumber}>BƯỚC {String(index + 1).padStart(2, '0')}</span><strong>{tab.label}</strong>{!gate.ready && <small>Chưa sẵn sàng</small>}</span>
              </Link>
              {navigable && sections.length > 0 && <button type="button" className={styles.groupToggle} aria-expanded={open} aria-controls={childrenId} aria-label={`${open ? 'Thu gọn' : 'Mở'} mục con ${tab.label}`} onClick={() => setExpanded(previous => ({ ...previous, [tab.id]: !open }))}><ShellIcon name="chevron" size={15} /></button>}
            </div>
            {navigable && sections.length > 0 && <ul id={childrenId} className={styles.children} hidden={!open}>{sections.map(section => <li key={section.id}>
              <Link scroll={section.path !== undefined ? false : undefined} prefetch={false} href={`${sessionPath(runId, tab.id)}${section.path ?? `#${section.id}`}`} aria-current={section.path !== undefined ? pathname === `${sessionPath(runId, tab.id)}${section.path}` ? 'page' : undefined : active && activeSection === section.id ? 'location' : undefined} onClick={() => setMobileOpen(false)}>{section.label}</Link>
            </li>)}</ul>}
          </li>;
        })}</ol>
      </nav>
      <footer className={styles.footer}>
        <Link href={`/app/sessions/${encodeURIComponent(runId)}/utilities`} prefetch={false} aria-current={pathname.endsWith('/utilities') ? 'page' : undefined} title="Lịch sử xuất, Lịch sử thao tác và Chính sách" onClick={() => setMobileOpen(false)}><ShellIcon name="history" size={18} /><span>Lịch sử và tiện ích</span></Link>
        <Link href="/app" title="Tất cả phiên" onClick={() => setMobileOpen(false)}><ShellIcon name="file" size={18} /><span>Tất cả phiên</span></Link>
      </footer>
    </div>
  </aside>;
}
