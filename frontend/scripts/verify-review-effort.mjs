// Real local API/worker/browser smoke, using synthetic records only.
// From frontend/: node scripts/verify-review-effort.mjs <local-steward-account.json>
// Account JSON contains email/password; it is never copied to reports.
import {chromium,request,expect as baseExpect} from '@playwright/test';
import {resolve} from 'node:path';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
const expect=baseExpect.configure({timeout:20000});
if(!process.argv[2])throw new Error('Pass the local synthetic STEWARD account JSON path');
const account=JSON.parse(await readFile(process.argv[2],'utf8'));
const origin='http://localhost:3000',apiBase='http://localhost:8001';
const api=await request.newContext({baseURL:apiBase,extraHTTPHeaders:{Origin:origin}});
async function checked(response){if(!response.ok())throw new Error(response.status()+' '+await response.text());return response.json();}
const user=await checked(await api.post('/api/v1/auth/login',{data:account}));
const headers={'X-CSRF-Token':user.csrf_token};
const out=resolve('artifacts/review-effort-stage2');await mkdir(out,{recursive:true});
const runs=[],results=[],errors=[];
async function state(id){return checked(await api.get(`/api/v1/mdm/runs/${id}/workflow`));}
async function summary(id){return checked(await api.get(`/api/v1/mdm/runs/${id}/workflow/pairs/summary`));}
async function until(fn,label){const deadline=Date.now()+90000;while(Date.now()<deadline){const value=await fn();if(value)return value;await new Promise(r=>setTimeout(r,200));}throw new Error('Timeout: '+label);}
async function create(name,csv){
 const made=await checked(await api.post('/api/v1/mdm/runs',{headers,multipart:{workflow_version:'2',files:{name:name+'.csv',mimeType:'text/csv',buffer:Buffer.from(csv)}}}));
 const id=made.run_id;runs.push(id);
 const w=await until(async()=>{const w=await state(id);return w.workflow_state==='DATA_READY'&&w;},'prepare');
 await checked(await api.post(`/api/v1/mdm/runs/${id}/workflow/start-decide`,{headers,data:{request_id:randomUUID(),expected_workflow_revision:w.workflow_revision,expected_assessment_id:w.assessment_id??null}}));
 await until(async()=>{const w=await state(id);return w.workflow_state==='DECIDE_REVIEW'&&!w.active_operation&&w;},'decide');
 return id;
}
const browser=await chromium.launch({headless:true,...(process.env.P119_BROWSER_PATH?{executablePath:process.env.P119_BROWSER_PATH}:{executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'})});
const page=await browser.newPage({viewport:{width:1440,height:1600}});page.on('pageerror',e=>errors.push(e.message));
const header='source_record_id,source_system,entity_type,name,phone,email,address\n';
const sourceSystems=['CRM','BILL','ERP','CSV','CRM'];
try{
 await page.goto(origin+'/login');await page.getByLabel('Email',{exact:true}).fill(account.email);await page.getByLabel('Mật khẩu',{exact:true}).fill(account.password);await page.getByRole('button',{name:'Đăng nhập',exact:true}).click();await page.waitForURL('**/app');
 for(const memberCount of [4,5])for(const mode of ['individual','cluster']){
  const pairCount=memberCount*(memberCount-1)/2;
  const csv=header+sourceSystems.slice(0,memberCount).map((source,i)=>`${i+1},${source},PERSON,Nguyen Van An,0901234567,an@example.com,123 Hanoi\n`).join('')+`${memberCount+1},CRM,PERSON,Tran Thi Binh,0987654321,binh@example.com,123 Hanoi\n`;
  const id=await create('Stage2-'+memberCount+'-'+mode,csv);const initial=await summary(id);
  expect(initial.total).toBe(pairCount+memberCount);expect(initial.insufficient).toBe(memberCount);expect(initial.needs_review).toBe(pairCount);
  await page.goto(`${origin}/app/sessions/${id}/decisions`);
  await expect(page.getByRole('combobox',{name:'Quyết định',exact:true})).toHaveValue('REQUIRED');
  await expect(page.getByRole('button',{name:/^Accept cặp /})).toHaveCount(pairCount);
  const start=performance.now();let decisionClicks=0,reviewSurfaceSourceRows=0;
  if(mode==='individual'){
   reviewSurfaceSourceRows=await page.getByRole('table',{name:'Danh sách cặp bản ghi'}).locator('tbody tr').count()*2;
   for(let i=1;i<=pairCount;i++){
    await page.getByRole('button',{name:/^Accept cặp /}).filter({visible:true}).first().click();decisionClicks++;
    await until(async()=>{const s=await summary(id);return s.accepted===i&&s;},'individual accepted '+i);
    if(i<pairCount)await expect(page.getByRole('button',{name:/^Accept cặp /}).filter({visible:true}).first()).toBeEnabled();
   }
  }else{
   await page.getByRole('button',{name:/^Duyệt cụm từ cặp /}).first().click();decisionClicks++;
   const members=page.getByRole('table',{name:'Thành viên cụm'});
   await expect(members.locator('tbody tr')).toHaveCount(memberCount);reviewSurfaceSourceRows=await members.locator('tbody tr').count();
   await page.screenshot({path:resolve(out,'cluster-review.png'),fullPage:true});
   await page.getByRole('button',{name:`Accept ${pairCount} cặp trong cụm`,exact:true}).click();decisionClicks++;
   await page.getByRole('button',{name:'Xác nhận Accept',exact:true}).click();decisionClicks++;
   await until(async()=>{const s=await summary(id);return s.accepted===pairCount&&s.needs_review===0&&s;},'cluster accepted');
  }
  const scriptedReviewMs=Math.round(performance.now()-start);
  await expect(page.getByRole('button',{name:'Hoàn tất phân loại và tạo nhóm',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Hoàn tất phân loại và tạo nhóm',exact:true}).click();
  await expect(page.getByRole('table',{name:'Nhóm dự kiến'})).toBeVisible();
  await page.getByRole('button',{name:'Xác nhận tạo nhóm',exact:true}).click();
  await until(async()=>{const w=await state(id);return w.workflow_state==='GOLDEN_REVIEW'&&w;},'publish');
  const groups=await checked(await api.get(`/api/v1/mdm/runs/${id}/workflow/clusters`));
  expect(groups.total).toBe(2);
  const sizes=groups.items.map(c=>c.member_count??c.member_record_ids?.length??c.members?.length).sort((a,b)=>a-b);
  expect(sizes).toEqual([1,memberCount]);
  // The API group contract exposes member_record_ids; inspect persisted pairs too.
  const pairs=await checked(await api.get(`/api/v1/mdm/runs/${id}/workflow/pairs?limit=100`));
  expect(pairs.items.filter(p=>p.decision==='ACCEPTED')).toHaveLength(pairCount);
  expect(pairs.items.filter(p=>p.baseline.decision==='INSUFFICIENT_EVIDENCE').every(p=>p.decision==='UNDECIDED')).toBe(true);
  results.push({mode,memberCount,decisionClicks,reviewSurfaceSourceRows,scriptedReviewMs,groups:groups.total,sizes,mandatoryInitially:pairCount,lowEvidenceRetained:memberCount});
 }
 const weak=await create('Stage2-KnownWeakDuplicate',header+'1,CRM,PERSON,Nguyen Van An,,,123 Hanoi\n2,BILL,PERSON,Nguyen Van An,,,123 Hanoi\n');
 const ws=await summary(weak);expect(ws.needs_review).toBe(0);expect(ws.insufficient).toBe(1);
 // Fixture truth is consulted after matching. No gold field was uploaded.
 results.push({mode:'known-weak-duplicate-diagnostic',sameEntityCandidatesOutsideRequired:1,retainedInsufficient:1});
 expect(errors).toEqual([]);
 await writeFile(resolve(out,'result.json'),JSON.stringify({status:'PASS',results,runs,pageErrors:errors,durationMeaning:'scripted browser duration; not a human reading-time study',rowMetricMeaning:'source rows on the chosen review surface; excludes the pair list seen before opening a cluster; not total exposure or measured human reading'},null,2));
 console.log(JSON.stringify({status:'PASS',results,runs}));
}catch(error){await page.screenshot({path:resolve(out,'failure.png'),fullPage:true});await writeFile(resolve(out,'result.json'),JSON.stringify({status:'FAIL',results,runs,pageErrors:errors,error:error.message},null,2));throw error;}
finally{await browser.close();await api.dispose();}
