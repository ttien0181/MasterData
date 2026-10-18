import { afterEach, expect, it, vi } from 'vitest';
import { setCsrfToken } from './api';
import { getWorkflow, decidePair, bulkDecisions, saveGolden } from './workflowApi';
import { sessionTabs, tabReadiness, sessionPath } from './workflowNavigation';
import * as workflowApi from './workflowApi';
it('sends preview, exact group approval and token-bound finalize contracts',async()=>{
 const fetcher=vi.fn().mockImplementation(async()=>new Response('{}',{status:200}));vi.stubGlobal('fetch',fetcher);
 const base={request_id:'req',expected_workflow_revision:4,expected_decision_revision:2,expected_assessment_id:'a'};
 await workflowApi.previewClusters('r',{...base,allow_partial:true});
 expect(fetcher.mock.calls[0][0]).toContain('/workflow/cluster-preview');
 await workflowApi.approveGroup('r',{...base,record_ids:['a','b','c'],reason:'Evidence checked'});
 expect(fetcher.mock.calls[1][0]).toContain('/workflow/group-approvals');expect(JSON.parse(fetcher.mock.calls[1][1].body).record_ids).toEqual(['a','b','c']);
 await workflowApi.finalizeDecisions('r',{...base,allow_partial:true,preview_operation_id:'preview',preview_token:'token'});
 expect(JSON.parse(fetcher.mock.calls[2][1].body)).toMatchObject({preview_operation_id:'preview',preview_token:'token',allow_partial:true});
});

afterEach(() => vi.unstubAllGlobals());
it('reads without mutation, sends cookie/CSRF and exact revision contracts', async () => {
  const fetcher = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ items: [] }), { status: 200 }));
  vi.stubGlobal('fetch', fetcher); setCsrfToken('unit-csrf');
  await getWorkflow('r');
  expect(fetcher.mock.calls[0][1]?.method ?? 'GET').toBe('GET');
  await decidePair('r', 'p', { request_id: 'one', expected_workflow_revision: 4, expected_pair_revision: 2, decision: 'no_merge' });
  const init = fetcher.mock.calls[1][1];
  expect(init.credentials).toBe('include');
  expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('unit-csrf');
  expect(JSON.parse(init.body)).toMatchObject({ decision: 'no_merge', expected_pair_revision: 2 });
  await bulkDecisions('r', { request_id: 'bulk', expected_workflow_revision: 5, expected_decision_revision: 3, decision: 'merge', scope: { filter: { group: 'D' }, excluded_pair_ids: ['p'] } });
  expect(JSON.parse(fetcher.mock.calls[2][1].body).scope).toEqual({ filter: { group: 'D' }, excluded_pair_ids: ['p'] });
  await saveGolden('r', 'c', { request_id: 'save', expected_workflow_revision: 6, expected_cluster_revision: 1, expected_golden_revision: 2, selections: { phone: { selection_mode: 'SOURCE_CHOICE', source_record_id: 'member' } } });
  expect(fetcher.mock.calls[3][1].method).toBe('PUT');
});
it('preserves typed stale details without replaying a mutation', async () => {
  const fetcher = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ detail: { code: 'STALE_REVISION', current: 8 } }), { status: 409 }));
  vi.stubGlobal('fetch', fetcher);
  await expect(decidePair('r', 'p', { request_id: 'one', expected_workflow_revision: 4, expected_pair_revision: 2, decision: 'merge' })).rejects.toMatchObject({ status: 409, detail: { code: 'STALE_REVISION', current: 8 } });
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it('has four URL tabs and allows incremental grouping before golden publication', () => {
  expect(sessionTabs.map(t => t.id)).toEqual(['data', 'decisions', 'clusters', 'golden']);
  expect(sessionPath('r', 'golden')).toBe('/app/sessions/r/golden');
  for (const state of ['DATA_PREPARING', 'DATA_READY', 'DECIDE_RUNNING', 'DECIDE_REVIEW', 'CLUSTER_QUEUED', 'CLUSTER_RUNNING']) {
    expect(tabReadiness(state, 'clusters').ready).toBe(['DECIDE_REVIEW','CLUSTER_QUEUED','CLUSTER_RUNNING'].includes(state));
    expect(tabReadiness(state, 'golden').ready).toBe(false);
  }
  expect(tabReadiness('GOLDEN_REVIEW', 'clusters').ready).toBe(true);
  expect(tabReadiness('FINALIZED', 'golden').ready).toBe(true);
});
