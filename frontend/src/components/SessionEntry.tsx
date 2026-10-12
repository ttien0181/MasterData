'use client';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { getWorkflow } from '@/lib/workflowApi';
export default function SessionEntry({ runId, view }: { runId: string; view?: string }) {
  const router = useRouter(); const [error, setError] = useState(''); const [attempt, setAttempt] = useState(0);
  useEffect(() => { const c = new AbortController(); void getWorkflow(runId, c.signal).then(w => { if (c.signal.aborted) return;
    const query = new URLSearchParams({ run: runId }); if (view) query.set('view', view);
    router.replace(w.workflow_version === 1 ? `/app/legacy?${query}` : `/app/sessions/${encodeURIComponent(runId)}/data`);
  }).catch(e => { if (!c.signal.aborted) setError(e.message); }); return () => c.abort(); }, [runId, view, router, attempt]);
  return <main className="workflow-main"><p role={error ? 'alert' : 'status'}>{error || 'Đang mở phiên từ đường dẫn…'}</p>{error && <button onClick={() => setAttempt(n => n + 1)}>Thử lại</button>}</main>;
}
