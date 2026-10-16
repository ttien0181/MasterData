'use client';
import { useCallback } from 'react';
import Link from 'next/link';
import { executeResolutionPreview } from '@/lib/api';
import { finalizeGolden, getGoldenRecords, mutation, prepareWorkflowExport, reopenDecisions } from '@/lib/workflowApi';
import GoldenResolution from './GoldenResolution';
import GenerationStatus from './GenerationStatus';
import { tabReadiness } from '@/lib/workflowNavigation';
import { blockerLabel, useWorkflow } from './SessionWorkspace';
import { useWorkflowAction } from './useWorkflowAction';
import { CachedReadStatus, Pager, ReadNotice } from './WorkflowReads';
import { useSessionRead } from './useSessionRead';
import { useStoredState } from './SessionCache';
import { useTabView } from './useTabView';
const fieldLabels: Record<string, string> = { name: 'Họ tên', phone: 'Điện thoại', email: 'Email', address: 'Địa chỉ', dob: 'Ngày sinh', government_id: 'Giấy tờ định danh' };
const displayValue = (value: unknown) => value == null || value === '' ? 'Chưa có giá trị' : typeof value === 'string' ? value : JSON.stringify(value);
export default function GoldenRecords() {
  const { workflow: w } = useWorkflow(); const action = useWorkflowAction(); const [cursor, page] = useStoredState<string | null>(w.run_id, 'golden:cursor', null); const [preview, setPreview] = useStoredState<Awaited<ReturnType<typeof prepareWorkflowExport>> | null>(w.run_id, `golden:preview:${w.golden_revision}:${w.workflow_state}`, null); const [reason, setReason] = useStoredState(w.run_id, 'golden:reason', '');
  const ready = tabReadiness(w.workflow_state, 'golden', w.cluster_revision).ready;
  const load = useCallback((signal: AbortSignal) => getGoldenRecords(w.run_id, new URLSearchParams(cursor ? { cursor } : {}), signal), [w.run_id, cursor, w.golden_revision, w.workflow_state,w.decision_revision]);
  const read = useSessionRead(w.run_id, ['golden', w.cluster_revision, w.golden_revision, w.workflow_state,w.decision_revision], { cursor }, load, ready);
  const generations=[w.cluster_result,read.data?.generation,...(read.data?.items.map(g=>g.generation)||[])].filter(g=>!!g);
  const published=read.data?.generation ?? w.cluster_result;
  const generation=published?{...published,stale:generations.some(g=>g.stale),read_only:generations.some(g=>g.read_only)}:published;
  const readOnly=!!w.active_operation||!!generation?.read_only||!!generation?.stale||!!read.data?.items.some(g=>g.read_only)||['CLUSTER_QUEUED','CLUSTER_RUNNING'].includes(w.workflow_state);
  const incomplete=generations.some(g=>g.stale||g.publication_status==='PARTIAL'||g.covered_records!==g.total_records);
  const view = useTabView(w.run_id, 'golden', !!read.data);
  if (!ready) return <section className="workflow-panel"><h2>Golden record</h2><p>Golden chưa sẵn sàng. Cần nhóm chính thức sau hoàn tất phân loại.</p></section>;
  return <section id="golden-overview" ref={view} className="workflow-panel"><h2>Golden record</h2><GenerationStatus runId={w.run_id} generation={generation} operation={w.latest_cluster_operation}/><GoldenResolution readOnly={readOnly}/>{action.error && <p role="alert">{action.error}</p>}
    {!read.data ? <ReadNotice error={read.error} retry={read.retry} /> : <><CachedReadStatus read={read} /><div id="golden-records" className="workflow-golden-grid">{read.data.items.map(g => <article className="workflow-golden-card" key={g.cluster_id}>
      <header><Link scroll={false} href={`/app/sessions/${encodeURIComponent(w.run_id)}/clusters/records?cluster=${encodeURIComponent(g.cluster_id)}`}>Nhóm {g.cluster_id}</Link><span className="workflow-golden-status">{g.status}</span></header>
      <dl>{Object.entries(g.fields).map(([field, selected]) => <div key={field}><dt>{fieldLabels[field] || field}</dt><dd>
        {selected.values?.length ? selected.values.map((item, index) => <p key={index}><strong>{displayValue(item.value)}</strong><small>{[item.source_system, item.provenance].filter(Boolean).join(' · ')}</small></p>) : <p><strong>{displayValue(selected.value)}</strong></p>}
        <small>{[selected.source_system, selected.provenance, selected.quality].filter(Boolean).join(' · ')}</small>
        {(selected.source_record_id || selected.values?.some(item => item.source_record_id)) && <details data-view-key={`source:${g.cluster_id}:${field}`}><summary>Bản ghi nguồn</summary><p>{selected.source_record_id}</p>{selected.values?.map((item, index) => item.source_record_id && <p key={index}>{item.source_record_id}</p>)}</details>}
      </dd></div>)}</dl>
    </article>)}</div>{!read.data.total && <p>Không có Golden.</p>}<Pager cursor={cursor} next={read.data.next_cursor} onPage={page} busy={read.fetching && read.previous} /></>}
    {w.blockers.map(b => <p key={b.code}>{blockerLabel(b.code)} {b.count ?? ''} {b.unit || ''}</p>)}
    <div id="golden-export"><button disabled={readOnly || incomplete || action.busy || !w.allowed_actions.includes('finalize-golden')} onClick={() => void action.run(() => finalizeGolden(w.run_id, { ...mutation(w), expected_golden_revision: w.golden_revision }))}>Chốt phiên</button>
    {w.workflow_state === 'FINALIZED' && <div className="workflow-toolbar"><button disabled={incomplete || action.busy || !w.allowed_actions.includes('export')} onClick={async () => { const result = await action.run(() => prepareWorkflowExport(w.run_id)); if (result) setPreview(result); }}>Chuẩn bị xuất CSV</button>{preview && <><p>{preview.estimated_output_rows} dòng · Hash {preview.result_hash}</p><button disabled={incomplete || action.busy || !w.allowed_actions.includes('export')} onClick={() => void action.run(() => executeResolutionPreview(w.run_id, preview.preview_id, crypto.randomUUID()))}>Xuất CSV</button></>}</div>}
    </div>{w.allowed_actions.includes('reopen-decisions') && <details data-view-key="reopen"><summary>Mở lại phân loại</summary><p>Mở lại sẽ vô hiệu hóa readiness hiện tại; lịch sử file đã xuất vẫn giữ.</p><label>Lý do <input value={reason} onChange={e => setReason(e.target.value)} /></label><button disabled={action.busy || !reason.trim()} onClick={async () => { const result = await action.run(() => reopenDecisions(w.run_id, { ...mutation(w), reason: reason.trim() })); if (result) { setPreview(null); setReason(''); } }}>Mở lại phân loại</button></details>}
  </section>;
}
