'use client';
import { useCallback, useState } from 'react';
import { request } from '@/lib/api';
import { ReadNotice, useWorkflowRead } from './WorkflowReads';

type Profile = { id: string; version: number; digest: string; lifecycle: string; score_unit: string;
  config: { thresholds?: { mode?: string }; encoder_identity?: { model_id?: string } } };
type Binding = { profile_id: string; profile_digest: string; revision: number };
type Feedback = { label: 'MATCH' | 'NON_MATCH' | null; supersedes_feedback_id: string | null; id: string };
type Evaluation = { id: string; status: string; evaluated_scope: { diagnostic_only?: boolean }; label_support: Record<string, number> };

export default function MatchingProfilePanel({ runId, canLearn = false, canApply = false }: { runId: string; canLearn?: boolean; canApply?: boolean }) {
  const [selected, setSelected] = useState(''); const [busy, setBusy] = useState(false); const [notice, setNotice] = useState('');
  const load = useCallback(async (signal: AbortSignal) => {
    const [profiles, binding, feedback, reports, defaultPointer] = await Promise.allSettled([
      request<Profile[]>('/matching/profiles', { signal }),
      request<Binding | null>(`/matching/runs/${runId}/binding`, { signal }),
      request<Feedback[]>(`/matching/runs/${runId}/feedback`, { signal }),
      request<Evaluation[]>(`/matching/runs/${runId}/evaluations`, { signal }),
      request<{ profile_id: string | null; revision: number }>('/matching/defaults/owner-private', { signal }),
    ]);
    return { profiles, binding, feedback, reports, defaultPointer };
  }, [runId]);
  const read = useWorkflowRead(load);
  if (!read.data) return <section><h3>Matching profile và đánh giá</h3><ReadNotice error={read.error} retry={read.retry} /></section>;
  const { profiles, binding, feedback, reports, defaultPointer } = read.data;
  const pinned = binding.status === 'fulfilled' ? binding.value : null;
  const profile = profiles.status === 'fulfilled' ? profiles.value.find(p => p.id === pinned?.profile_id) : null;
  const events = feedback.status === 'fulfilled' ? feedback.value : [];
  const superseded = new Set(events.map(e => e.supersedes_feedback_id).filter(Boolean));
  const current = events.filter(e => !superseded.has(e.id));
  async function mutate(path: string, method: string, body: unknown) {
    setBusy(true); setNotice('');
    try {
      const value = await request<{ status?: string; reasons?: string[] }>(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      setNotice(value.status === 'INSUFFICIENT_LABELS' ? 'Chưa đủ nhãn có provenance và TRAIN/DEV độc lập đã xác minh; không học hoặc đổi ngưỡng.'
        : value.status === 'INVALID_SPLIT' ? 'Split dữ liệu chưa hợp lệ hoặc chưa được xác minh; không học hoặc đổi ngưỡng.'
        : value.status || 'Đã đổi default cho run mới; run hiện tại giữ profile đã pin.');
      read.retry();
    } catch (cause) { setNotice(cause instanceof Error ? cause.message : 'Không thực hiện được yêu cầu'); }
    finally { setBusy(false); }
  }
  return <section aria-label="Matching profile và đánh giá"><h3>Matching profile và đánh giá</h3>
    {profile ? <><p>Profile v{profile.version} · {profile.lifecycle} · binding revision {pinned?.revision}</p>
      <p>Score unit: {profile.score_unit}</p><p>Threshold mode: {profile.config.thresholds?.mode || 'Theo cấu hình profile đã pin'}</p>
      <p>Digest: <code>{profile.digest}</code></p></> : <p>Run chưa có profile binding hoặc registry chưa được bật. Matching hiện tại vẫn theo cấu hình đã pin của run.</p>}
    {feedback.status === 'fulfilled' ? <p>Phản hồi trực tiếp đã lưu: MATCH {current.filter(e => e.label === 'MATCH').length}; NON_MATCH {current.filter(e => e.label === 'NON_MATCH').length}. Reject/Defer không là nhãn khác người; điều kiện học kiểm tra riêng revision và provenance.</p>
      : <p>Feedback chưa khả dụng trong phạm vi quyền hoặc feature flag hiện tại.</p>}
    {reports.status === 'fulfilled' && reports.value.length ? reports.value.map(r => <p key={r.id}>Report {r.id}: {r.status}{r.evaluated_scope.diagnostic_only ? ' · chỉ diagnostic' : ''}</p>)
      : <p>Chưa có report đánh giá được backend cung cấp cho run. Không suy precision/recall từ cosine hoặc rule score.</p>}
    <p>Học ngưỡng cần nhãn và TRAIN/DEV độc lập đã xác minh. Không tự đổi profile của run đang chạy.</p>
    {canLearn && feedback.status === 'fulfilled' && <button disabled={busy} onClick={() => void mutate(`/matching/runs/${runId}/learn-thresholds`, 'POST', {})}>Kiểm tra điều kiện học ngưỡng</button>}
    {canApply && profiles.status === 'fulfilled' && defaultPointer.status === 'fulfilled' && <div>
      <p>Default cho run mới: {defaultPointer.value.profile_id || 'Chưa có'}. Chọn profile hợp lệ trước đó để rollback.</p>
      <label>Profile cho run mới <select value={selected} disabled={busy} onChange={e => setSelected(e.target.value)}><option value="">Chọn profile đã validate</option>
        {profiles.value.filter(p => ['ACTIVE', 'VALIDATED'].includes(p.lifecycle)).map(p => <option key={p.id} value={p.id}>v{p.version} · {p.lifecycle}</option>)}</select></label>
      <button disabled={busy || !selected} onClick={() => void mutate('/matching/defaults/owner-private', 'PUT', { profile_id: selected, expected_revision: defaultPointer.value.revision })}>Apply / rollback default</button>
    </div>}
    {notice && <p role="status">{notice}</p>}
    <button onClick={read.retry}>Đọc lại profile và report</button>
  </section>;
}
