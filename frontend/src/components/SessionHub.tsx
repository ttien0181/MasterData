"use client";
// IamNTN hub composition; current server history and ownership contracts retained.
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { bulkResolutionRuns, getResolutionRuns, renameResolutionRun, type RunListOptions } from '@/lib/api';
import type { ResolutionRunHistory } from '@/lib/contracts';
import { can } from '@/lib/permissions';
import { getWorkflow } from '@/lib/workflowApi';
import { sessionPath } from '@/lib/workflowNavigation';
import { useAppUser } from './AppSession';
import SessionsHub from './SessionsHub';
import styles from './SessionHub.module.css';
import SampleLibrary from './SampleLibrary';
import demoStyles from './DemoExperience.module.css';
export default function SessionHub() {
  const user = useAppUser(); const router = useRouter();
  const [history, setHistory] = useState<ResolutionRunHistory | null>(null);
  const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const [filters, setFilters] = useState<RunListOptions>({}); const [cursor, setCursor] = useState<string | null>(null); const [reload, setReload] = useState(0);
  const page = useCallback((next: string | null, options: RunListOptions = {}) => { setFilters(options); setCursor(next); setReload(n => n + 1); }, []);
  useEffect(() => { let active = true; setLoading(true);
    void getResolutionRuns(25, 0, { ...filters, cursor }).then(found => { if (active) { setHistory(found); setError(''); } }).catch(cause => { if (active) setError(cause.message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [filters, cursor, reload]);
  async function open(run: string) { try { const w = await getWorkflow(run); router.push(w.workflow_version === 1 ? `/app/legacy?run=${encodeURIComponent(run)}` : sessionPath(run)); } catch (cause) { setError(cause instanceof Error ? cause.message : 'Không mở được phiên.'); } }
  return <main className={`workflow-main ${styles.page}`}><header className={styles.heading}><div><h1>Tổng quan</h1><p>Tìm và quản lý các phiên xử lý dữ liệu khách hàng.</p></div><div className={demoStyles.hubActions}><SampleLibrary/>{can(user, 'create_run') && <Link data-demo="new-upload" className={styles.create} href="/app/uploads/new">Tải tệp mới</Link>}</div></header>
    <SessionsHub embedded items={history?.items || []} total={history?.total || 0} nextCursor={history?.next_cursor || null} loading={loading} error={error} canManage={can(user, 'manage_run')}
      onPage={page} onRetry={options => page(null, options)} onOpen={run => void open(run)} onClose={() => undefined}
      onRename={async (run, name) => { await renameResolutionRun(run, name); setReload(n => n + 1); }} onBulk={async (operation, runs) => { await bulkResolutionRuns(operation, runs); setReload(n => n + 1); }} />
  </main>;
}
