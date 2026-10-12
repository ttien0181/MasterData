import { expect, test, type Page } from '@playwright/test';

// Production client/query stores with synthetic HTTP responses. No live session writes.
async function fixture(page: Page) {
  const reads: { group: string; offset: number; subgroup: string; revision: number; limit: number }[] = [];
  const writes: string[] = [], errors: string[] = [];
  let revision = 1, denied = false, delayPairs = false;
  let release!: () => void;
  let gate = Promise.resolve();
  page.on('pageerror', error => errors.push(error.message));
  const pair = (group: string, index: number) => ({pair_id:`${group}-${index}`,revision:denied&&index===0?1:0,decision:denied&&index===0?'NON_MATCH':'UNDECIDED',score_unit:'p119_rule_v1',eligibility:{manual_allowed:true,auto_allowed:false,blocked_reasons:[]},baseline:{id:`${group}-${index}`,left_id:`l-${group}-${index}`,right_id:`r-${group}-${index}`,primary_group:group==='ALL'?'A':group,subgroup:'A1_NAME_PHONE',decision:'REVIEW',score:5,field_evidence:[]},left_record:{record_id:`l-${group}-${index}`,source_system:'CRM',raw_fields:{name:`${group} person ${index}`,phone:'0901234567'},normalized_fields:{},extra_fields:{}},right_record:{record_id:`r-${group}-${index}`,source_system:'BILL',raw_fields:{name:`${group} other ${index}`},normalized_fields:{},extra_fields:{}}});
  await page.route('**/api/v1/**', async route => {
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    let body: unknown = {};
    if(path.endsWith('/auth/me')) body={id:'group-cache-owner',email:'group-cache@example.test',role:'STEWARD',csrf_token:'synthetic',capabilities:['read','approve_pair']};
    else if(path.endsWith('/workflow')) body={run_id:'r',workflow_version:2,workflow_state:'DECIDE_REVIEW',workflow_revision:revision+3,decision_revision:revision,cluster_revision:0,golden_revision:0,counts:{records:{value:60,unit:'records'},pairs:{value:59,unit:'pairs'}},allowed_actions:['decide-pair','bulk-decisions'],blockers:[],active_operation:null,retryable_operation:null};
    else if(path.endsWith('/pairs/summary')) body={total:59,needs_review:denied?58:59,accepted:0,denied:denied?1:0,kept_separate:0,deferred:0,blocked:denied?1:0,manual_available:denied?58:59,auto_available:0,decision_revision:revision,groups:[{key:'A',total:34,subgroups:[{key:'A1_NAME_PHONE',total:34}]},{key:'D',total:25,subgroups:[]},{key:'C',total:0,subgroups:[]}]};
    else if(path.endsWith('/pairs')) {
      const group=url.searchParams.get('group') || 'ALL',offset=Number(url.searchParams.get('offset')||0),subgroup=url.searchParams.get('subgroup')||'',total=group==='C'?0:group==='A'?34:25;
      reads.push({group,offset,subgroup,revision,limit:Number(url.searchParams.get('limit'))});
      if(delayPairs) await gate;
      body={items:Array.from({length:Math.max(0,Math.min(10,total-offset))},(_,index)=>pair(group,index+offset)),total,offset,next_cursor:offset+10<total?`cursor-${offset+10}`:null};
    } else if(path.endsWith('/decision')) {writes.push(path);denied=true;revision++;body=pair('A',0);}
    else if(path.endsWith('/data')) body={files:[],schema:['name'],profile:{record_count:60,unit:'records',quality_flags:{},field_quality:{},source_counts:{CRM:60}},samples:{items:[],total:0,next_cursor:null}};
    else if(path.endsWith('/runs')||path.endsWith('/upload-sessions')) body={items:[],total:0,next_cursor:null};
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
  });
  return {reads,writes,errors,hold:()=>{delayPairs=true;gate=new Promise(resolve=>{release=resolve;});},release:()=>{delayPairs=false;release?.();}};
}
const row=(page:Page,id:string)=>page.getByRole('checkbox',{name:`Chọn cặp ${id}`,exact:true});
async function group(page:Page,value:string) {
  await page.getByRole('combobox',{name:'Group',exact:true}).evaluate((element,value)=>{const select=element as HTMLSelectElement;select.value=value;select.dispatchEvent(new Event('change',{bubbles:true}));},value);
}

test('Group return restores cached page, filters, search and table view without another read',async({page})=>{
  const mock=await fixture(page);
  await page.setViewportSize({width:900,height:760});
  await page.goto('/app/sessions/r/decisions');
  await expect(row(page,'ALL-0')).toBeVisible();
  await expect.poll(()=>mock.reads.filter(read=>read.group==='A'&&read.offset===0).length).toBe(1);
  await group(page,'A');
  await expect(row(page,'A-0')).toBeVisible();
  await page.getByRole('combobox',{name:'SubGroup',exact:true}).selectOption('A1_NAME_PHONE');
  await page.getByRole('button',{name:'Trang tiếp',exact:true}).click(); await expect(row(page,'A-10')).toBeVisible();
  await page.getByRole('button',{name:'Trang tiếp',exact:true}).click(); await expect(row(page,'A-20')).toBeVisible();
  const table=page.locator('[data-scroll-key="pairs"]');
  await table.evaluate(element=>{element.scrollLeft=120;element.dispatchEvent(new Event('scroll'));});
  const detail=page.locator('details[data-view-key="pair:A-20"]');
  await detail.evaluate(element=>{(element as HTMLDetailsElement).open=true;element.dispatchEvent(new Event('toggle'));});
  await page.evaluate(()=>{window.dispatchEvent(new Event('wheel'));window.scrollTo(0,700);window.dispatchEvent(new Event('scroll'));});
  const savedY=await page.evaluate(()=>window.scrollY);
  await group(page,'D'); await expect(row(page,'D-0')).toBeAttached();
  await group(page,'A');
  await expect(row(page,'A-20')).toBeAttached();
  await expect(page.getByText(/Trang 3 \/ 4/)).toBeAttached();
  await expect(page.getByRole('combobox',{name:'SubGroup',exact:true})).toHaveValue('A1_NAME_PHONE');
  await expect.poll(()=>table.evaluate(element=>element.scrollLeft)).toBe(120);
  await expect(detail).toHaveAttribute('open','');
  await expect.poll(()=>page.evaluate(()=>window.scrollY)).toBe(savedY);
  expect(mock.reads.filter(read=>read.group==='A'&&read.offset===20&&read.subgroup==='A1_NAME_PHONE')).toHaveLength(1);
  await page.getByRole('textbox',{name:'Tìm trong trang'}).fill('A person 20');
  await group(page,'D'); await expect(page.getByRole('textbox',{name:'Tìm trong trang'})).toHaveValue('');
  await group(page,'A'); await expect(page.getByRole('textbox',{name:'Tìm trong trang'})).toHaveValue('A person 20');
  expect(mock.reads.every(read=>read.limit===10)).toBe(true);
  expect(mock.reads.filter(read=>read.group==='C')).toHaveLength(0);
  expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
});

test('an uncached delayed Group shows its own loading area, never the previous Group rows',async({page})=>{
  const mock=await fixture(page);
  await page.goto('/app/sessions/r/decisions');await expect(row(page,'ALL-0')).toBeVisible();
  mock.hold();
  await group(page,'C');
  await expect(page.getByText('Đang đọc Group C…',{exact:true})).toBeVisible();
  await expect(row(page,'ALL-0')).toHaveCount(0);
  mock.release();
  await expect(page.getByText(/Không có cặp trong phạm vi này/)).toBeVisible();
  expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
});

test('Deny refresh keeps cached rows visible, fences writes, and then displays the server decision',async({page})=>{
  const mock=await fixture(page);
  await page.goto('/app/sessions/r/decisions');await expect(row(page,'ALL-0')).toBeVisible();
  await group(page,'A');await expect(row(page,'A-0')).toBeVisible();
  mock.hold();
  await page.getByRole('button',{name:'Deny cặp A-0',exact:true}).click();
  await expect.poll(()=>mock.reads.filter(read=>read.group==='A'&&read.offset===0&&read.revision===2).length).toBeGreaterThan(0);
  await expect(row(page,'A-0')).toBeAttached();
  await expect(page.getByRole('button',{name:'Accept cặp A-0',exact:true})).toBeDisabled();
  await expect(page.getByText('Đang cập nhật dữ liệu…',{exact:true})).toBeAttached();
  mock.release();
  await expect(row(page,'A-0').locator('xpath=ancestor::tr')).toContainText('Deny');
  await expect(page.getByRole('button',{name:'Deny cặp A-0',exact:true})).toBeEnabled();
  expect(mock.writes).toHaveLength(1);expect(mock.errors).toEqual([]);
});
