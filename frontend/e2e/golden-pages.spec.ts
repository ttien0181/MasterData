import {expect,test,type Page} from '@playwright/test';
import path from 'node:path';

async function fixture(page:Page,finalized=false,allConfirmed=false){
 let revision=3,goldenRevision=1,exportAttempts=0;
 const records=Array.from({length:32},(_,i)=>({id:`c${String(i+1).padStart(2,'0')}`,name:`Nguyễn An ${i+1}`,confirmed:finalized||allConfirmed||i<4}));
 const writes:{path:string;body:any}[]=[],errors:string[]=[];
 page.on('pageerror',error=>errors.push(error.message));
 const generation=()=>({revision:1,source_decision_revision:1,stale:false,read_only:finalized,publication_status:'COMPLETE',clusters:32,multi_record_groups:32,singletons:0,covered_records:64,unresolved_records:0,total_records:64});
 const fields=(name:string)=>({name:{value:name,source_system:'CRM',source_record_id:'a',provenance:'SOURCE_CHOICE'},phone:{value:'0901234567',source_system:'CRM'},email:{value:'an@example.test',source_system:'CRM'}});
 const golden=(id:string)=>{const row=records.find(r=>r.id===id)!;return {cluster_id:id,cluster_revision:1,golden_revision:goldenRevision,revision,read_only:finalized,generation:generation(),status:finalized?'FINALIZED':row.confirmed?'CONFIRMED':'DRAFT',fields:fields(row.name),selections:{},alternatives:{name:[{source_record_id:`${id}-a`,source_system:'CRM',value:row.name,auto_rule_available:true}]}};};
 await page.route('**/api/v1/**',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname;let body:any={};
  if(req.method()!=='GET')writes.push({path:p,body:req.postDataJSON()});
  if(p.endsWith('/auth/me'))body={id:'golden-pages-user',email:'golden@example.test',role:'STEWARD',csrf_token:'synthetic',capabilities:['read','edit_golden','export_data']};
  else if(p.endsWith('/workflow'))body={run_id:'r',workflow_version:2,workflow_state:finalized?'FINALIZED':'GOLDEN_REVIEW',workflow_revision:revision,decision_revision:1,cluster_revision:1,golden_revision:goldenRevision,cluster_result:generation(),counts:{records:{value:64,unit:'records'}},allowed_actions:finalized?['export']:['save-golden','confirm-golden',...(records.every(r=>r.confirmed)?['finalize-golden']:[])],blockers:records.some(r=>!r.confirmed)?[{code:'UNCONFIRMED_GOLDEN',count:records.filter(r=>!r.confirmed).length}]:[],active_operation:null,retryable_operation:null};
  else if(p.endsWith('/golden/index')){
   const status=url.searchParams.get('status'),q=(url.searchParams.get('q')||'').toLowerCase();
   const matches=records.filter(r=>(status==='review'?!r.confirmed:status==='confirmed'?r.confirmed:true)&&(!q||r.name.toLowerCase().includes(q)||r.id.includes(q)));
   const cursor=url.searchParams.get('cursor'),remaining=matches.filter(r=>!cursor||r.id>cursor),limit=Number(url.searchParams.get('limit')||25),items=remaining.slice(0,limit);
   body={items:items.map(r=>({cluster_id:r.id,display_name:r.name,member_count:2,phone:'0901234567',email:'an@example.test',status:finalized?'FINALIZED':r.confirmed?'CONFIRMED':'DRAFT',review_fields:[]})),total:matches.length,next_cursor:remaining.length>limit?items.at(-1)!.id:null,summary:{total:32,confirmed:records.filter(r=>r.confirmed).length,needs_review:records.filter(r=>!r.confirmed).length},generation:generation()};
  }else if(/\/clusters\/[^/]+\/golden\/confirm$/.test(p)){
   const id=p.split('/').at(-3)!;records.find(r=>r.id===id)!.confirmed=true;revision++;goldenRevision++;body=golden(id);
  }else if(/\/clusters\/[^/]+\/golden$/.test(p)){
   const id=p.split('/').at(-2)!;
   if(req.method()==='PUT'){const change=req.postDataJSON().selections.name;if(change?.selection_mode==='MANUAL_OVERRIDE')records.find(r=>r.id===id)!.name=change.value;revision++;goldenRevision++;}
   body=golden(id);
  }else if(/\/clusters\/[^/]+$/.test(p)){
   const id=p.split('/').at(-1)!,row=records.find(r=>r.id===id)!;
   body={cluster_id:id,display_name:row.name,member_count:2,singleton:false,status:'REVIEW_APPROVED',member_record_ids:[`${id}-a`,`${id}-b`],generation:generation(),members:{items:['a','b'].map((suffix,i)=>({record_id:`${id}-${suffix}`,source_system:i?'ERP':'CRM',raw_fields:{name:i?row.name.toUpperCase():row.name,phone:i?'0910000000':'0901234567',email:'an@example.test'},normalized_fields:{},extra_fields:{}})),total:2,next_cursor:null},pair_evidence:{items:[],total:0,next_cursor:null},llm_stats:{},workflow_context:{},evidence:{items:[],total:0,next_cursor:null}};
  }else if(p.endsWith('/finalize-golden')){finalized=true;revision++;body={};}
  else if(p.endsWith('/exports'))body={items:exportAttempts>=2?[{id:'export1',created_at:'2026-10-17T05:00:00Z',row_count:32}]:[]};
  else if(p.endsWith('/previews'))body={preview_id:'preview1',estimated_output_rows:32,result_hash:'synthetic',expires_at:new Date(Date.now()+900000).toISOString()};
  else if(p.endsWith('/execute')){
   exportAttempts++;
   if(exportAttempts===1){await route.fulfill({status:503,json:{detail:'Server disconnected during CSV export'}});return;}
   await route.fulfill({status:200,contentType:'text/csv',body:'name\nSynthetic\n'});return;
  }
  await route.fulfill({json:body});
 });
 return {writes,errors};
}
const nav=(page:Page)=>page.getByRole('navigation',{name:'Các bước trong phiên'});

test('three Golden pages stay separate and review retains filters, pagination and saved choices',async({page})=>{
 const mock=await fixture(page);
 await page.setViewportSize({width:1440,height:1000});
 await page.goto('/app/sessions/r/golden');
 await expect(page.getByRole('heading',{name:'Tổng quan & tiến độ',exact:true})).toBeVisible();
 await expect(page.getByRole('progressbar',{name:'Golden đã xác nhận'})).toHaveAttribute('value','4');
 await expect(page.getByRole('table')).toHaveCount(0);
 await page.screenshot({path:path.resolve('../artifacts/golden-pages-20261017/overview.png'),fullPage:true});
 await page.getByRole('link',{name:'Tiếp tục duyệt →'}).click();
 await expect(page).toHaveURL(/\/golden\/records\?status=review$/);
 await expect(page.getByRole('table',{name:'Danh sách Golden'})).toBeVisible();
 await expect(page.getByRole('button',{name:'Cần kiểm tra (28)'})).toHaveAttribute('aria-pressed','true');
 const pager=page.getByRole('navigation',{name:'Phân trang Golden'});
 await pager.getByRole('button',{name:'Trang tiếp'}).click();
 await expect(page.getByRole('button',{name:'Xem và duyệt Nguyễn An 30'})).toBeVisible();
 await nav(page).getByRole('link',{name:'Tổng quan & tiến độ',exact:true}).click();
 await nav(page).getByRole('link',{name:'Duyệt Golden',exact:true}).click();
 await expect(page.getByText('Trang 2',{exact:true})).toBeVisible();
 await pager.getByRole('button',{name:'Trang trước'}).click();
 await page.getByRole('button',{name:'Xem và duyệt Nguyễn An 5',exact:true}).click();
 const popup=page.getByRole('dialog');
 await expect(popup.getByRole('table',{name:'Đối chiếu thành viên nhóm'})).toBeVisible();
 await popup.locator('summary').filter({hasText:'Họ tên'}).click();
 await popup.getByRole('textbox',{name:'Chỉnh tay name'}).fill('Tên đã chọn');
 await popup.getByRole('button',{name:'Lưu lựa chọn Golden'}).click();
 await expect(popup.getByRole('button',{name:'Xác nhận Golden',exact:true})).toBeEnabled();
 await popup.getByRole('button',{name:'Xác nhận Golden',exact:true}).click();
 await expect(popup.getByText('Golden đã xác nhận',{exact:true})).toBeVisible();
 await page.screenshot({path:path.resolve('../artifacts/golden-pages-20261017/review-popup.png')});
 await popup.getByRole('button',{name:'Nhóm tiếp theo →'}).click();
 await expect(page.getByRole('dialog',{name:'Duyệt Golden · Nguyễn An 6'})).toBeVisible();
 await page.getByRole('button',{name:'Đóng chi tiết nhóm'}).click();
 await page.getByRole('searchbox',{name:'Tìm Golden'}).fill('c30');
 await page.getByRole('button',{name:'Tìm',exact:true}).click();
 await expect(page.getByRole('table',{name:'Danh sách Golden'}).getByRole('row')).toHaveCount(2);
 await page.screenshot({path:path.resolve('../artifacts/golden-pages-20261017/review.png'),fullPage:true});
 expect(mock.writes.filter(w=>w.path.endsWith('/golden'))).toHaveLength(1);
 expect(mock.writes.filter(w=>w.path.endsWith('/confirm'))).toHaveLength(1);
 expect(mock.errors).toEqual([]);
});

test('finalized is distinct from export success, retry is idempotent and only committed files can be downloaded',async({page})=>{
 const mock=await fixture(page,true);
 await page.setViewportSize({width:1440,height:1000});
 await page.goto('/app/sessions/r/golden/export');
 await expect(page.locator('#golden-export').getByText('Phiên đã chốt',{exact:true})).toBeVisible();
 await expect(page.getByText('Chưa có file CSV được lưu thành công.')).toBeVisible();
 await expect(page.getByRole('button',{name:'Tải CSV',exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'Xem trước CSV'}).click();
 await page.getByRole('button',{name:'Xuất CSV',exact:true}).click();
 await expect(page.locator('#golden-export').getByRole('alert')).toContainText('Xuất CSV thất bại');
 await expect(page.getByRole('button',{name:'Tải CSV',exact:true})).toHaveCount(0);
 await page.screenshot({path:path.resolve('../artifacts/golden-pages-20261017/export-failed.png'),fullPage:true});
 await page.getByRole('button',{name:'Thử xuất lại'}).click();
 await expect(page.getByText('Đã tạo CSV thành công',{exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Tải CSV',exact:true})).toBeVisible();
 const requests=mock.writes.filter(w=>w.path.endsWith('/execute'));
 expect(requests).toHaveLength(2);expect(requests[0].body.execute_request_id).toBe(requests[1].body.execute_request_id);
 await page.screenshot({path:path.resolve('../artifacts/golden-pages-20261017/export-success.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 await page.screenshot({path:path.resolve('../artifacts/golden-pages-20261017/export-mobile.png'),fullPage:true});
 expect(mock.errors).toEqual([]);
});

test('read-only Golden popup contains keyboard focus on compact fields and fits a phone screen',async({page})=>{
 const mock=await fixture(page,true);
 await page.setViewportSize({width:390,height:844});
 await page.goto('/app/sessions/r/golden/records?status=all&cluster=c32');
 const popup=page.getByRole('dialog');
 await expect(popup).toBeVisible();
 await expect(popup.getByRole('button',{name:'Xác nhận Golden',exact:true})).toBeDisabled();
 for(let i=0;i<20;i++){
  await page.keyboard.press('Tab');
  expect(await popup.evaluate(dialog=>dialog.contains(document.activeElement))).toBe(true);
 }
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 await page.screenshot({path:path.resolve('../artifacts/golden-pages-20261017/review-mobile.png')});
 await page.keyboard.press('Escape');
 await expect(popup).toHaveCount(0);
 expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
});

test('ready data can be finalized explicitly before CSV export is enabled',async({page})=>{
 const mock=await fixture(page,false,true);
 await page.goto('/app/sessions/r/golden/export');
 await expect(page.getByRole('button',{name:'Xem trước CSV'})).toBeDisabled();
 page.once('dialog',dialog=>dialog.accept());
 await page.getByRole('button',{name:'Chốt phiên',exact:true}).click();
 await expect(page.getByRole('button',{name:'Đã chốt phiên',exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Xem trước CSV'})).toBeEnabled();
 expect(mock.writes.filter(write=>write.path.endsWith('/finalize-golden'))).toHaveLength(1);
 expect(mock.errors).toEqual([]);
});

test('cache audit: three Golden pages and popup reuse reads after three minutes while export history refreshes',async({page})=>{
 await page.clock.install();
 const mock=await fixture(page);
 const reads:string[]=[];
 page.on('request',request=>{if(request.method()==='GET'&&/\/workflow\/golden\/index|\/exports$|\/clusters\//.test(request.url()))reads.push(request.url());});
 await page.goto('/app/sessions/r/golden');
 await page.evaluate(()=>{(window as unknown as Record<string,string>).__cacheAuditDocument='same-document';});
 await expect(page.getByRole('progressbar',{name:'Golden đã xác nhận'})).toBeVisible();
 await nav(page).getByRole('link',{name:'Duyệt Golden',exact:true}).click();
 await page.getByRole('searchbox',{name:'Tìm Golden'}).fill('c30');
 await page.getByRole('button',{name:'Tìm',exact:true}).click();
 await page.getByRole('button',{name:'Xem và duyệt Nguyễn An 30',exact:true}).click();
 await expect(page.getByRole('dialog').getByRole('table',{name:'Đối chiếu thành viên nhóm'})).toBeVisible();
 await page.getByRole('dialog').getByRole('checkbox',{name:'Chỉ hiện trường khác nhau'}).check();
 await page.getByRole('button',{name:'Đóng chi tiết nhóm'}).click();
 await nav(page).getByRole('link',{name:'Chốt & xuất dữ liệu',exact:true}).click();
 await expect(page.getByText('Chưa có file CSV được lưu thành công.')).toBeVisible();
 await nav(page).getByRole('link',{name:'Tổng quan & tiến độ',exact:true}).click();
 const before=[...reads];
 await page.clock.fastForward(180000);
 await nav(page).getByRole('link',{name:'Duyệt Golden',exact:true}).click();
 await expect(page.getByRole('searchbox',{name:'Tìm Golden'})).toHaveValue('c30');
 await page.getByRole('button',{name:'Xem và duyệt Nguyễn An 30',exact:true}).click();
 await expect(page.getByRole('dialog').getByRole('table',{name:'Đối chiếu thành viên nhóm'})).toBeVisible();
 const popupDiffFilterPreserved=await page.getByRole('dialog').getByRole('checkbox',{name:'Chỉ hiện trường khác nhau'}).isChecked();
 await page.getByRole('button',{name:'Đóng chi tiết nhóm'}).click();
 await nav(page).getByRole('link',{name:'Chốt & xuất dữ liệu',exact:true}).click();
 await expect.poll(()=>reads.filter(url=>url.endsWith('/exports')).length).toBe(2);
 expect(reads.filter(url=>!url.endsWith('/exports'))).toEqual(before.filter(url=>!url.endsWith('/exports')));
 expect(await page.evaluate(()=>(window as unknown as Record<string,string>).__cacheAuditDocument)).toBe('same-document');
 expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
 const report={wait_ms:180000,before_reads:before.length,after_reads:reads.length,additional_reads:reads.slice(before.length),extra_golden_index_or_popup_reads:0,popup_diff_filter_preserved:popupDiffFilterPreserved,hard_reload:false,api:'synthetic fixtures; no live writes'};
 await test.info().attach('cache-audit',{body:JSON.stringify(report,null,2),contentType:'application/json'});
});
