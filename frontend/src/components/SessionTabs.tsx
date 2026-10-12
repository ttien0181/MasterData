"use client";
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { sessionPath, sessionTabs, tabReadiness } from '@/lib/workflowNavigation';
export default function SessionTabs({ runId, state, clusterRevision = 0 }: { runId: string; state: string; clusterRevision?: number }) {
  const pathname = usePathname();
  return <nav className="workflow-tabs" aria-label="Các bước trong phiên">{sessionTabs.map(tab => { const gate = tabReadiness(state, tab.id, clusterRevision); const href = sessionPath(runId, tab.id);
    return <Link scroll={false} prefetch={false} key={tab.id} href={href} aria-current={pathname === href ? 'page' : undefined} title={gate.reason || tab.label} className={gate.ready ? '' : 'workflow-tab-pending'}>{tab.label}{!gate.ready && <span className="workflow-tab-hint">Chưa sẵn sàng</span>}</Link>;
  })}</nav>;
}
