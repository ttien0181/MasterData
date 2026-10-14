'use client';
import { useEffect } from 'react';
import { useStoredState } from './SessionCache';
import type { GoldenRead, Selection } from '@/lib/workflowContracts';
const manualFields = new Set(['name','email','phone','address','dob','government_id']);
const valueLabel = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value);
export default function GoldenFieldEditor({ golden, editable, busy, onSave, onDirtyChange, runId = '' }: { golden: GoldenRead; editable: boolean; busy: boolean; onSave: (choices: Record<string, Selection>, clusterRevision: number, goldenRevision: number) => Promise<unknown>; onDirtyChange?: (dirty: boolean) => void; runId?: string }) {
  // Retain intent across refreshed server revisions. Never silently rebase a draft.
  const [editor, setEditor] = useStoredState(runId, `golden-draft:${golden.cluster_id}`, () => ({ draft: null as Record<string, Selection> | null, base: golden, note: '', reasons: {} as Record<string,string> }));
  const { draft, base, note } = editor;
  const reasons = editor.reasons || {};
  const dirty = !!draft || Object.values(reasons).some(Boolean);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  function choose(field: string, selection: Selection) { setEditor(previous => ({ ...previous, draft: { ...(previous.draft || {}), [field]: selection }, base: dirty ? previous.base : golden, note: '' })); }
  const stale = dirty && (base.cluster_revision !== golden.cluster_revision || base.golden_revision !== golden.golden_revision);
  async function save() { if (!draft || stale) return; const result = await onSave(draft, base.cluster_revision, base.golden_revision); if (result) setEditor({ draft: null, base: golden, reasons: {}, note: 'Đã lưu lựa chọn trên server. Cần xác nhận Golden sau khi đối chiếu.' }); }
  return <section className="workflow-panel"><h3>Trường Golden</h3><p>Trạng thái: {golden.status} · Revision {golden.golden_revision}</p>
    {stale && <p role="alert">Server đã có revision mới. Bản nháp của bạn được giữ để đối chiếu; hãy bỏ nháp rồi chọn lại trên dữ liệu mới trước khi lưu.</p>}
    {note && <p role="status">{note}</p>}
    {!!golden.requires_manual_review?.length && <p role="alert">Thành viên nhóm đã thay đổi. Cần đối chiếu và chọn lại các giá trị: {golden.requires_manual_review.join(', ')} trước khi xác nhận.</p>}
    {Object.keys({ ...golden.fields, ...golden.alternatives, ...golden.pending_selections }).map(field => { const current = golden.fields[field]; const choice = draft?.[field] || golden.selections[field]; const candidates = golden.pending_selections?.[field] || []; const orphaned = candidates.length > 0 && !manualFields.has(field) && !golden.alternatives[field]?.length; return <fieldset key={field} disabled={!editable || busy}><legend>{field}</legend><p>Giá trị server: <strong>{valueLabel(current?.value)}</strong></p>{current?.values?.map((v, index) => <p key={index}><span>{valueLabel(v.value)}</span> <small>{v.source_system} · {v.source_record_id} · {v.provenance}</small></p>)}<p>Nguồn: {current?.source_system} {current?.source_record_id} · {current?.provenance} · {current?.quality}</p>
      {candidates.length > 0 && <div><p>Lựa chọn cũ cần đối chiếu:</p>{candidates.map((candidate,index) => <p key={index}><strong>{valueLabel(candidate.value)}</strong> <small>Nguồn {candidate.source_record_id || 'chỉnh tay'} · Nhóm {candidate.ancestor_cluster_id}</small></p>)}</div>}
      {orphaned && <div><p>Nguồn của trường bổ sung đã rời nhóm. Bạn có thể bỏ lựa chọn cũ; lý do và giá trị cũ sẽ được lưu trong lịch sử.</p><label>Lý do bỏ lựa chọn {field}<input aria-label={`Lý do bỏ lựa chọn ${field}`} maxLength={500} value={reasons[field] || ''} onChange={e => setEditor(previous => ({...previous,base:dirty ? previous.base : golden,reasons:{...(previous.reasons || {}),[field]:e.target.value}}))}/></label><button disabled={stale || !reasons[field]?.trim()} onClick={() => choose(field,{selection_mode:'DISCARD_PENDING',reason:reasons[field].trim()})}>Bỏ lựa chọn cũ {field}</button></div>}
      <div className="workflow-toolbar">{golden.alternatives[field]?.map((source, i) => <button key={`${source.source_record_id}-${i}`} aria-pressed={choice?.selection_mode === 'SOURCE_CHOICE' && choice.source_record_id === source.source_record_id} aria-label={`Chọn ${field} từ ${source.source_system}: ${valueLabel(source.value)}`} onClick={() => choose(field, { selection_mode: 'SOURCE_CHOICE', source_record_id: source.source_record_id })}>{source.source_system}: {valueLabel(source.value)}</button>)}</div>
      {manualFields.has(field) ? <label>Chỉnh tay {field}<input aria-label={`Chỉnh tay ${field}`} value={choice?.selection_mode === 'MANUAL_OVERRIDE' ? String(choice.value ?? '') : ''} onChange={e => choose(field, { selection_mode: 'MANUAL_OVERRIDE', value: e.target.value })} /></label> : <p>Trường bổ sung chỉ chọn nguồn. Auto áp dụng khi server có quy tắc rõ ràng.</p>}
      {choice && <p>Lựa chọn {draft?.[field] ? 'chưa lưu' : 'đã lưu'}: {choice.selection_mode} {choice.reason || choice.source_record_id || valueLabel(choice.value)}</p>}
    </fieldset>; })}
    {!!golden.discarded_pending_selections?.length && <details><summary>Lịch sử bỏ lựa chọn cũ</summary>{golden.discarded_pending_selections.map((entry,index) => <p key={index}>{entry.field}: {entry.reason} · {entry.actor_user_id} · {entry.created_at}</p>)}</details>}
    <div className="workflow-toolbar"><button disabled={!editable || busy || !draft || stale} onClick={() => void save()}>Lưu lựa chọn Golden</button><button disabled={busy || !dirty} onClick={() => setEditor({ draft: null, base: golden, reasons:{}, note: 'Đã bỏ bản nháp, dùng dữ liệu server hiện tại.' })}>Bỏ bản nháp</button></div>
  </section>;
}
