// Live deployed UI + real reads; intercept only review writes to avoid changing the user's decisions.
import {chromium,expect as baseExpect} from '@playwright/test';
import {mkdir,writeFile} from 'node:fs/promises';
const expect=baseExpect.configure({timeout:60000});
const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
const out='artifacts/review-queue-20261012',checks={},errors=[],pairs=new Map();
await mkdir(out,{recursive:true});
let page,releaseFirst,writes=0,bulkWrites=0,activeBulk=false;
const firstGate=new Promise(resolve=>releaseFirst=resolve);
try{
 const context=await browser.newContext({viewport:{width:1440,height:1100}});
 await context.addCookies([{name:'masterone_session',value:process.env.PAIR_ASSESSMENT_TOKEN,url:'http://localhost:3000',httpOnly:true,sameSite:'Lax'}]);
 page=await context.newPage();page.setDefaultTimeout(60000);
 page.on('pageerror',error=>errors.push(error.message));
 page.on('response',async response=>{if(response.request().method()==='GET'&&response.url().includes('/workflow/pairs?')){const data=await response.json().catch(()=>({}));for(const p of data.items||[])pairs.set(p.pair_id,p);}});
 await page.route('**/workflow/pairs/*/decision',async route=>{
  writes++;const id=decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-2));
  if(writes===1)await firstGate;
  const pair=pairs.get(id);if(!pair)throw new Error('Expected visible pair in live read');
  await route.fulfill({json:{...pair,revision:pair.revision+1,decision:'ACCEPTED'}});
 });
 await page.route(/\/workflow\/?$/,async route=>{
  let response;
  try{response=await route.fetch();}catch{if(page.isClosed())return;throw new Error('Workflow verification read failed');}
  const w=await response.json();
  if(activeBulk){w.allowed_actions=['queue-decisions'];w.active_operation={operation_id:'ui-test-bulk',run_id:w.run_id,operation_type:'BULK_DECISIONS',status:'RUNNING',stage:'review',progress:{},error_code:null,result:{}};}
  await route.fulfill({response,json:w});
 });
 await page.route('**/workflow/bulk-decisions',async route=>{bulkWrites++;await route.fulfill({status:409,json:{detail:{code:'UI_TEST_UNEXPECTED_WRITE'}}});});
 const url=`http://localhost:3000/app/sessions/${process.env.PAIR_ASSESSMENT_RUN}/decisions`;
 await page.goto(url);
 const table=page.getByRole('table',{name:'Danh sách cặp bản ghi'});
 await expect(table.locator('tbody tr')).toHaveCount(10);
 const enabled=[];for(const button of await table.getByRole('button',{name:/^Accept cặp /}).all())if(await button.isEnabled())enabled.push(await button.getAttribute('aria-label'));
 expect(enabled.length).toBeGreaterThanOrEqual(2);
 const first=page.getByRole('button',{name:enabled[0],exact:true}),second=page.getByRole('button',{name:enabled[1],exact:true});
 await first.click();await expect(first).toBeDisabled();await expect(second).toBeEnabled();await second.click();
 await expect.poll(()=>writes).toBe(1);checks.unrelated_pair_stays_enabled=true;checks.writes_serialized=true;
 await page.getByRole('link',{name:'Tổng quan dữ liệu',exact:true}).click();
 releaseFirst();await expect.poll(()=>writes).toBe(2);checks.queue_continues_across_session_tabs=true;
 await page.getByRole('link',{name:'Phân loại các cặp bản ghi',exact:true}).click();await expect(page.getByRole('region',{name:'Hàng chờ duyệt'})).toBeVisible();
 await expect(page.getByRole('heading',{name:/Hàng chờ duyệt · 0/})).toBeVisible();
 activeBulk=true;
 // The next decision intention triggers a fresh workflow read showing an existing running bulk job.
 const next=table.getByRole('button',{name:/^Accept cặp /}).first();await next.click();
 await expect(page.getByText('Chờ gửi yêu cầu duyệt',{exact:true})).toBeVisible();
 await page.getByText('Công cụ đánh giá',{exact:true}).click();
 await expect(page.getByRole('button',{name:'Đánh giá lại bằng chứng'})).toBeDisabled();
 await page.getByText('Công cụ đánh giá',{exact:true}).click();
 await page.getByRole('combobox',{name:'Group',exact:true}).selectOption('A');
 await expect(table.locator('tbody tr')).toHaveCount(10);
 await page.getByRole('combobox',{name:'Phạm vi thao tác'}).selectOption('group');
 await expect(page.getByRole('button',{name:'Accept theo phạm vi'})).toBeEnabled();
 await page.getByRole('button',{name:'Accept theo phạm vi'}).click();
 await expect(page.getByRole('button',{name:'Xác nhận Accept'})).toBeEnabled();await page.getByRole('button',{name:'Xác nhận Accept'}).click();
 checks.group_can_queue_during_bulk=true;
 const subgroup=await page.getByRole('combobox',{name:'SubGroup'}).locator('option').nth(1).getAttribute('value');
 await page.getByRole('combobox',{name:'SubGroup'}).selectOption(subgroup);
 await page.getByRole('combobox',{name:'Phạm vi thao tác'}).selectOption('subgroup');
 await expect(page.getByRole('button',{name:'Accept theo phạm vi'})).toBeEnabled();await page.getByRole('button',{name:'Accept theo phạm vi'}).click();
 await expect(page.getByRole('button',{name:'Xác nhận Accept'})).toBeEnabled();await page.getByRole('button',{name:'Xác nhận Accept'}).click();
 checks.subgroup_can_queue_during_bulk=true;
 expect(bulkWrites).toBe(0);expect(writes).toBe(2);checks.no_parallel_bulk_submission=true;
 await page.screenshot({path:`${out}/desktop.png`,fullPage:true});
 await page.getByRole('region',{name:'Hàng chờ duyệt'}).locator('summary').click();
 while(await page.getByRole('button',{name:/^Hủy yêu cầu /}).count())await page.getByRole('button',{name:/^Hủy yêu cầu /}).first().click();
 await expect(page.getByRole('heading',{name:/Hàng chờ duyệt · 0/})).toBeVisible();checks.unsent_requests_cancelled=true;
 expect(errors).toEqual([]);checks.no_browser_errors=true;
 await writeFile(`${out}/browser.json`,JSON.stringify({checks,review_writes_intercepted:true,real_review_writes:0},null,2));console.log(JSON.stringify(checks));
}finally{releaseFirst();if(page)await page.unrouteAll({behavior:'ignoreErrors'});await browser.close();}
