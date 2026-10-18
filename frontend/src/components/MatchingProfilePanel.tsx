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
  if (!read.data) return <section><h3>Thiết lập đối chiếu và đánh giá</h3><ReadNotice error={read.error} retry={read.retry} /></section>;
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
      setNotice(value.status === 'INSUFFICIENT_LABELS' ? 'Chưa có đủ kết quả đối chiếu đã xác minh để điều chỉnh quy tắc. Cách xử lý hiện tại được giữ nguyên.'
        : value.status === 'INVALID_SPLIT' ? 'Dữ liệu đánh giá chưa được kiểm tra đầy đủ. Chưa thể điều chỉnh quy tắc.'
        : 'Đã đổi thiết lập mặc định cho phiên mới. Phiên hiện tại giữ thiết lập đang dùng.');
      read.retry();
    } catch (cause) { setNotice(cause instanceof Error ? cause.message : 'Không thực hiện được yêu cầu'); }
    finally { setBusy(false); }
  }
  return <section aria-label="Thiết lập đối chiếu và đánh giá"><h3>Thiết lập đối chiếu và đánh giá</h3>
    {profile ? <><p>Bộ quy tắc phiên bản {profile.version} · {{ACTIVE:'Đang áp dụng',VALIDATED:'Đã kiểm tra',DRAFT:'Bản nháp',RETIRED:'Đã ngừng dùng'}[profile.lifecycle]??'Đang cập nhật'}</p>
      <details><summary>Mã tra cứu thiết lập</summary><p>Mã bộ quy tắc: {profile.digest}</p><p>Cách tính điểm: {profile.score_unit}</p><p>Lần cập nhật: {pinned?.revision}</p></details></> : <p>Chưa có thông tin thiết lập đối chiếu riêng. Phiên vẫn dùng các quy tắc đã chọn khi bắt đầu.</p>}
    {feedback.status === 'fulfilled' ? <p>Kết quả bạn đã xác nhận: Cùng khách hàng {current.filter(e => e.label === 'MATCH').length}; Khác khách hàng {current.filter(e => e.label === 'NON_MATCH').length}. Giữ riêng hoặc hoãn xử lý không đồng nghĩa với xác nhận khác khách hàng.</p>
      : <p>Tài khoản hoặc phiên hiện tại chưa hỗ trợ góp ý kết quả đối chiếu.</p>}
    {reports.status === 'fulfilled' && reports.value.length ? reports.value.map(r => <p key={r.id}>Báo cáo {r.id}: {r.status==='COMPLETED'?'Đã hoàn tất':'Đang cập nhật'}{r.evaluated_scope.diagnostic_only ? ' · chỉ dùng để tham khảo' : ''}</p>)
      : <p>Chưa có báo cáo đánh giá cho phiên này. Điểm đối chiếu chưa cho biết kết quả gộp chính xác đến mức nào.</p>}
    <p>Chỉ điều chỉnh quy tắc khi có đủ kết quả đã xác minh và dữ liệu kiểm tra độc lập. Phiên đang chạy giữ thiết lập hiện tại.</p>
    {canLearn && feedback.status === 'fulfilled' && <button disabled={busy} onClick={() => void mutate(`/matching/runs/${runId}/learn-thresholds`, 'POST', {})}>Kiểm tra điều kiện điều chỉnh quy tắc</button>}
    {canApply && profiles.status === 'fulfilled' && defaultPointer.status === 'fulfilled' && <div>
      <p>Thiết lập mặc định cho phiên mới: {defaultPointer.value.profile_id || 'Chưa có'}. Bạn có thể chọn bộ quy tắc trước đó để khôi phục.</p>
      <label>Bộ quy tắc cho phiên mới <select value={selected} disabled={busy} onChange={e => setSelected(e.target.value)}><option value="">Chọn bộ quy tắc đã kiểm tra</option>
        {profiles.value.filter(p => ['ACTIVE', 'VALIDATED'].includes(p.lifecycle)).map(p => <option key={p.id} value={p.id}>Phiên bản {p.version} · {p.lifecycle==='ACTIVE'?'Đang áp dụng':'Đã kiểm tra'}</option>)}</select></label>
      <button disabled={busy || !selected} onClick={() => void mutate('/matching/defaults/owner-private', 'PUT', { profile_id: selected, expected_revision: defaultPointer.value.revision })}>Lưu thiết lập mặc định</button>
    </div>}
    {notice && <p role="status">{notice}</p>}
    <button onClick={read.retry}>Tải lại thiết lập và báo cáo</button>
  </section>;
}
