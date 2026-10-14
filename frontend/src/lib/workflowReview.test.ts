import { afterEach, expect, it, vi } from 'vitest';
import * as api from './workflowApi';
import { tabReadiness } from './workflowNavigation';
import {reasonLabel} from './pairReview';
it('explains a missing soft Accept reason as required review evidence',()=>{
 expect(reasonLabel('SOFT_ACCEPT_REASON_REQUIRED')).toBe('Cần bổ sung lý do đối chiếu cho quyết định Accept có xung đột mềm');
});
it.each(['CLUSTER_GUARD_UNION_BUDGET','CLUSTER_GUARD_RUN_BUDGET'])('explains %s as an unfinished check with a recovery action',code=>{
 expect(reasonLabel(code)).toMatch(/Chưa kiểm tra xong/);expect(reasonLabel(code)).toMatch(/Tạo các nhóm đủ điều kiện.*thử lại/);
});
afterEach(()=>vi.unstubAllGlobals());
it('reads an individual pair and request receipt using encoded identifiers',async()=>{
 const fetcher=vi.fn(async (_url:string,_init?:RequestInit)=>new Response(JSON.stringify(null)));vi.stubGlobal('fetch',fetcher);
 await api.getPair('r/a','p/b');await api.getOperationByRequestId('r/a','q/b');
 expect(fetcher.mock.calls[0][0]).toContain('/runs/r%2Fa/workflow/pairs/p%2Fb');
 expect(fetcher.mock.calls[1][0]).toContain('/requests/q%2Fb');
});
it('posts a reasoned amendment and explicit partial completion',async()=>{
 const fetcher=vi.fn(async (_url:string,_init?:RequestInit)=>new Response(JSON.stringify({})));vi.stubGlobal('fetch',fetcher);
 await api.amendPair('r','p',{request_id:'a',expected_workflow_revision:2,expected_pair_revision:3,decision:'deny',reason:'Reviewed sources'});
 expect(fetcher.mock.calls[0][0]).toContain('/pairs/p/amend');expect(JSON.parse(fetcher.mock.calls[0][1]!.body as string).reason).toBe('Reviewed sources');
 await api.finalizeDecisions('r',{request_id:'f',expected_workflow_revision:2,expected_decision_revision:3,allow_partial:true,preview_operation_id:'preview',preview_token:'token'});
 expect(JSON.parse(fetcher.mock.calls[1][1]!.body as string).allow_partial).toBe(true);
});
it('keeps published cluster and golden snapshots readable during regeneration',()=>{
 for(const state of ['CLUSTER_QUEUED','CLUSTER_RUNNING'])for(const tab of ['clusters','golden'] as const)expect(tabReadiness(state,tab,2).ready).toBe(true);
});
it('reads paged actionable conflicts',async()=>{
 const fetcher=vi.fn(async (_url:string,_init?:RequestInit)=>new Response(JSON.stringify({items:[],total:0,next_cursor:null})));vi.stubGlobal('fetch',fetcher);
 await api.getConflicts('r',new URLSearchParams({cursor:'10'}));expect(fetcher.mock.calls[0][0]).toContain('/conflicts?cursor=10');
});
it('reads bounded unresolved record pages with the supplied cursor',async()=>{
 const fetcher=vi.fn(async (_url:string,_init?:RequestInit)=>new Response(JSON.stringify({items:[],total:50,next_cursor:'25'})));vi.stubGlobal('fetch',fetcher);
 await api.getUnresolvedRecords('r/a',new URLSearchParams({limit:'25',cursor:'25'}));
 expect(fetcher.mock.calls[0][0]).toContain('/runs/r%2Fa/workflow/unresolved-records?limit=25&cursor=25');
});
