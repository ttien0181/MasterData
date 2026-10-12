"use client";
import { useRef, useState } from 'react';
import { ApiError } from '@/lib/api';
import { useWorkflow } from './SessionWorkspace';
import { useSessionCache } from './SessionCache';
export function useWorkflowAction() {
  const cache = useSessionCache();
  const inFlight = useRef(false); const { refresh, workflow } = useWorkflow(); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  async function run<T>(command: () => Promise<T>): Promise<T | undefined> {
    if (inFlight.current) return; inFlight.current = true; setBusy(true); setError('');
    try { const result = await command(); await refresh(); return result; }
    catch (cause) { cache.handleError(cause, workflow.run_id); if (cause instanceof ApiError && cause.status === 409) { await refresh().catch(() => undefined); setError('Phiên đã thay đổi hoặc chưa đủ điều kiện. Đã đọc lại server; lựa chọn chưa lưu vẫn được giữ. Hãy đối chiếu rồi thao tác lại.'); } else setError(cause instanceof Error ? cause.message : 'Không thực hiện được thao tác.'); return undefined; }
    finally { inFlight.current = false; setBusy(false); }
  }
  return { busy, error, run };
}
