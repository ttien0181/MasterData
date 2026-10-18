"use client";
import { useRef, useState } from 'react';
import { ApiError } from '@/lib/api';
import {reasonLabel} from '@/lib/pairReview';
import { useWorkflow } from './SessionWorkspace';
import { useSessionCache } from './SessionCache';
export function useWorkflowAction() {
  const cache = useSessionCache();
  const inFlight = useRef(false); const { refresh, workflow } = useWorkflow(); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  async function run<T>(command: () => Promise<T>): Promise<T | undefined> {
    if (inFlight.current) return; inFlight.current = true; setBusy(true); setError('');
    try { const result = await command(); await refresh(); return result; }
    catch (cause) {
      cache.handleError(cause, workflow.run_id);
      if (cause instanceof ApiError && cause.status === 409) {
        await refresh().catch(() => undefined);
        const detail=cause.detail&&typeof cause.detail==='object'?cause.detail as Record<string,unknown>:null;
        setError(detail?.code==='GROUP_APPROVAL_BLOCKED'&&typeof detail.reason==='string'
          ?`Không thể duyệt ngoại lệ nhóm: ${reasonLabel(detail.reason)}. Đã tải lại dữ liệu; lý do chưa lưu vẫn được giữ. Hãy đối chiếu các thành viên trước khi thao tác lại.`
          :'Phiên đã thay đổi hoặc chưa đủ điều kiện. Đã tải lại dữ liệu; lựa chọn chưa lưu vẫn được giữ. Hãy đối chiếu rồi thao tác lại.');
      } else setError(cause instanceof Error ? cause.message : 'Không thực hiện được thao tác.');
      return undefined;
    }
    finally { inFlight.current = false; setBusy(false); }
  }
  return { busy, error, run };
}
