'use client';
import { useCallback, useEffect, useEffectEvent, useRef } from 'react';
import { autoSelectGolden, confirmGolden, confirmSelectedGolden, getCluster, getClusterGolden, getClusters, goldenRevision, saveGolden, requestGroupAnnotations, mutation } from '@/lib/workflowApi';
import type { Selection, WorkflowRead } from '@/lib/workflowContracts';
import { tabReadiness } from '@/lib/workflowNavigation';
import { useWorkflow } from './SessionWorkspace';
import { useWorkflowAction } from './useWorkflowAction';
import { CachedReadStatus, Pager, ReadNotice } from './WorkflowReads';
import GoldenFieldEditor from './GoldenFieldEditor';
import GoldenResolution from './GoldenResolution';
import StructureControls from './StructureControls';
import GroupMembersTable from './GroupMembersTable';
import {pairEvidenceSummary} from '@/lib/pairEvidenceSummary';
import { usePageSelection } from './usePageSelection';
import ClusterListTable from './ClusterListTable';
import ClusterDetailDialog from './ClusterDetailDialog';
import styles from './ClusterWorkspace.module.css';
import { useSessionRead } from './useSessionRead';
import { removeStoredState, sessionKey, useSessionCache, useStoredState } from './SessionCache';
import { useTabView } from './useTabView';
export default function ClusterWorkspace({ initialClusterId = '' }: { initialClusterId?: string }) {
  const { workflow: w, refresh } = useWorkflow(); const action = useWorkflowAction(); const ready = w.cluster_revision>0 && tabReadiness(w.workflow_state, 'clusters', w.cluster_revision).ready;
  const selection = usePageSelection({}, `${w.run_id}:${w.cluster_revision}:${w.golden_revision}`, 'clusters');
  const scope = selection.all ? { excluded_cluster_ids: selection.ids } : selection.count ? { cluster_ids: selection.ids } : {};
  const cache = useSessionCache();
  const seenGeneration=useRef(w.cluster_revision);
  const [pagination, setPagination] = useStoredState<{index:number;pages:{cursor:string|null;offset:number}[]}>(w.run_id, `clusters:${w.cluster_revision}:pagination`, {index:0,pages:[{cursor:null,offset:0}]});
  const {cursor,offset}=pagination.pages[pagination.index];
  const [clusterId, select] = useStoredState(w.run_id, `clusters:${w.cluster_revision}:open`, seenGeneration.current===w.cluster_revision?initialClusterId:''); const [memberCursor, memberPage] = useStoredState<string | null>(w.run_id, `clusters:${clusterId}:members`, null); const [evidenceCursor, evidencePage] = useStoredState<string | null>(w.run_id, `clusters:${clusterId}:evidence`, null); const [dirty, setDirty] = useStoredState(w.run_id, `clusters:${clusterId}:dirty`, false);
  const [structureOpen, setStructureOpen] = useStoredState(w.run_id, `clusters:${w.cluster_revision}:${clusterId}:structure-open`, false);
  const [resolutionOpen,setResolutionOpen]=useStoredState(w.run_id,'clusters:resolution-open',false);
  const [draftGroups,setDraftGroups]=useStoredState<Record<string,string>>(w.run_id,`clusters:${w.cluster_revision}:draft-groups`,{});
  const hasDrafts=dirty||Object.keys(draftGroups).length>0;
  const listLoad = useCallback((signal: AbortSignal) => getClusters(w.run_id, new URLSearchParams(cursor ? { cursor } : {}), signal), [w.run_id, cursor, w.cluster_revision,w.decision_revision,w.workflow_state]);
  const list = useSessionRead(w.run_id, ['clusters', w.cluster_revision, w.golden_revision,w.decision_revision,w.workflow_state], { cursor }, listLoad, ready, true, Infinity);
  const detailLoad = useCallback((signal: AbortSignal) => getCluster(w.run_id, clusterId, new URLSearchParams({ ...(memberCursor ? { cursor: memberCursor } : {}), evidence_cursor: evidenceCursor || '0' }), signal), [w.run_id, clusterId, memberCursor, evidenceCursor, w.cluster_revision,w.decision_revision,w.workflow_state]);
  const detail = useSessionRead(w.run_id, ['cluster', clusterId, w.cluster_revision,w.decision_revision,w.workflow_state,w.annotation_revision], { memberCursor, evidenceCursor }, detailLoad, ready && !!clusterId, true, Infinity);
  const goldenLoad = useCallback((signal: AbortSignal) => getClusterGolden(w.run_id, clusterId, signal), [w.run_id, clusterId, w.cluster_revision, w.golden_revision,w.decision_revision,w.workflow_state]);
  const golden = useSessionRead(w.run_id, ['cluster-golden', clusterId, w.cluster_revision, w.golden_revision,w.decision_revision,w.workflow_state], {}, goldenLoad, ready && !!clusterId, false, Infinity);
  const updateDirty=useCallback((value:boolean)=>{
    setDirty(value);
    setDraftGroups(previous=>{
      const name=detail.data?.display_name||clusterId;
      if(value&&previous[clusterId]===name||!value&&!(clusterId in previous))return previous;
      const next={...previous};if(value)next[clusterId]=name;else delete next[clusterId];return next;
    });
  },[clusterId,detail.data?.display_name,setDirty,setDraftGroups]);
  const generations=[w.cluster_result,list.data?.generation,detail.data?.generation,golden.data?.generation].filter(g=>!!g);
  const published=list.data?.generation ?? w.cluster_result;
  const generation=published?{...published,stale:generations.some(g=>g.stale),read_only:generations.some(g=>g.read_only)}:published;
  const readOnly=!!generation?.read_only||!!generation?.stale||['CLUSTER_QUEUED','CLUSTER_RUNNING'].includes(w.workflow_state);
  const view = useTabView(w.run_id, `clusters:records:${w.cluster_revision}`, !!list.data && (!clusterId || !!detail.data || !!detail.error));
  const applyInitial = useEffectEvent(() => { if (initialClusterId) open(initialClusterId); });
  useEffect(() => { applyInitial(); }, [initialClusterId]);
  useEffect(()=>{
    if(seenGeneration.current===w.cluster_revision)return;
    seenGeneration.current=w.cluster_revision;select('');
    const url=new URL(window.location.href);url.searchParams.delete('cluster');window.history.replaceState(null,'',url);
  },[w.cluster_revision,select]);
  function open(id: string) { if (id === clusterId) return; const url = new URL(window.location.href); if (dirty && !window.confirm('Bỏ lựa chọn hồ sơ đại diện chưa lưu để mở nhóm khác?')) { url.searchParams.set('cluster', clusterId); window.history.replaceState(null, '', url); return; } if (dirty) { removeStoredState(cache, w.run_id, `golden-draft:${clusterId}`); updateDirty(false); } select(id); url.searchParams.set('cluster', id); window.history.replaceState(null, '', url); }
  function closeDetail() {
    if(action.busy)return;
    if(dirty&&!window.confirm('Đóng chi tiết nhóm khi chưa lưu hồ sơ đại diện? Bản nháp sẽ được giữ để bạn tiếp tục khi mở lại nhóm.'))return;
    select('');
    const url=new URL(window.location.href);url.searchParams.delete('cluster');window.history.replaceState(null,'',url);
  }
  async function reload() {
    await refresh().catch(() => undefined);
    const latest = cache.client.getQueryData<WorkflowRead>(sessionKey(cache.userId, w.run_id, 'workflow'));
    // Changed revisions already trigger fresh reads; only retry unchanged identities.
    if (latest?.cluster_revision === w.cluster_revision) {
      detail.retry();
      if (latest.golden_revision === w.golden_revision) golden.retry();
    }
  }
  async function save(choices: Record<string, Selection>, clusterRevision: number, goldenRevisionValue: number) { return action.run(() => saveGolden(w.run_id, clusterId, { ...goldenRevision(w), expected_cluster_revision: clusterRevision, expected_golden_revision: goldenRevisionValue, selections: choices })); }
  if (!ready) return <section id="clusters-list" className="workflow-panel"><h2>Danh sách nhóm hồ sơ</h2><p>{['CLUSTER_QUEUED','CLUSTER_RUNNING'].includes(w.workflow_state) ? (w.workflow_state === 'CLUSTER_QUEUED' ? 'Chờ cập nhật nhóm hồ sơ…' : 'Đang cập nhật nhóm hồ sơ…') : 'Chưa có nhóm hồ sơ. Hệ thống sẽ tự động cập nhật khi bạn xác nhận trùng ở bước 2.'}</p></section>;
  const selectedCluster=detail.data;
  const structureClusters = selectedCluster ? [selectedCluster, ...(list.data?.items || []).filter(c=>c.cluster_id!==selectedCluster.cluster_id)] : [];
  const detailTools = detail.data && <>
          {(memberCursor||detail.data.members.next_cursor)&&<Pager cursor={memberCursor} next={detail.data.members.next_cursor} onPage={memberPage} busy={detail.fetching&&detail.previous} />}
          <details data-view-key={`member-sources:${clusterId}`}><summary>Xem dữ liệu nguồn đầy đủ</summary>{detail.data.members.items.map(r=><details key={r.record_id} data-view-key={`member:${clusterId}:${r.record_id}`}><summary>{r.source_system} · {r.external_id||r.record_id}</summary><pre>{JSON.stringify({raw:r.raw_fields,normalized:r.normalized_fields,extra:r.extra_fields},null,2)}</pre></details>)}</details>
          <details data-view-key={`pair-evidence:${clusterId}`}><summary>Thông tin đối chiếu cặp ({detail.data.pair_evidence.total} cặp)</summary>{detail.data.pair_evidence.items.map(p=><details key={p.id} data-view-key={`evidence:${clusterId}:${p.id}`}><summary>{pairEvidenceSummary(p)} · {p.decision} · {p.score}</summary><pre>{JSON.stringify(p,null,2)}</pre></details>)}{(evidenceCursor||detail.data.pair_evidence.next_cursor)&&<Pager cursor={evidenceCursor} next={detail.data.pair_evidence.next_cursor} onPage={evidencePage} busy={detail.fetching&&detail.previous}/>}</details>
          <details data-view-key={`context:${clusterId}`}><summary>Thông tin đối chiếu bổ sung</summary><pre>{JSON.stringify({context:detail.data.workflow_context,llm:detail.data.llm_stats,evidence:detail.data.evidence},null,2)}</pre></details>
          {clusterId&&detail.data&&!dirty&&!readOnly&&!w.active_operation&&w.allowed_actions.some(action=>['reopen-decisions','save-golden','decide-pair','build-cluster-queue'].includes(action))&&generation?.publication_status==='COMPLETE'&&['GOLDEN_REVIEW','FINALIZED'].includes(w.workflow_state)&&<div className="workflow-toolbar"><button aria-expanded={structureOpen} aria-controls="cluster-structure-controls" onClick={()=>setStructureOpen(value=>!value)}>Tách/Gộp nhóm</button>{structureOpen&&<div id="cluster-structure-controls"><StructureControls key={`${w.run_id}:${w.cluster_revision}:${clusterId}:${w.workflow_revision}:${w.golden_revision}`} workflow={w} clusterId={clusterId} clusters={structureClusters} refresh={refresh}/></div>}</div>}
  </>;
  return <section id="clusters-list" ref={view} className={styles.workspace}>
    {action.error && <p role="alert">{action.error}</p>}
    {generation?.stale&&<p role="status">Kết quả chưa cập nhật, chỉ có thể xem.</p>}
    {!generation?.stale&&readOnly&&<p role="status">{['CLUSTER_QUEUED','CLUSTER_RUNNING'].includes(w.workflow_state)?'Đang cập nhật nhóm. Bạn có thể xem dữ liệu trong khi chờ xử lý.':'Kết quả hiện tại chỉ có thể xem, chưa thể chỉnh sửa hoặc xác nhận.'}</p>}
    <CachedReadStatus read={list} />
    {Object.keys(draftGroups).length>0&&<aside className={styles.draftNotice} aria-label="Bản nháp hồ sơ đại diện chưa lưu"><p>Lưu hoặc bỏ các bản nháp trước khi xác nhận hồ sơ đại diện hàng loạt.</p>{Object.entries(draftGroups).map(([id,name])=><button key={id} onClick={()=>open(id)}>Mở lại bản nháp nhóm {name}</button>)}</aside>}
    {!list.data ? <ReadNotice error={list.error} retry={list.retry} /> : <ClusterListTable data={list.data} clusterId={clusterId} selection={selection}
      pageIndex={pagination.index} offset={offset} busy={list.fetching} previous={list.previous}
      canConfirm={!readOnly&&!hasDrafts&&!action.busy&&w.allowed_actions.includes('confirm-golden')}
      onOpen={open}
      onFirst={()=>setPagination(previous=>({...previous,index:0}))}
      onPrevious={()=>setPagination(previous=>({...previous,index:Math.max(0,previous.index-1)}))}
      onNext={()=>{if(!list.data?.next_cursor||list.previous||list.fetching)return;const next={cursor:list.data.next_cursor,offset:offset+list.data.items.length};setPagination(previous=>({index:previous.index+1,pages:[...previous.pages.slice(0,previous.index+1),next]}));}}
      onConfirm={()=>void action.run(()=>confirmSelectedGolden(w.run_id,{...goldenRevision(w),...scope}))}/>}
    <details className={styles.secondaryTools} open={resolutionOpen} onToggle={event=>setResolutionOpen(event.currentTarget.open)}><summary>Công cụ bổ sung</summary>
      {w.allowed_actions.includes('annotate-groups')&&<p><button disabled={readOnly||action.busy} onClick={()=>void action.run(()=>requestGroupAnnotations(w.run_id,{...mutation(w),expected_cluster_revision:w.cluster_revision}))}>Bổ sung đối chiếu địa chỉ</button></p>}
      {resolutionOpen&&<GoldenResolution readOnly={readOnly} dirty={hasDrafts} scope={selection.all || selection.count ? scope : clusterId ? {cluster_ids:[clusterId]} : {}} />}
    </details>
    {clusterId && <ClusterDetailDialog key={`${w.run_id}:${w.cluster_revision}:${clusterId}`} title={`Chi tiết nhóm ${detail.data?.display_name || clusterId}`} close={closeDetail} busy={action.busy} dirty={dirty}>
      {action.error&&<p role="alert">{action.error}</p>}
<CachedReadStatus read={detail} />
      {!detail.data ? <ReadNotice error={detail.error} retry={detail.retry} /> : golden.data && golden.data.cluster_id === clusterId ? <>
        <CachedReadStatus read={golden} />
        <GoldenFieldEditor customerView key={clusterId} runId={w.run_id} golden={golden.data}
          members={detail.data.members.items.map(r=>({record_id:r.record_id,external_id:r.external_id,source_system:r.source_system,name:r.raw_fields.name,fields:{...r.extra_fields,...r.raw_fields}}))}
          totalMemberCount={detail.data.members.total}
          busy={action.busy} editable={!readOnly&&!golden.data.read_only&&w.allowed_actions.includes('save-golden')}
          onSave={save} onDirtyChange={updateDirty}
          actions={<>
            <button disabled={readOnly || !!golden.data.read_only || action.busy || dirty || !w.allowed_actions.includes('auto-select')} onClick={()=>void action.run(()=>autoSelectGolden(w.run_id,{...goldenRevision(w),cluster_ids:[clusterId]}))}>Tự động chọn thông tin đại diện</button>
            <button disabled={readOnly || !!golden.data.read_only || action.busy || dirty || !w.allowed_actions.includes('confirm-golden')} onClick={()=>void action.run(()=>confirmGolden(w.run_id,clusterId,goldenRevision(w)))}>Xác nhận hồ sơ đại diện</button>
            <button disabled={action.busy} onClick={()=>void reload()}>Tải lại dữ liệu</button>
          </>}>
          {detailTools}
        </GoldenFieldEditor>
      </> : <section className="workflow-panel"><h3>Thông tin đại diện của khách hàng</h3>
        <GroupMembersTable key={clusterId} totalMemberCount={detail.data.members.total} externalPagination members={detail.data.members.items.map(r=>({record_id:r.record_id,external_id:r.external_id,source_system:r.source_system,name:r.raw_fields.name,fields:{...r.extra_fields,...r.raw_fields}}))}/>
        {detailTools}
        <ReadNotice error={golden.error} retry={golden.retry}/>
      </section>}
    </ClusterDetailDialog>}
  </section>;
}
