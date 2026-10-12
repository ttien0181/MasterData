'use client';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAppUser } from './AppSession';
import ResolutionWorkspace from './ResolutionWorkspace';
import { logout, setCsrfToken } from '@/lib/api';
import { getWorkflow } from '@/lib/workflowApi';
import { clearActiveImport, setActiveImportUser } from '@/lib/activeImport';
export default function LegacyWorkspace() {
  const user = useAppUser(); const router = useRouter(); const [ready, setReady] = useState(false); const [error, setError] = useState(''); const [attempt, setAttempt] = useState(0);
  useEffect(() => { const controller = new AbortController(); const run = new URLSearchParams(window.location.search).get('run');
    if (!run) { setReady(true); return; }
    void getWorkflow(run, controller.signal).then(w => { if (controller.signal.aborted) return; if (w.workflow_version === 2) router.replace(`/app/sessions/${encodeURIComponent(run)}/data`); else if (w.workflow_version === 1) setReady(true); else setError('Không nhận diện được phiên lịch sử. Hãy đọc lại.'); }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [router, attempt]);
  if (!ready) return <main className="workflow-main"><p role={error ? 'alert' : 'status'}>{error || 'Đang đọc phiên lịch sử…'}</p>{error && <button onClick={() => { setError(''); setAttempt(n => n + 1); }}>Thử lại</button>}</main>;
  return <ResolutionWorkspace user={user} onLogout={async () => { await logout(); clearActiveImport(); setActiveImportUser(''); setCsrfToken(''); router.replace('/login'); }} />;
}
