import { expect, test, type Page } from '@playwright/test';

// Production client/query stores with synthetic HTTP responses. No live session writes.
async function fixture(page: Page,completed=false) {
  const reads: { group: string; offset: number; subgroup: string; revision: number; limit: number; visibility:string; resolution:string }[] = [];
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
    else if(path.endsWith('/pairs/summary')) body={total:84,needs_review:denied?58:59,accepted:0,denied:denied?1:0,kept_separate:0,deferred:0,blocked:denied?1:0,manual_available:denied?58:59,auto_available:0,decision_revision:revision,groups:[{key:'A',total:34,pending:denied?33:34,unresolved:denied?33:34,subgroups:[{key:'A1_NAME_PHONE',total:34,unresolved:denied?33:34}]},{key:'B',total:25,pending:25,unresolved:25,subgroups:[]},{key:'C',total:25,pending:0,unresolved:25,subgroups:[]}]};
    else if(path.endsWith('/pairs')) {
      const group=url.searchParams.get('group') || 'ALL',offset=Number(url.searchParams.get('offset')||0),subgroup=url.searchParams.get('subgroup')||'',resolution=url.searchParams.get('resolution')||'';
      const total=completed?(resolution==='REQUIRED'?0:group==='C'?25:group==='A'?34:group==='ALL'?59:0):group==='C'?0:group==='A'?34:group==='ALL'?59:25;
      reads.push({group,offset,subgroup,revision,limit:Number(url.searchParams.get('limit')),visibility:url.searchParams.get('visibility')||'',resolution});
      if(delayPairs) await gate;
      body={items:Array.from({length:Math.max(0,Math.min(10,total-offset))},(_,index)=>({...pair(group,index+offset),...(completed&&group==='A'?{decision:'ACCEPTED'}:{})})),total,offset,next_cursor:offset+10<total?`cursor-${offset+10}`:null};
    } else if(path.endsWith('/decision')) {writes.push(path);denied=true;revision++;body=pair('A',0);}
    else if(path.endsWith('/data')) body={files:[],schema:['name'],profile:{record_count:60,unit:'records',quality_flags:{},field_quality:{},source_counts:{CRM:60}},samples:{items:[],total:0,next_cursor:null}};
    else if(path.endsWith('/runs')||path.endsWith('/upload-sessions')) body={items:[],total:0,next_cursor:null};
    if(completed&&path.endsWith('/pairs/summary'))body={...(body as object),total:59,needs_review:0,accepted:34,groups:[{key:'A',total:34,pending:0,manual_available:0,unresolved:0,subgroups:[]},{key:'B',total:0,pending:0,manual_available:0,unresolved:0,subgroups:[]},{key:'C',total:25,pending:0,manual_available:0,unresolved:25,subgroups:[]}]};
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
  });
  return {reads,writes,errors,hold:()=>{delayPairs=true;gate=new Promise(resolve=>{release=resolve;});},release:()=>{delayPairs=false;release?.();}};
}
const row=(page:Page,id:string)=>page.getByRole('checkbox',{name:`Chọn cặp ${id}`,exact:true});

test('ineligible Nhóm C explains the block immediately without a bulk preview request',async({page})=>{
  const mock=await fixture(page,true),previews:string[]=[];
  page.on('request',request=>{if(request.url().endsWith('/bulk-decisions/preview'))previews.push(request.url());});
  await page.goto('/app/sessions/r/decisions/pairs');
  await group(page,'C');await expect(row(page,'C-0')).toBeVisible();
  await expect(page.getByRole('button',{name:'Đồng ý gộp Nhóm C',exact:true})).toBeDisabled();
  await expect(page.getByText('Nhóm C chưa đủ thông tin đối chiếu để Đồng ý gộp. Cần bổ sung dữ liệu hoặc đối chiếu.',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Chọn bộ lọc (25)',exact:true}).click();
  await expect(page.getByRole('button',{name:'Đồng ý gộp theo phạm vi',exact:true})).toBeDisabled();
  await expect(page.getByRole('dialog',{name:'Xác nhận Đồng ý gộp'})).toHaveCount(0);
  expect(previews).toEqual([]);expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
});

test('completed Nhóm A remains readable and the required-review card stays fixed on Nhóm C',async({page})=>{
  const mock=await fixture(page,true);
  await page.goto('/app/sessions/r/decisions/pairs');
  await expect(page.getByText('Không còn cặp cần duyệt.',{exact:true})).toBeVisible();
  const required=page.getByRole('group',{name:'Nhóm',exact:true}).getByRole('button',{name:'Cần duyệt 0',exact:true});
  await expect(required).toHaveAttribute('aria-pressed','true');
  await group(page,'A');await expect(row(page,'A-0')).toBeVisible();
  await expect(page.getByRole('group',{name:'Nhóm',exact:true}).getByRole('button',{name:/^Nhóm A /}).locator('strong')).toHaveText('0');
  await expect(page.getByRole('combobox',{name:'Quyết định',exact:true})).toHaveValue('');
  expect(mock.reads.filter(read=>read.group==='A').every(read=>read.resolution==='')).toBe(true);
  await page.getByRole('combobox',{name:'Quyết định',exact:true}).selectOption('REQUIRED');
  await expect(page.getByText('Không còn cặp cần duyệt trong Nhóm A.',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Xem tất cả Nhóm A',exact:true}).click();
  await expect(row(page,'A-0')).toBeVisible();
  await group(page,'C');await expect(row(page,'C-0')).toBeVisible();
  await expect(page.getByRole('group',{name:'Nhóm',exact:true}).getByRole('button',{name:/^Nhóm C /}).locator('strong')).toHaveText('25');
  await expect(required).toBeVisible();await expect(required).toHaveAttribute('aria-pressed','false');
  await required.click();
  await expect(page.getByRole('combobox',{name:'Quyết định',exact:true})).toHaveValue('REQUIRED');
  await expect(required).toHaveAttribute('aria-pressed','true');
  await expect(page.getByText('Không còn cặp cần duyệt.',{exact:true})).toBeVisible();
  expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
});

test('Đồng ý gộp Nhóm previews every matching page without selecting rows or sending a decision',async({page})=>{
  const mock=await fixture(page),previews:Record<string,unknown>[]=[];
  await page.route('**/workflow/bulk-decisions/preview',async route=>{
    previews.push(route.request().postDataJSON());
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({total:34,applicable:34,blocked:0,skipped:0,workflow_revision:4,decision_revision:1,preview_token:'a'.repeat(64)})});
  });
  await page.goto('/app/sessions/r/decisions/pairs');
  await expect(row(page,'ALL-0')).toBeVisible();
  await group(page,'A');await expect(row(page,'A-0')).toBeVisible();
  await page.getByRole('button',{name:'Đồng ý gộp Nhóm A',exact:true}).click();
  await expect(page.getByText('34 cặp có thể Đồng ý gộp',{exact:true})).toBeVisible();
  expect(previews).toHaveLength(1);
  expect(previews[0].scope).toEqual({filter:{group:'A',visibility:'review',taxonomy:'actions_v1'}});
  await page.getByRole('button',{name:'Hủy',exact:true}).click();
  expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
});

test('unchanged classification survives three minutes and a step switch without pair refetch',async({page})=>{
  await page.clock.install();
  const mock=await fixture(page);
  const summaryReads:string[]=[];
  page.on('request',request=>{if(request.url().includes('/pairs/summary'))summaryReads.push(request.url());});
  await page.goto('/app/sessions/r/decisions/pairs');
  await expect(row(page,'ALL-0')).toBeVisible();
  await page.clock.fastForward(3000);
  expect(mock.reads.filter(read=>read.group==='A')).toHaveLength(0);
  await page.locator('a[href="/app/sessions/r/data"]').first().click();
  await page.clock.fastForward(180000);
  const pages=mock.reads.length,summaries=summaryReads.length;
  await page.locator('a[href="/app/sessions/r/decisions/pairs"]').first().click();
  await expect(row(page,'ALL-0')).toBeVisible();
  await page.clock.fastForward(1000);
  expect(mock.reads.length).toBe(pages);
  expect(summaryReads.length).toBe(summaries);
  expect(mock.errors).toEqual([]);
});
async function group(page:Page,value:string) {
  await page.getByRole('group',{name:'Nhóm',exact:true}).getByRole('button',{name:new RegExp(`^Nhóm ${value} `)}).evaluate(element=>(element as HTMLButtonElement).click());
}

test('Nhóm return restores cached page, filters, search and table view without another read',async({page})=>{
  const mock=await fixture(page);
  await page.setViewportSize({width:700,height:760});
  await page.goto('/app/sessions/r/decisions/pairs');
  await expect(row(page,'ALL-0')).toBeVisible();
  await group(page,'A');
  await expect(row(page,'A-0')).toBeVisible();
  await page.getByRole('button',{name:'Nhóm chi tiết Nhóm A',exact:true}).click();
  await page.getByRole('group',{name:'Nhóm chi tiết của Nhóm A',exact:true}).getByRole('button',{name:'Tên và điện thoại 34',exact:true}).click();
  await page.getByRole('button',{name:'Trang tiếp',exact:true}).click(); await expect(row(page,'A-10')).toBeVisible();
  await page.getByRole('button',{name:'Trang tiếp',exact:true}).click(); await expect(row(page,'A-20')).toBeVisible();
  const table=page.locator('[data-scroll-key="pairs"]');
  await table.evaluate(element=>{element.scrollLeft=120;element.dispatchEvent(new Event('scroll'));});
  await page.evaluate(()=>{window.dispatchEvent(new Event('wheel'));window.scrollTo(0,700);window.dispatchEvent(new Event('scroll'));});
  const savedY=await page.evaluate(()=>window.scrollY);
  await group(page,'B'); await expect(row(page,'B-0')).toBeAttached();
  await group(page,'A');
  await expect(row(page,'A-20')).toBeAttached();
  await expect(page.getByText(/Trang 3 \/ 4/)).toBeAttached();
  await expect(page.getByText('Tên và điện thoại',{exact:true}).first()).toBeAttached();
  await expect(page.getByRole('combobox',{name:'SubGroup',exact:true})).toHaveCount(0);
  await expect.poll(()=>table.evaluate(element=>element.scrollLeft)).toBe(120);
  await expect.poll(()=>page.evaluate(()=>window.scrollY)).toBe(savedY);
  expect(mock.reads.filter(read=>read.group==='A'&&read.offset===20&&read.subgroup==='A1_NAME_PHONE')).toHaveLength(1);
  await page.getByRole('textbox',{name:'Tìm trong trang'}).fill('A person 20');
  await group(page,'B'); await expect(page.getByRole('textbox',{name:'Tìm trong trang'})).toHaveValue('');
  await group(page,'A'); await expect(page.getByRole('textbox',{name:'Tìm trong trang'})).toHaveValue('A person 20');
  expect(mock.reads.every(read=>read.limit===10)).toBe(true);
  expect(mock.reads.every(read=>read.visibility==='review')).toBe(true);
  expect(mock.reads.filter(read=>read.group==='C')).toHaveLength(0);
  expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
});

test('an uncached delayed Nhóm shows its own loading area, never the previous Nhóm rows',async({page})=>{
  const mock=await fixture(page);
  await page.goto('/app/sessions/r/decisions/pairs');await expect(row(page,'ALL-0')).toBeVisible();
  mock.hold();
  await group(page,'C');
  await expect(page.getByText('Đang đọc Nhóm C…',{exact:true})).toBeVisible();
  await expect(row(page,'ALL-0')).toHaveCount(0);
  mock.release();
  await expect(page.getByText('Không có cặp khớp bộ lọc.',{exact:true})).toBeVisible();
  expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
});

test('Không gộp refresh keeps cached rows visible, fences writes, and then displays the hệ thống decision',async({page})=>{
  const mock=await fixture(page);
  await page.goto('/app/sessions/r/decisions/pairs');await expect(row(page,'ALL-0')).toBeVisible();
  await group(page,'A');await expect(row(page,'A-0')).toBeVisible();
  mock.hold();
  await page.getByRole('button',{name:'Không gộp cặp A-0',exact:true}).click();
  await expect.poll(()=>mock.reads.filter(read=>read.group==='A'&&read.offset===0&&read.revision===2).length).toBeGreaterThan(0);
  await expect(row(page,'A-0')).toBeAttached();
  await expect(page.getByRole('button',{name:'Đồng ý gộp cặp A-0',exact:true})).toBeDisabled();
  await expect(page.getByText('Đang cập nhật dữ liệu…',{exact:true})).toBeAttached();
  mock.release();
  await expect(row(page,'A-0').locator('xpath=ancestor::tr')).toContainText('Không gộp');
  await expect(page.getByRole('button',{name:'Không gộp cặp A-0',exact:true})).toBeDisabled();
  await expect(page.getByRole('group',{name:'Nhóm',exact:true}).getByRole('button',{name:/^Nhóm A /}).locator('strong')).toHaveText('33');
  expect(mock.writes).toHaveLength(1);expect(mock.errors).toEqual([]);
});
