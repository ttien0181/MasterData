'use client';
import { useCallback, useEffect, useEffectEvent } from 'react';
import { autoSelectGolden, confirmGolden, confirmSelectedGolden, getCluster, getClusterGolden, getClusters, goldenRevision, saveGolden } from '@/lib/workflowApi';
import type { Selection, WorkflowRead } from '@/lib/workflowContracts';
import { tabReadiness } from '@/lib/workflowNavigation';
import { useWorkflow } from './SessionWorkspace';
import { useWorkflowAction } from './useWorkflowAction';
import { CachedReadStatus, Pager, ReadNotice } from './WorkflowReads';
import GoldenFieldEditor from './GoldenFieldEditor';
import GoldenResolution from './GoldenResolution';
import { usePageSelection } from './usePageSelection';
import PageCheckbox from './PageCheckbox';
import { useSessionRead } from './useSessionRead';
import { removeStoredState, sessionKey, useSessionCache, useStoredState } from './SessionCache';
import { useTabView } from './useTabView';
export default function ClusterWorkspace({ initialClusterId = '' }: { initialClusterId?: string }) {
  const { workflow: w, refresh } = useWorkflow(); const action = useWorkflowAction(); const ready = tabReadiness(w.workflow_state, 'clusters', w.cluster_revision).ready;
  const selection = usePageSelection({}, `${w.run_id}:${w.cluster_revision}:${w.golden_revision}`, 'clusters');
  const scope = selection.all ? { excluded_cluster_ids: selection.ids } : selection.count ? { cluster_ids: selection.ids } : {};
  const cache = useSessionCache();
  const [cursor, page] = useStoredState<string | null>(w.run_id, 'clusters:cursor', null); const [clusterId, select] = useStoredState(w.run_id, 'clusters:open', initialClusterId); const [memberCursor, memberPage] = useStoredState<string | null>(w.run_id, `clusters:${clusterId}:members`, null); const [evidenceCursor, evidencePage] = useStoredState<string | null>(w.run_id, `clusters:${clusterId}:evidence`, null); const [dirty, setDirty] = useStoredState(w.run_id, `clusters:${clusterId}:dirty`, false);
  const listLoad = useCallback((signal: AbortSignal) => getClusters(w.run_id, new URLSearchParams(cursor ? { cursor } : {}), signal), [w.run_id, cursor, w.cluster_revision]);
  const list = useSessionRead(w.run_id, ['clusters', w.cluster_revision, w.golden_revision], { cursor }, listLoad, ready);
  const detailLoad = useCallback((signal: AbortSignal) => getCluster(w.run_id, clusterId, new URLSearchParams({ ...(memberCursor ? { cursor: memberCursor } : {}), evidence_cursor: evidenceCursor || '0' }), signal), [w.run_id, clusterId, memberCursor, evidenceCursor, w.cluster_revision]);
  const detail = useSessionRead(w.run_id, ['cluster', clusterId, w.cluster_revision], { memberCursor, evidenceCursor }, detailLoad, ready && !!clusterId);
  const goldenLoad = useCallback((signal: AbortSignal) => getClusterGolden(w.run_id, clusterId, signal), [w.run_id, clusterId, w.cluster_revision, w.golden_revision]);
  const golden = useSessionRead(w.run_id, ['cluster-golden', clusterId, w.cluster_revision, w.golden_revision], {}, goldenLoad, ready && !!clusterId, false);
  const view = useTabView(w.run_id, 'clusters', !!list.data && (!clusterId || !!detail.data));
  const applyInitial = useEffectEvent(() => { if (initialClusterId) open(initialClusterId); });
  useEffect(() => { applyInitial(); }, [initialClusterId]);
  function open(id: string) { if (id === clusterId) return; const url = new URL(window.location.href); if (dirty && !window.confirm('Bỏ lựa chọn Golden chưa lưu để mở nhóm khác?')) { url.searchParams.set('cluster', clusterId); window.history.replaceState(null, '', url); return; } if (dirty) { removeStoredState(cache, w.run_id, `golden-draft:${clusterId}`); setDirty(false); } select(id); url.searchParams.set('cluster', id); window.history.replaceState(null, '', url); }
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
  if (!ready) return <section className="workflow-panel"><h2>Các nhóm bản ghi</h2><p>Chưa có nhóm chính thức. Hoàn tất phân loại để tạo nhóm; trang này chỉ đọc tiến độ.</p></section>;
  return <section ref={view}><h2>Các nhóm bản ghi</h2>{action.error && <p role="alert">{action.error}</p>}
    <CachedReadStatus read={list} /><GoldenResolution dirty={dirty} scope={selection.all || selection.count ? scope : clusterId ? {cluster_ids:[clusterId]} : {}} />
    {list.data && <div className="workflow-toolbar"><PageCheckbox ids={list.data.items.map(c => c.cluster_id)} selected={selection.selected} onSelect={selection.selectPage} onDeselect={selection.deselectPage} /><button onClick={selection.selectAll}>Chọn tất cả nhóm</button><button onClick={selection.clear}>Bỏ chọn nhóm</button><span>{selection.all ? `Đã chọn ${list.data.total - selection.count} nhóm` : `Đã chọn ${selection.count} nhóm`}</span><button disabled={dirty || action.busy || !w.allowed_actions.includes('confirm-golden') || (!selection.all && !selection.count)} onClick={() => void action.run(() => confirmSelectedGolden(w.run_id,{...goldenRevision(w),...scope}))}>Accept nhóm đã chọn</button></div>}
    {!list.data ? <ReadNotice error={list.error} retry={list.retry} /> : <section className="workflow-panel"><p>{list.data.total} nhóm. Singleton vẫn được tính vào độ phủ bản ghi.</p><div className="workflow-toolbar">{list.data.items.map(c => <div key={c.cluster_id}><input type="checkbox" aria-label={`Chọn nhóm ${c.cluster_id}`} checked={selection.selected(c.cluster_id)} onChange={() => selection.toggle(c.cluster_id)} /><button aria-pressed={c.cluster_id === clusterId} onClick={() => open(c.cluster_id)}>{c.singleton ? 'Singleton' : 'Nhóm'} {c.cluster_id} · {c.member_count} bản ghi · {c.status}</button></div>)}</div>{!list.data.total && <p>Không có nhóm.</p>}<Pager cursor={cursor} next={list.data.next_cursor} onPage={page} busy={list.fetching && list.previous} /></section>}
    {clusterId && <><h3>Chi tiết nhóm {clusterId}</h3><CachedReadStatus read={detail} />{!detail.data ? <ReadNotice error={detail.error} retry={detail.retry} /> : <section className="workflow-panel"><h4>Thành viên ({detail.data.members.total} bản ghi)</h4>{detail.data.members.items.map(r => <details key={r.record_id} data-view-key={`member:${clusterId}:${r.record_id}`}><summary>{r.source_system} · {r.record_id}</summary><pre>{JSON.stringify({ raw: r.raw_fields, normalized: r.normalized_fields, extra: r.extra_fields }, null, 2)}</pre></details>)}<Pager cursor={memberCursor} next={detail.data.members.next_cursor} onPage={memberPage} busy={detail.fetching && detail.previous} /><h4>Bằng chứng cặp ({detail.data.pair_evidence.total} cặp)</h4>{detail.data.pair_evidence.items.map(p => <details key={p.id} data-view-key={`evidence:${clusterId}:${p.id}`}><summary>{p.left_id} / {p.right_id} · {p.decision} · {p.score}</summary><pre>{JSON.stringify(p, null, 2)}</pre></details>)}<Pager cursor={evidenceCursor} next={detail.data.pair_evidence.next_cursor} onPage={evidencePage} busy={detail.fetching && detail.previous} /><details data-view-key={`context:${clusterId}`}><summary>Ngữ cảnh / semantic annotations</summary><pre>{JSON.stringify({ context: detail.data.workflow_context, llm: detail.data.llm_stats, evidence: detail.data.evidence }, null, 2)}</pre></details></section>}
      {golden.data && golden.data.cluster_id === clusterId ? <><CachedReadStatus read={golden} /><GoldenFieldEditor key={clusterId} runId={w.run_id} golden={golden.data} busy={action.busy} editable={w.allowed_actions.includes('save-golden')} onSave={save} onDirtyChange={setDirty} /><div className="workflow-toolbar"><button disabled={action.busy || dirty || !w.allowed_actions.includes('auto-select')} onClick={async () => { await action.run(() => autoSelectGolden(w.run_id, { ...goldenRevision(w), cluster_ids: [clusterId] })); }}>Auto chọn Golden của nhóm</button><button disabled={action.busy || dirty || !w.allowed_actions.includes('confirm-golden')} onClick={async () => { await action.run(() => confirmGolden(w.run_id, clusterId, goldenRevision(w))); }}>Xác nhận Golden của nhóm</button><button disabled={action.busy} onClick={() => void reload()}>Đọc lại từ server</button></div><p>Auto giữ lựa chọn thủ công đã lưu. Lưu bản nháp trước khi Auto hoặc xác nhận.</p></> : <ReadNotice error={golden.error} retry={golden.retry} />}
    </>}
  </section>;
}
