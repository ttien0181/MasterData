// Local synthetic fixtures, genuine API/PostgreSQL/worker/Edge; no certificate deployment.
import {chromium,request,expect as baseExpect} from '@playwright/test';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {randomUUID} from 'node:crypto';
import {spawnSync} from 'node:child_process';
const expect=baseExpect.configure({timeout:25000});
if(!process.argv[2])throw new Error('Pass local synthetic STEWARD account JSON');
const account=JSON.parse(await readFile(process.argv[2],'utf8'));
const origin='http://localhost:3000',api=await request.newContext({baseURL:'http://localhost:8001',extraHTTPHeaders:{Origin:origin}});
async function checked(r){if(!r.ok())throw new Error(r.status()+' '+await r.text());return r.json();}
const auth=await checked(await api.post('/api/v1/auth/login',{data:account})),headers={'X-CSRF-Token':auth.csrf_token};
const out=resolve('artifacts/pair-automation-stage4');await mkdir(out,{recursive:true});
const checks=[],runs=[],errors=[];
let workerStopped=false;
function workerCommand(args){const result=spawnSync('docker',['compose',...args],{cwd:resolve('..'),encoding:'utf8',timeout:45000});if(result.status!==0)throw new Error('Local worker command failed: '+(result.error?.message||result.stderr));}
const browser=await chromium.launch({headless:true,executablePath:process.env.P119_BROWSER_PATH||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
const page=await browser.newPage({viewport:{width:1440,height:1100}});page.on('pageerror',e=>errors.push(e.message));
const root=id=>`/api/v1/mdm/runs/${id}/workflow`;
const state=id=>api.get(root(id)).then(checked);
const mutation=w=>({request_id:randomUUID(),expected_workflow_revision:w.workflow_revision,expected_assessment_id:w.assessment_id??null});
async function until(fn,label){const end=Date.now()+90000;while(Date.now()<end){const x=await fn();if(x)return x;await new Promise(r=>setTimeout(r,200));}throw new Error('Timeout: '+label);}
async function create(name,contacts){const csv='source_record_id,source_system,entity_type,name,phone,email,dob,address\n'+['CRM','ERP'].map((source,i)=>`${i+1},${source},PERSON,Nguyen Van An,${contacts?'0912345678':''},${contacts?'an.nguyen@example.net':''},1990-05-15,"123 Nguyen Trai, Ha Noi"`).join('\n')+'\n';const r=await checked(await api.post('/api/v1/mdm/runs',{headers,multipart:{workflow_version:'2',files:{name:name+'.csv',mimeType:'text/csv',buffer:Buffer.from(csv)}}}));runs.push(r.run_id);const w=await until(async()=>{const w=await state(r.run_id);return w.workflow_state==='DATA_READY'&&w;},'prepare');await checked(await api.post(root(r.run_id)+'/start-decide',{headers,data:mutation(w)}));await until(async()=>{const w=await state(r.run_id);return w.workflow_state==='DECIDE_REVIEW'&&!w.active_operation&&w;},'decide');return r.run_id;}
async function shadow(id){await page.getByRole('button',{name:'Chạy thử tự động hóa',exact:true}).click();return until(async()=>{const w=await state(id);return !w.active_operation&&w.latest_automation_operation?.status==='COMPLETED'&&w.latest_automation_operation.operation_type==='AUTOMATION_SHADOW'&&w;},'shadow');}
try {
 await page.goto(origin+'/login');await page.getByLabel('Email',{exact:true}).fill(account.email);await page.getByLabel('Mật khẩu',{exact:true}).fill(account.password);await page.getByRole('button',{name:'Đăng nhập',exact:true}).click();await page.waitForURL('**/app');
 const id=await create('Stage4-ControlledShadow',true);await page.goto(`${origin}/app/sessions/${id}/decisions`);await expect(page.getByRole('region',{name:'Tự động hóa có kiểm soát'})).toBeVisible();
 const w=await shadow(id),plan=w.latest_automation_operation.result;
 expect(plan.candidate_components).toBe(1);expect(plan.can_apply).toBe(false);expect(plan.qualified_components).toBe(0);
 await expect(page.getByText('Chưa có chứng nhận chất lượng cho vùng tự động.')).toBeVisible();await expect(page.getByRole('button',{name:'Áp dụng tự động',exact:true})).toBeDisabled();
 const pairs=await checked(await api.get(root(id)+'/pairs'));expect(pairs.items.every(p=>p.decision==='UNDECIDED'&&!p.automation)).toBe(true);
 const refused=await api.post(root(id)+'/automation/apply',{headers,data:{...mutation(w),expected_decision_revision:w.decision_revision,preview_operation_id:w.latest_automation_operation.operation_id,preview_token:plan.preview_token}});expect(refused.status()).toBe(409);expect((await refused.json()).detail.code).toBe('AUTOMATION_NOT_CERTIFIED');
 await page.screenshot({path:resolve(out,'uncertified-shadow.png'),fullPage:true});await writeFile(resolve(out,'shadow-operation.json'),JSON.stringify(w.latest_automation_operation,null,2));
 checks.push({check:'strict shadow is read-only; uncertified HTTP/UI application denied',candidate_components:1,automatic_pairs:0});
 await page.getByRole('button',{name:'Dừng tự động hóa phiên này',exact:true}).click();await until(async()=>{const w=await state(id);return w.automation_stopped&&w;},'persistent stop');await expect(page.getByText('Đã dừng tự động hóa phiên này.')).toBeVisible();await page.reload();await expect(page.getByText('Đã dừng tự động hóa phiên này.')).toBeVisible();
 checks.push({check:'stop persists across browser reload and keeps saved decisions'});
 const recovered=await create('Stage4-RecoveryStaysManual',false);await page.goto(`${origin}/app/sessions/${recovered}/decisions`);const rw=await shadow(recovered);expect(rw.latest_automation_operation.result.candidate_components).toBe(0);const rp=await checked(await api.get(root(recovered)+'/pairs'));expect(rp.items[0].baseline.review_support.kind).toBe('NAME_FULL_DOB');expect(rp.items[0].decision).toBe('UNDECIDED');
 checks.push({check:'stage3 review recovery never becomes an automatic candidate',automatic_pairs:0});
 // An offline worker must not leave manual review locked behind a stopped task.
 workerCommand(['stop','worker']);workerStopped=true;
 const beforeStop=await state(recovered);
 const queued=await checked(await api.post(root(recovered)+'/automation/shadow',{headers,data:{...mutation(beforeStop),expected_decision_revision:beforeStop.decision_revision}}));
 expect(queued.status).toBe('QUEUED');await page.reload();await expect(page.getByRole('button',{name:'Dừng tự động hóa phiên này',exact:true})).toBeEnabled();await page.getByRole('button',{name:'Dừng tự động hóa phiên này',exact:true}).click();
 await until(async()=>{const w=await state(recovered);return w.automation_stopped&&!w.active_operation&&w;},'offline immediate stop');
 const cancelled=await checked(await api.get(root(recovered)+'/operations/'+queued.operation_id));expect(cancelled.status).toBe('CANCELLED');
 const manual=page.getByRole('button',{name:/^Không gộp cặp /}).first();await expect(manual).toBeEnabled();await manual.click();await until(async()=>{const pairs=await checked(await api.get(root(recovered)+'/pairs'));return pairs.items[0].decision==='REJECTED';},'manual review while worker remains offline');
 await page.screenshot({path:resolve(out,'offline-stop-manual.png'),fullPage:true});checks.push({check:'offline queued worker is cancelled immediately and manual review resumes before restart'});
 workerCommand(['up','-d','worker']);workerStopped=false;
 expect(errors).toEqual([]);await writeFile(resolve(out,'result.json'),JSON.stringify({status:'PASS',checks,runs,pageErrors:errors,scope:'local synthetic fixtures; no production certification/activation'},null,2));console.log(JSON.stringify({status:'PASS',checks}));
}catch(e){await page.screenshot({path:resolve(out,'failure.png'),fullPage:true});await writeFile(resolve(out,'result.json'),JSON.stringify({status:'FAIL',checks,runs,pageErrors:errors,error:e.message},null,2));throw e;}
finally{if(workerStopped)workerCommand(['up','-d','worker']);await browser.close();await api.dispose();}
