// Isolated production-browser smoke. Every API request is fulfilled locally;
// this script never uses cookies, credentials, or data from a real backend.
// Run an existing production build on port 3013, then: node scripts/verify-cluster-handoff.mjs
import {chromium,expect as baseExpect} from '@playwright/test';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const expect=baseExpect.configure({timeout:12000});
const origin='http://localhost:3013';
const out=resolve('artifacts/cluster-handoff-20261013');
await mkdir(out,{recursive:true});
const checks=[],requests=[],errors=[],unexpected=[];
const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
const clone=value=>JSON.parse(JSON.stringify(value));
const record=(id,name,source)=>({record_id:id,external_id:id,source_system:source,raw_fields:{name,phone:'0901234567',email:`${id}@example.test`},normalized_fields:{name:name.toLowerCase()},extra_fields:{}});
const pair=(id,left,right,group='A')=>({pair_id:id,assessment_id:'mock-assessment',revision:0,decision:'UNDECIDED',score_unit:'rule',baseline:{id,left_id:left.record_id,right_id:right.record_id,primary_group:group,subgroup:group==='D'?'D2_NAME_CONFLICT':'A1_NAME_PHONE',decision:'REVIEW',score:4,field_evidence:[]},eligibility:{manual_allowed:true,auto_allowed:false,blocked_reasons:[],reason_required:group==='D'},left_record:left,right_record:right});
const operation=(id,type,status,result={})=>({operation_id:id,run_id:'mock-handoff',operation_type:type,status,stage:status==='COMPLETED'?'completed':'calculating',progress:{processed:status==='COMPLETED'?4:1,total:4},error_code:null,result});

async function scenario({stale=false,gateSingle=false}={}){
 const context=await browser.newContext({viewport:{width:1440,height:1100},serviceWorkers:'block'});
 const page=await context.newPage();page.setDefaultTimeout(12000);
 page.on('pageerror',error=>errors.push(error.message));
 const pairs=[pair('soft-d',record('a','Nguyễn Lan','CRM'),record('b','Nguyễn Lân','BILL'),'D'),pair('keep-apart',record('c','Trần Bình','CRM'),record('d','Trần Binh','BILL'))];
 const state={workflow:{run_id:'mock-handoff',workflow_version:2,assessment_id:'mock-assessment',workflow_state:'DECIDE_REVIEW',workflow_revision:4,decision_revision:1,cluster_revision:0,golden_revision:0,counts:{records:{value:4,unit:'records'}},allowed_actions:['decide-pair','bulk-decisions','preview-clusters','finalize-decisions','finalize-partial','approve-group'],blockers:[],active_operation:null,retryable_operation:null},decisions:[],bulk:[],previews:[],finalizes:[],clusterReads:0,operationReads:0};
 let releaseSingle;const singleGate=new Promise(resolve=>{releaseSingle=resolve;});
 let previewResult;
 // A second catch-all gate aborts unexpected external resources. No route.continue
 // can forward an API or external-host request to a real backend.
 await context.route('**/*',async route=>{
  const url=new URL(route.request().url());
  if(url.pathname.startsWith('/api/v1/')||url.origin!==origin){unexpected.push(url.href);return route.abort('blockedbyclient');}
  return route.continue();
 });
 await context.route('**/api/v1/**',async route=>{
  const request=route.request(),url=new URL(request.url()),path=url.pathname,method=request.method();
  const body=method==='GET'||method==='OPTIONS'?null:request.postDataJSON();
  requests.push({method,path,body});
  const fulfill=(json,status=200)=>route.fulfill({status,json,headers:{'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Credentials':'true','Access-Control-Allow-Headers':'Content-Type, X-CSRF-Token','Access-Control-Allow-Methods':'GET, POST, PUT, OPTIONS'}});
  if(method==='OPTIONS')return fulfill({});
  if(path==='/api/v1/auth/me')return fulfill({id:'isolated-browser-user',email:'mock@example.test',role:'STEWARD',csrf_token:'mock-csrf',capabilities:['read','approve_pair','reject_pair']});
  const root='/api/v1/mdm/runs/mock-handoff/workflow';
  if(path===root)return fulfill(clone(state.workflow));
  if(path===`${root}/pairs/summary`)return fulfill({assessment_id:'mock-assessment',total:2,needs_review:pairs.filter(p=>p.decision==='UNDECIDED').length,accepted:pairs.filter(p=>p.decision==='ACCEPTED').length,denied:0,kept_separate:pairs.filter(p=>p.decision==='REJECTED').length,deferred:0,blocked:0,manual_available:2,auto_available:0,decision_revision:state.workflow.decision_revision,groups:['A','D'].map(key=>({key,total:1,pending:1,subgroups:[{key:key==='D'?'D2_NAME_CONFLICT':'A1_NAME_PHONE',total:1}]}))});
  if(path===`${root}/pairs`){const items=pairs.filter(p=>(!url.searchParams.get('group')||p.baseline.primary_group===url.searchParams.get('group'))&&(!url.searchParams.get('subgroup')||p.baseline.subgroup===url.searchParams.get('subgroup')));return fulfill({items:clone(items),total:items.length,offset:0,next_cursor:null});}
  if(path.startsWith(`${root}/pairs/`)&&path.endsWith('/decision')&&method==='POST'){
   state.decisions.push(clone(body));if(gateSingle&&state.decisions.length===1)await singleGate;
   const id=path.split('/').at(-2),found=pairs.find(p=>p.pair_id===id);if(!found)throw new Error('Unknown mock pair');
   expect(body.expected_assessment_id).toBe('mock-assessment');expect(body.expected_pair_revision).toBe(found.revision);
   found.decision={merge:'ACCEPTED',no_merge:'REJECTED',deny:'NON_MATCH',defer:'DEFERRED'}[body.decision];found.review_reason=body.reason;found.revision++;
   state.workflow.workflow_revision++;state.workflow.decision_revision++;return fulfill(clone(found));
  }
  if(path===`${root}/bulk-decisions/preview`)return fulfill({preview_token:'mock-bulk-token',total:2,applicable:2,blocked:0,skipped:0,reason_required:1,workflow_revision:state.workflow.workflow_revision,decision_revision:state.workflow.decision_revision});
  if(path===`${root}/bulk-decisions`&&method==='POST'){state.bulk.push(clone(body));return fulfill(operation('mock-bulk','BULK_DECISIONS','COMPLETED'));}
  if(path===`${root}/cluster-preview`&&method==='POST'){
   state.previews.push(clone(body));state.operationReads=0;
   previewResult={preview_token:'mock-preview-token',publication:{publication_status:'COMPLETE',clusters:3,multi_record_groups:1,singletons:2,covered_records:4,unresolved_records:0,total_records:4},groups:[{member_record_ids:['a','b'],singleton:false,status:'DRAFT'},{member_record_ids:['c'],singleton:true,status:'DRAFT'},{member_record_ids:['d'],singleton:true,status:'DRAFT'}],conflicts:[],conflict_count:0,pending_regions:[],source_decision_revision:state.workflow.decision_revision,source_assessment_id:'mock-assessment',allow_partial:body.allow_partial};
   return fulfill(operation('mock-preview','CLUSTER_PREVIEW','QUEUED'));
  }
  if(path===`${root}/operations/mock-preview`){state.operationReads++;if(state.operationReads===1)return fulfill(operation('mock-preview','CLUSTER_PREVIEW','RUNNING'));state.workflow.workflow_revision++;if(stale)state.workflow.decision_revision++;return fulfill(operation('mock-preview','CLUSTER_PREVIEW','COMPLETED',previewResult));}
  if(path===`${root}/finalize-decisions`&&method==='POST'){
   state.finalizes.push(clone(body));expect(body.preview_operation_id).toBe('mock-preview');expect(body.preview_token).toBe('mock-preview-token');
   state.workflow.workflow_revision++;state.workflow.workflow_state='CLUSTER_QUEUED';state.workflow.allowed_actions=[];
   state.workflow.latest_cluster_operation=operation('mock-cluster','CLUSTER','QUEUED');state.workflow.active_operation=state.workflow.latest_cluster_operation;
   return fulfill(state.workflow.latest_cluster_operation);
  }
  if(path===`${root}/clusters`){state.clusterReads++;return fulfill({items:[],total:0,next_cursor:null});}
  if(path===`${root}/operations/mock-cluster`)return fulfill(state.workflow.latest_cluster_operation);
  if(path===`${root}/operations/mock-bulk`)return fulfill(operation('mock-bulk','BULK_DECISIONS','COMPLETED'));
  unexpected.push(`${method} ${path}`);return fulfill({detail:'UNEXPECTED_MOCK_REQUEST'},501);
 });
 await page.goto(`${origin}/app/sessions/mock-handoff/decisions`);
 await expect(page.getByRole('table',{name:'Danh sách cặp bản ghi'}).locator('tbody tr')).toHaveCount(2);
 return {context,page,state,releaseSingle};
}

try{
 const main=await scenario({gateSingle:true});const {page,state}=main;
 await page.getByRole('button',{name:'Accept cặp soft-d',exact:true}).click();
 const reasonDialog=page.getByRole('dialog',{name:'Xác nhận Accept có mâu thuẫn'});
 await expect(reasonDialog).toBeVisible();await expect(reasonDialog.getByRole('button',{name:'Xác nhận Accept',exact:true})).toBeDisabled();expect(state.decisions).toHaveLength(0);
 await reasonDialog.getByRole('textbox',{name:'Lý do Accept'}).fill('   ');await expect(reasonDialog.getByRole('button',{name:'Xác nhận Accept',exact:true})).toBeDisabled();
 await reasonDialog.getByRole('textbox',{name:'Lý do Accept'}).fill('Đã đối chiếu giấy tờ và nguồn liên hệ');await page.screenshot({path:`${out}/soft-d-reason.png`});
 await reasonDialog.getByRole('button',{name:'Xác nhận Accept',exact:true}).click();await expect.poll(()=>state.decisions.length).toBe(1);
 await page.getByRole('button',{name:'Không gộp cặp keep-apart',exact:true}).click();
 await expect(page.getByRole('button',{name:'Hoàn tất phân loại và tạo nhóm',exact:true})).toBeDisabled();expect(state.decisions).toHaveLength(1);
 main.releaseSingle();await expect.poll(()=>state.decisions.length).toBe(2);
 await expect(page.getByRole('button',{name:'Hoàn tất phân loại và tạo nhóm',exact:true})).toBeEnabled();
 expect(state.decisions[0]).toMatchObject({decision:'merge',reason:'Đã đối chiếu giấy tờ và nguồn liên hệ'});expect(state.decisions[1].decision).toBe('no_merge');checks.push('soft_D_reason_blank_gate_and_preserved_single_queue','no_merge_distinct_serialized_queue_dispatch');
 await page.getByRole('button',{name:'Hoàn tất phân loại và tạo nhóm',exact:true}).click();
 const preview=page.getByRole('dialog',{name:'Xem trước các nhóm sẽ tạo'});
 await expect(preview.getByRole('status')).toBeVisible();await expect(preview.getByRole('button',{name:'Xác nhận tạo nhóm',exact:true})).toBeDisabled();expect(state.finalizes).toHaveLength(0);
 await expect(preview.getByRole('table',{name:'Nhóm dự kiến'})).toBeVisible();await expect(preview.getByText('a / b',{exact:true})).toBeVisible();await expect(preview.getByText(/4 \/ 4 bản ghi/)).toBeVisible();await expect(preview.getByRole('button',{name:'Xác nhận tạo nhóm',exact:true})).toBeEnabled();
 await page.screenshot({path:`${out}/prospective-groups.png`});checks.push('async_preview_progress_and_exact_members','workflow_only_revision_keeps_preview_valid');
 await preview.getByRole('button',{name:'Xác nhận tạo nhóm',exact:true}).click();await expect.poll(()=>state.finalizes.length).toBe(1);
 expect(state.finalizes[0]).toMatchObject({preview_operation_id:'mock-preview',preview_token:'mock-preview-token',allow_partial:false});
 await expect(page).toHaveURL(/\/clusters$/);await expect(page.getByText('Lần tạo nhóm gần nhất: Chờ tạo nhóm',{exact:true})).toBeVisible();await expect(page.getByText('Nhóm đang được tạo. Kết quả sẽ hiển thị khi tác vụ hoàn tất.',{exact:true})).toBeVisible();expect(state.clusterReads).toBe(0);
 await page.screenshot({path:`${out}/first-generation-pending.png`,fullPage:true});checks.push('preview_confirmation_binds_token_before_finalize','first_generation_pending_renders_progress_without_membership_read');await main.context.close();

 const drift=await scenario({stale:true});await drift.page.getByRole('button',{name:'Hoàn tất phân loại và tạo nhóm',exact:true}).click();
 const stalePreview=drift.page.getByRole('dialog',{name:'Xem trước các nhóm sẽ tạo'});await expect(stalePreview.getByRole('alert')).toHaveText(/Quyết định hoặc đánh giá đã thay đổi/);await expect(stalePreview.getByRole('button',{name:'Xác nhận tạo nhóm',exact:true})).toBeDisabled();expect(drift.state.finalizes).toHaveLength(0);
 await drift.page.screenshot({path:`${out}/stale-preview-lock.png`});checks.push('stale_decision_preview_lock_no_finalize_write');await drift.context.close();

 const bulk=await scenario();await bulk.page.getByRole('combobox',{name:'Phạm vi thao tác'}).selectOption('all');await bulk.page.getByRole('button',{name:'Accept theo phạm vi',exact:true}).click();
 const bulkDialog=bulk.page.getByRole('dialog',{name:'Xác nhận Accept',exact:true});await expect(bulkDialog.getByText('2 cặp có thể áp dụng',{exact:true})).toBeVisible();await expect(bulkDialog.getByRole('button',{name:'Xác nhận Accept',exact:true})).toBeDisabled();
 await bulkDialog.getByRole('textbox',{name:'Lý do Accept'}).fill('Đã kiểm tra các nguồn trong phạm vi');await bulkDialog.getByRole('button',{name:'Xác nhận Accept',exact:true}).click();await expect.poll(()=>bulk.state.bulk.length).toBe(1);expect(bulk.state.bulk[0]).toMatchObject({reason:'Đã kiểm tra các nguồn trong phạm vi',preview_token:'mock-bulk-token'});checks.push('bulk_required_reason_preserved_with_confirmed_preview_token');await bulk.context.close();
 expect(unexpected).toEqual([]);expect(errors).toEqual([]);checks.push('all_API_requests_mocked_zero_real_backend_requests','no_browser_runtime_errors');
 await writeFile(`${out}/browser.json`,JSON.stringify({mode:'isolated API mocks against Next production build',origin,checks,api_requests_intercepted:requests.length,real_backend_requests:0,unexpected,errors,requests},null,2));
 console.log(JSON.stringify({checks,artifact_directory:out,api_requests_intercepted:requests.length,real_backend_requests:0},null,2));
}catch(error){await writeFile(`${out}/failure.json`,JSON.stringify({checks,errors,unexpected,error:String(error),requests},null,2));throw error;}
finally{await browser.close();}
