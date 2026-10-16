'use client';
import { useCallback, useState } from 'react';
import { request } from '@/lib/api';
import { valueText } from '@/lib/pairReview';
import type { ClusterRead, OperationRead } from '@/lib/workflowContracts';
import { ReadNotice, useWorkflowRead } from './WorkflowReads';

type Versions = { run_id: string; workflow_state: string; workflow_revision: number; decision_revision: number;
  cluster_revision: number; golden_revision: number };
type Availability = { enabled: boolean; can_manage: boolean; active_constraints: { id: string; partitions: string[][] }[] };
type Preview = OperationRead & { result: { preview_token: string; blocked_reasons: string[];
  intent: { partitions: string[][] }; cross_candidate_pair_ids: string[];
  affected_golden: { cluster_id: string; confirmed: boolean }[] } };
type Bridges = { total: number; limit: number; items: { pair_id: string; left_name: string; right_name: string; decision: string; rule_score: number; score_unit: string }[] };
type Members = { members: { total: number; items: { record_id: string; external_id?: string; source_system: string; raw_fields: Record<string, unknown> }[] } };

export default function StructureControls({ workflow: w, clusters, refresh, clusterId }: {
  workflow: Versions; clusters: ClusterRead[]; refresh: () => Promise<unknown>; clusterId?: string;
}) {
  const root = `/matching/runs/${w.run_id}/structure`;
  const load = useCallback((signal: AbortSignal) => request<Availability>(root, { signal }), [root]);
  const state = useWorkflowRead(load);
  const [kind, setKind] = useState<'split' | 'remerge'>('split');
  const [first, setFirst] = useState(clusterId || ''); const [second, setSecond] = useState('');
  const [selected, setSelected] = useState<string[]>([]); const [reason, setReason] = useState('');
  const [bridge, setBridge] = useState(''); const [constraint, setConstraint] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null); const [error, setError] = useState('');
  const [busy, setBusy] = useState(false); const [operation, setOperation] = useState('');
  const bridgeLoad = useCallback((signal: AbortSignal) => request<Bridges>(`${root}/bridges?${new URLSearchParams({ left_cluster_id: first, right_cluster_id: second })}`, { signal }), [root, first, second]);
  const bridges = useWorkflowRead(bridgeLoad, kind === 'remerge' && !!first && !!second && state.data?.enabled === true);
  const memberLoad = useCallback((signal: AbortSignal) => request<Members>(`/runs/${w.run_id}/workflow/clusters/${encodeURIComponent(first)}?limit=100`, { signal }), [w.run_id, first]);
  const members = useWorkflowRead(memberLoad, kind === 'split' && !!first && state.data?.enabled === true);
  if (!state.data) return <ReadNotice error={state.error} retry={state.retry} />;
  if (!state.data.enabled) return <p>Split/Remerge chưa được bật ở môi trường này.</p>;
  if (!state.data.can_manage) return <p>Quyền hiện tại chỉ cho xem cấu trúc nhóm.</p>;
  if (!['GOLDEN_REVIEW', 'FINALIZED'].includes(w.workflow_state)) return <p>Split/Remerge cần generation hoàn chỉnh đang được phục vụ.</p>;
  const group = clusters.find(c => c.cluster_id === first);
  const memberItems = members.data?.members.items || [];
  const versions = () => ({ expected_workflow_revision: w.workflow_revision, expected_decision_revision: w.decision_revision,
    expected_cluster_revision: w.cluster_revision, expected_golden_revision: w.golden_revision });
  const write = <T,>(path: string, body: unknown) => request<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  async function prepare() {
    if (!group) return;
    setBusy(true); setError(''); setPreview(null);
    try {
      const body = { request_id: crypto.randomUUID(), ...versions(), reason,
        cluster_ids: kind === 'split' ? [first] : [first, second],
        ...(kind === 'split' ? { partitions: [selected, group.member_record_ids.filter(r => !selected.includes(r))] }
          : { bridge_pair_ids: bridge.split(',').map(v => v.trim()).filter(Boolean), constraint_id: constraint }) };
      setPreview(await write<Preview>(`${root}/${kind}/preview`, body));
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Preview thất bại'); }
    finally { setBusy(false); }
  }
  async function confirm() {
    if (!preview || preview.result.blocked_reasons.length) return;
    setBusy(true); setError('');
    try {
      const result = await write<OperationRead>(root + '/execute', { request_id: crypto.randomUUID(), ...versions(),
        preview_operation_id: preview.operation_id, preview_token: preview.result.preview_token });
      setOperation(result.operation_id); setPreview(null); await refresh();
    } catch (cause) { setPreview(null); setError(cause instanceof Error ? cause.message : 'Execute thất bại'); await refresh(); }
    finally { setBusy(false); }
  }
  return <section className="workflow-panel" aria-label="Tách và gộp nhóm"><h3>Tách và gộp nhóm</h3>
    {error && <p role="alert">{error}</p>}{operation && <p role="status">Đã xếp hàng operation {operation}; chờ worker publication.</p>}
    <label>Thao tác <select value={kind} disabled={busy} onChange={e => { setKind(e.target.value as typeof kind); setPreview(null); }}>
      <option value="split">Tách một nhóm</option><option value="remerge">Gộp lại hai nhóm</option></select></label>
    {clusterId?<p>Nhóm {group?.display_name || first} · {group?.member_count ?? 0} bản ghi</p>:<label>Nhóm trên trang hiện tại <select value={first} disabled={busy} onChange={e => { setFirst(e.target.value); setSecond(''); setBridge(''); setConstraint(''); setSelected([]); setPreview(null); }}>
      <option value="">Chọn nhóm</option>{clusters.map(c => <option key={c.cluster_id} value={c.cluster_id}>{c.display_name || c.cluster_id} ({c.member_count})</option>)}</select></label>}
    {kind === 'split' && group && <fieldset><legend>Chọn thành viên partition 1; phần còn lại thuộc partition 2</legend>
      {!members.data ? <ReadNotice error={members.error} retry={members.retry} /> : group.member_record_ids.map(rid => {
        const member = memberItems.find(r => r.record_id === rid);
        return <label key={rid} style={{ display: 'block' }}><input type="checkbox" disabled={busy} checked={selected.includes(rid)}
          onChange={() => { setSelected(old => old.includes(rid) ? old.filter(r => r !== rid) : [...old, rid]); setPreview(null); }} />
          {member ? `${member.source_system} · ${member.external_id || rid} · ${valueText(member.raw_fields.name)}` : rid}</label>;
      })}
    </fieldset>}
    {kind === 'remerge' && <><label>Nhóm thứ hai <select value={second} disabled={busy} onChange={e => { setSecond(e.target.value); setBridge(''); setConstraint(''); setPreview(null); }}>
      <option value="">Chọn nhóm</option>{clusters.filter(c => c.cluster_id !== first).map(c => <option key={c.cluster_id} value={c.cluster_id}>{c.display_name || c.cluster_id}</option>)}</select></label>
      <label>Constraint cần release <select value={constraint} disabled={busy} onChange={e => { setConstraint(e.target.value); setPreview(null); }}>
        <option value="">Chọn constraint</option>{state.data.active_constraints.map(c => <option key={c.id} value={c.id}>Tách {c.partitions.map(p => p.length).join('/')} · {c.id.slice(0, 8)}</option>)}</select></label>
      {first && second && (!bridges.data ? <ReadNotice error={bridges.error} retry={bridges.retry} /> : <fieldset><legend>Chọn bằng chứng nối đã tồn tại</legend>
        <p>Hiển thị {bridges.data.items.length}/{bridges.data.total} cặp. Không tạo bridge mới.</p>
        {bridges.data.items.map(p => <label key={p.pair_id}><input type="checkbox" disabled={busy} checked={bridge.split(',').includes(p.pair_id)}
          onChange={() => { setBridge(old => { const ids = old.split(',').filter(Boolean); return (ids.includes(p.pair_id) ? ids.filter(id => id !== p.pair_id) : [...ids, p.pair_id]).join(','); }); setPreview(null); }} />
          {p.left_name} ↔ {p.right_name} · {p.decision} · score {p.rule_score} ({p.score_unit})</label>)}
        {!bridges.data.total && <p>Không có candidate bridge giữa hai nhóm; Remerge chưa được hỗ trợ trong phạm vi này.</p>}
      </fieldset>)}</>}
    <label>Lý do<input value={reason} disabled={busy} maxLength={500} onChange={e => { setReason(e.target.value); setPreview(null); }} /></label>
    <button disabled={busy || !first || !reason.trim() || (kind === 'split' && (!group || !selected.length || selected.length === group.member_count)) || (kind === 'remerge' && (!second || !bridge.trim() || !constraint))}
      onClick={() => void prepare()}>Preview {kind === 'split' ? 'tách' : 'gộp lại'}</button>
    {preview && <div><p>Partitions: {preview.result.intent.partitions.map(p => p.length).join(' / ')} thành viên.</p>
      <p>Cross-partition candidate edges: {preview.result.cross_candidate_pair_ids.length}. Golden ảnh hưởng: {preview.result.affected_golden.length}.</p>
      {preview.result.blocked_reasons.length > 0 ? <p role="alert">Blocked: {preview.result.blocked_reasons.join(', ')}</p>
        : <button disabled={busy} onClick={() => void confirm()}>Xác nhận thay đổi cấu trúc và tạo generation mới</button>}</div>}
  </section>;
}
