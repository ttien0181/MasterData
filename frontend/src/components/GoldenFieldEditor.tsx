'use client';
import { useEffect, type ReactNode } from 'react';
import {fieldLabels,valueText} from '@/lib/pairReview';
import {goldenStatusLabel,sourceChoiceLabel} from '@/lib/uiLabels';
import styles from './GoldenPages.module.css';
import { useStoredState } from './SessionCache';
import GroupMembersTable from './GroupMembersTable';
import memberStyles from './GroupMembersTable.module.css';
import type { GoldenRead, PreviewMember, Selection } from '@/lib/workflowContracts';
function FieldSection({compact,field,value,children}:{compact:boolean;field:string;value:unknown;children:ReactNode}){return compact?<details className={styles.field}><summary><span>{fieldLabels[field]||field}</span><strong>{valueText(value)}</strong><small>Thay đổi ⌄</small></summary>{children}</details>:<>{children}</>;}
const manualFields = new Set(['name','email','phone','address','dob','government_id']);
const valueLabel = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value);
export default function GoldenFieldEditor({ golden, editable, busy, onSave, onDirtyChange, runId = '', compact = false, customerView = false, members, totalMemberCount, children, actions }: { members?: PreviewMember[]; totalMemberCount?:number; children?:ReactNode; actions?:ReactNode; customerView?: boolean; compact?:boolean; golden: GoldenRead; editable: boolean; busy: boolean; onSave: (choices: Record<string, Selection>, clusterRevision: number, goldenRevision: number) => Promise<unknown>; onDirtyChange?: (dirty: boolean) => void; runId?: string }) {
  // Retain intent across refreshed server revisions. Never silently rebase a draft.
  const [editor, setEditor] = useStoredState(runId, `golden-draft:${golden.cluster_id}`, () => ({ draft: null as Record<string, Selection> | null, base: golden, note: '', reasons: {} as Record<string,string> }));
  const { draft, base, note } = editor;
  const reasons = editor.reasons || {};
  const dirty = !!draft || Object.values(reasons).some(Boolean);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  function choose(field: string, selection: Selection) { setEditor(previous => ({ ...previous, draft: { ...(previous.draft || {}), [field]: selection }, base: dirty ? previous.base : golden, note: '' })); }
  const stale = dirty && (base.cluster_revision !== golden.cluster_revision || base.golden_revision !== golden.golden_revision);
  async function save() { if (!draft || stale) return; const result = await onSave(draft, base.cluster_revision, base.golden_revision); if (result) {setEditor({ draft: null, base: golden, reasons: {}, note: 'Đã lưu thông tin bạn chọn. Bạn có thể xem lại hồ sơ đại diện trước khi xuất dữ liệu.' });window.dispatchEvent(new CustomEvent('demo:completed',{detail:'golden-save'}));} }
  const tableFields = [...new Set(['name','phone','email','dob','government_id','address', ...Object.keys(golden.fields), ...Object.keys(golden.alternatives), ...Object.keys(golden.pending_selections || {})])];
  const choices = {...golden.selections, ...draft};
  const selectedSources = Object.fromEntries(tableFields.map(field => {
    const choice = choices[field];
    return [field, choice ? choice.selection_mode === 'SOURCE_CHOICE' ? choice.source_record_id : undefined : golden.fields[field]?.source_record_id || undefined];
  }));
  function canChooseSource(field:string,recordId:string) {
    const source=golden.alternatives[field]?.find(item=>item.source_record_id===recordId);
    return !!source&&source.value!=null&&!['','—'].includes(valueText(source.value).trim());
  }
  function keptValue(field:string) {
    const choice=choices[field],source=golden.alternatives[field]?.find(value=>value.source_record_id===selectedSources[field]);
    const value=choice?.selection_mode==='MANUAL_OVERRIDE'?choice.value:choice?.selection_mode==='SOURCE_CHOICE'?source?.value ?? golden.fields[field]?.value:golden.fields[field]?.value;
    const pending=golden.pending_selections?.[field] || [];
    const orphaned=pending.length>0&&!manualFields.has(field)&&!golden.alternatives[field]?.length;
    return <>
      <strong>{choice?.selection_mode==='DISCARD_PENDING'?'Đã bỏ lựa chọn cũ':valueText(value)}</strong>
      {selectedSources[field]&&<small>{source?.source_system || golden.fields[field]?.source_system}{!members?.some(member=>member.record_id===selectedSources[field])?' · Hồ sơ ở trang khác':''}</small>}
      {choice?.selection_mode==='MANUAL_OVERRIDE'&&<small>Giá trị nhập</small>}
      {manualFields.has(field)&&editable&&<details open={choice?.selection_mode==='MANUAL_OVERRIDE'||undefined}><summary aria-label={`Nhập giá trị khác ${fieldLabels[field]||field}`}>Nhập giá trị khác</summary><input aria-label={`Nhập trực tiếp ${field}`} disabled={busy||stale} value={choice?.selection_mode==='MANUAL_OVERRIDE'?String(choice.value??''):''} onChange={event=>choose(field,{selection_mode:'MANUAL_OVERRIDE',value:event.target.value})}/></details>}
      {pending.length>0&&<details><summary>Lựa chọn cũ cần đối chiếu</summary>{pending.map((candidate,index)=><p key={index}>{valueText(candidate.value)}</p>)}</details>}
      {orphaned&&editable&&<><label>Lý do bỏ lựa chọn cũ<input aria-label={`Lý do bỏ lựa chọn ${field}`} disabled={busy||stale} maxLength={500} value={reasons[field]||''} onChange={event=>setEditor(previous=>({...previous,base:dirty?previous.base:golden,reasons:{...previous.reasons,[field]:event.target.value}}))}/></label><button disabled={busy||stale||!reasons[field]?.trim()} onClick={()=>choose(field,{selection_mode:'DISCARD_PENDING',reason:reasons[field].trim()})}>Bỏ lựa chọn cũ {fieldLabels[field]||field}</button></>}
    </>;
  }
  return <section className={`workflow-panel${members?' '+memberStyles.editor:''}`}><h3>{customerView?'Thông tin đại diện của khách hàng':compact?'Lựa chọn hồ sơ đại diện':'Trường hồ sơ đại diện'}</h3>{!compact&&!customerView&&<p>Trạng thái: {goldenStatusLabel(golden.status)} · Phiên bản dữ liệu {golden.golden_revision}</p>}
    {stale && <p role="alert">{customerView?'Dữ liệu đã thay đổi. Bản nháp được giữ để bạn đối chiếu; hãy bỏ bản nháp rồi chọn lại thông tin mới trước khi lưu.':'Dữ liệu đã được cập nhật. Bản nháp của bạn vẫn được giữ để xem lại; hãy bỏ bản nháp rồi chọn thông tin trên dữ liệu mới trước khi lưu.'}</p>}
    {note && <p role="status">{customerView?(note.startsWith('Đã lưu')?'Đã lưu thay đổi. Hãy xác nhận hồ sơ đại diện sau khi đối chiếu.':'Đã bỏ bản nháp, sử dụng dữ liệu hiện tại.'):compact&&note.startsWith('Đã lưu')?'Đã lưu thay đổi.':note}</p>}
    {!!golden.requires_manual_review?.length && <p role="alert">Thành viên nhóm đã thay đổi. Cần đối chiếu và chọn lại các giá trị: {golden.requires_manual_review.map(field=>members?(fieldLabels[field]||field):field).join(', ')} trước khi xác nhận.</p>}
    {members?<GroupMembersTable members={members} totalMemberCount={totalMemberCount} fields={tableFields} externalPagination golden={golden.fields} goldenLabel="Thông tin giữ lại" cellSelection={{selectedSources,disabled:!editable||busy||stale,canChoose:canChooseSource,onChoose:(field,id)=>choose(field,{selection_mode:'SOURCE_CHOICE',source_record_id:id}),renderKept:keptValue,isManual:field=>choices[field]?.selection_mode==='MANUAL_OVERRIDE'}}/>:Object.keys({ ...golden.fields, ...golden.alternatives, ...golden.pending_selections }).map(field => { const current = golden.fields[field]; const choice = draft?.[field] || golden.selections[field]; const candidates = golden.pending_selections?.[field] || []; const orphaned = candidates.length > 0 && !manualFields.has(field) && !golden.alternatives[field]?.length; return <FieldSection key={field} compact={compact} field={field} value={current?.value}><fieldset disabled={!editable || busy}><legend>{(compact||customerView)?(fieldLabels[field]||field):field}</legend><p>{customerView?'Thông tin hiện tại':'Thông tin đã lưu'}: <strong>{valueLabel(current?.value)}</strong></p>{current?.values?.map((v, index) => <p key={index}><span>{valueLabel(v.value)}</span> <small>{v.source_system} · {v.source_record_id} · {sourceChoiceLabel(v.provenance)}</small></p>)}<p>Nguồn: {current?.source_system}{!customerView&&<> {current?.source_record_id} · {sourceChoiceLabel(current?.provenance)} · {current?.quality}</>}</p>
      {candidates.length > 0 && <div><p>Lựa chọn cũ cần đối chiếu:</p>{candidates.map((candidate,index) => <p key={index}><strong>{valueLabel(candidate.value)}</strong> <small>Nguồn {candidate.source_record_id || 'nhập trực tiếp'} · Nhóm {candidate.ancestor_cluster_id}</small></p>)}</div>}
      {orphaned && <div><p>Nguồn của trường bổ sung đã rời nhóm. Bạn có thể bỏ lựa chọn cũ; lý do và giá trị cũ sẽ được lưu trong lịch sử.</p><label>Lý do bỏ lựa chọn {field}<input aria-label={`Lý do bỏ lựa chọn ${field}`} maxLength={500} value={reasons[field] || ''} onChange={e => setEditor(previous => ({...previous,base:dirty ? previous.base : golden,reasons:{...(previous.reasons || {}),[field]:e.target.value}}))}/></label><button disabled={stale || !reasons[field]?.trim()} onClick={() => choose(field,{selection_mode:'DISCARD_PENDING',reason:reasons[field].trim()})}>Bỏ lựa chọn cũ {field}</button></div>}
      <div className="workflow-toolbar">{golden.alternatives[field]?.map((source, i) => <button key={`${source.source_record_id}-${i}`} aria-pressed={choice?.selection_mode === 'SOURCE_CHOICE' && choice.source_record_id === source.source_record_id} aria-label={`Chọn ${field} từ ${source.source_system}: ${valueLabel(source.value)}`} onClick={() => choose(field, { selection_mode: 'SOURCE_CHOICE', source_record_id: source.source_record_id })}>{source.source_system}: {valueLabel(source.value)}</button>)}</div>
      {manualFields.has(field) ? <label>{compact||customerView?'Nhập giá trị khác':`Nhập trực tiếp ${field}`}<input aria-label={`Nhập trực tiếp ${field}`} value={choice?.selection_mode === 'MANUAL_OVERRIDE' ? String(choice.value ?? '') : ''} onChange={e => choose(field, { selection_mode: 'MANUAL_OVERRIDE', value: e.target.value })} /></label> : <p>Với thông tin bổ sung, hãy chọn giá trị từ hồ sơ nguồn. Hệ thống tự chọn khi đã có quy tắc phù hợp.</p>}
      {choice && <p>Lựa chọn {draft?.[field] ? 'chưa lưu' : 'đã lưu'}: {customerView?(choice.selection_mode==='MANUAL_OVERRIDE'?valueLabel(choice.value):choice.selection_mode==='SOURCE_CHOICE'?'Đã chọn thông tin từ hồ sơ nguồn':choice.reason):<>{sourceChoiceLabel(choice.selection_mode)} {choice.reason || choice.source_record_id || valueLabel(choice.value)}</>}</p>}
    </fieldset></FieldSection>; })}
    {!!golden.discarded_pending_selections?.length && <details><summary>Lịch sử bỏ lựa chọn cũ</summary>{golden.discarded_pending_selections.map((entry,index) => <p key={index}>{entry.field}: {entry.reason} · {entry.actor_user_id} · {entry.created_at}</p>)}</details>}
    {children}
    <div className="workflow-toolbar"><button data-demo="golden-save" disabled={!editable || busy || !draft || stale} onClick={() => void save()}>{customerView?'Lưu thông tin đại diện':'Lưu lựa chọn hồ sơ đại diện'}</button><button disabled={busy || !dirty} onClick={() => setEditor({ draft: null, base: golden, reasons:{}, note: 'Đã bỏ bản nháp, dùng dữ liệu hệ thống hiện tại.' })}>Bỏ bản nháp</button>{actions}</div>
  </section>;
}
