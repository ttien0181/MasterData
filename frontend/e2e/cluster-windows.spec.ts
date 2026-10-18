import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';

async function fixture(page: Page,readOnly=false,stale=false) {
  const reads:string[]=[],writes:{path:string;body:any}[]=[],errors:string[]=[];
  let revision=1;
  let goldenRevision=1;
  const selections:Record<string,Record<string,any>>={};
  page.on('pageerror',error=>errors.push(error.message));
  const generation=()=>({revision,source_decision_revision:1,stale,read_only:stale,publication_status:'COMPLETE',clusters:2,multi_record_groups:2,singletons:0,covered_records:4,unresolved_records:0,total_records:4});
  const group=(id:string)=>({cluster_id:id,display_name:id,member_count:2,member_record_ids:[`${id}-a`,`${id}-b`],singleton:false,status:'DRAFT',evidence:{items:[],total:0,next_cursor:null}});
  const records=(id:string)=>['a','b'].map(suffix=>({record_id:`${id}-${suffix}`,external_id:suffix,source_system:suffix==='a'?'CSV':'CRM',raw_fields:{name:suffix==='a'?'PHAN MINH KHOA':'Phan Minh Khoa',phone:suffix==='a'?'0386967972':'+84386967972',dob:'1991-07-27',government_id:'001191004308',address:'370 Đường Nguyễn Trãi, Hà Nội'},normalized_fields:{phone:'0386967972'},extra_fields:{}}));
  const goldenFor=(id:string)=>{
    const rows=records(id),choices=selections[id]||{},fields:Record<string,any>={},alternatives:Record<string,any[]>={};
    for(const field of ['name','phone','email','dob','government_id','address']){
      alternatives[field]=rows.filter(row=>(row.raw_fields as Record<string,unknown>)[field]!=null).map(row=>({source_record_id:row.record_id,source_system:row.source_system,value:(row.normalized_fields as Record<string,unknown>)[field]||(row.raw_fields as Record<string,unknown>)[field],auto_rule_available:true}));
      const selected=choices[field]?.selection_mode==='SOURCE_CHOICE'?alternatives[field].find(source=>source.source_record_id===choices[field].source_record_id):alternatives[field][0];
      fields[field]=choices[field]?.selection_mode==='MANUAL_OVERRIDE'?{value:choices[field].value}:selected?{value:selected.value,source_record_id:selected.source_record_id,source_system:selected.source_system}:{};
    }
    return {cluster_id:id,cluster_revision:revision,golden_revision:goldenRevision,revision:goldenRevision,status:'DRAFT',fields,alternatives,selections:choices,generation:generation()};
  };
  await page.route('**/api/v1/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    let body:any={};
    if(request.method()==='GET')reads.push(path+url.search);else writes.push({path,body:request.postDataJSON()});
    if(path.endsWith('/auth/me'))body={id:'cluster-windows-owner',email:'windows@example.test',role:'STEWARD',csrf_token:'synthetic',capabilities:['read','approve_pair','edit_golden']};
    else if(path.endsWith('/workflow'))body={run_id:'r',workflow_version:2,workflow_state:'GOLDEN_REVIEW',workflow_revision:revision+1+goldenRevision,decision_revision:1,cluster_revision:revision,golden_revision:goldenRevision,counts:{records:{value:4,unit:'records'}},cluster_result:generation(),allowed_actions:readOnly?[]:['save-golden','build-cluster-queue'],blockers:[],active_operation:null,retryable_operation:null};
    else if(path.endsWith('/cluster-queue'))body={pending_count:2};
    else if(path.endsWith('/clusters'))body={items:[group(url.searchParams.has('cursor')?'c2':'c1')],total:2,next_cursor:url.searchParams.has('cursor')?null:'next',generation:generation()};
    else if(/\/clusters\/[^/]+\/golden$/.test(path)){
      const id=path.split('/').at(-2)!;
      if(request.method()==='PUT'){
        selections[id]={...selections[id],...request.postDataJSON().selections};goldenRevision++;
        body={items:[goldenFor(id)],workflow_revision:revision+1+goldenRevision,golden_revision:goldenRevision};
      }else body=goldenFor(id);
    }
    else if(/\/clusters\/[^/]+$/.test(path)){const id=path.split('/').at(-1)!;body={...group(id),members:{items:records(id),total:2,next_cursor:null},pair_evidence:{items:[],total:0,next_cursor:null},workflow_context:{},llm_stats:{},generation:generation()};}
    else if(path.endsWith('/golden/resolution'))body={resolution:null,undo_operation_id:null,stale:false};
    else if(path.endsWith('/golden/index'))body={items:[],total:2,next_cursor:null,summary:{total:2,confirmed:0,needs_review:2},generation:generation()};
    else if(path.endsWith('/structure'))body={enabled:true,can_manage:true,active_constraints:[]};
    else if(path.endsWith('/split/preview'))body={operation_id:'preview',run_id:'r',operation_type:'SPLIT_PREVIEW',status:'COMPLETED',stage:'complete',progress:{},result:{preview_token:'token',blocked_reasons:['HARD_CONSTRAINT'],intent:{partitions:[['c1-a'],['c1-b']]},cross_candidate_pair_ids:[],affected_golden:[]}};
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
  });
  return {reads,writes,errors,nextGeneration:()=>{revision++;}};
}
const nav=(page:Page)=>page.getByRole('navigation',{name:'Các bước trong phiên'});
const resourceReads=(paths:string[])=>paths.filter(path=>/\/clusters(?:\?|\/)|\/cluster-queue/.test(path));

test('one workspace shows the queue and customer groups and preserves drafts after returning',async({page})=>{
  await page.clock.install();const mock=await fixture(page);
  await page.goto('/app/sessions/r/clusters');
  await expect(page.getByRole('heading',{name:'Các nhóm hồ sơ khách hàng',exact:true})).toBeVisible();
  const children=nav(page).locator('ul[id$="-clusters"]');
  await expect(children.getByRole('link')).toHaveCount(0);
  await expect(page.getByRole('group',{name:'Tổng hồ sơ',exact:true})).toContainText('4');
  await expect(page.getByRole('button',{name:'Tạo nhóm mới'})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Cập nhật toàn bộ'})).toHaveCount(0);
  await expect(page.getByRole('table',{name:'Danh sách nhóm hồ sơ'})).toBeVisible();
  expect(mock.reads.filter(path=>path.endsWith('/structure'))).toHaveLength(0);
  await page.getByRole('button',{name:'Trang tiếp',exact:true}).first().click();
  await expect(page.getByRole('button',{name:/^Đối chiếu nhóm c2$/})).toBeVisible();
  await page.getByRole('button',{name:'Đối chiếu nhóm c2',exact:true}).click();
  await page.getByLabel('Nhập giá trị khác Họ tên',{exact:true}).click();
  await page.getByRole('textbox',{name:'Nhập trực tiếp name'}).fill('Draft preserved');
  page.once('dialog',dialog=>dialog.accept());
  await page.getByRole('button',{name:'Đóng chi tiết nhóm'}).click();
  const before=resourceReads(mock.reads);
  await nav(page).getByRole('link',{name:'Hồ sơ đại diện',exact:false}).click();
  await expect(page.getByRole('heading',{name:'Tổng quan & tiến độ',exact:true})).toBeVisible();
  await page.clock.fastForward(180000);
  await nav(page).getByRole('link',{name:'Các nhóm hồ sơ khách hàng',exact:false}).click();
  await page.getByRole('button',{name:'Đối chiếu nhóm c2',exact:true}).click();
  await expect(page.getByRole('textbox',{name:'Nhập trực tiếp name'})).toHaveValue('Draft preserved');
  expect(resourceReads(mock.reads)).toEqual(before);
  expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
});

test('structure controls open inside the selected group and retain preview guards',async({page})=>{
  const mock=await fixture(page);
  await page.goto('/app/sessions/r/clusters/records');
  await expect(page.getByRole('button',{name:'Tách/Gộp nhóm',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:/^Đối chiếu nhóm c1$/}).click();
  await page.getByRole('button',{name:'Tách/Gộp nhóm',exact:true}).click();
  const structure=page.getByRole('region',{name:'Tách và gộp nhóm',exact:true});
  await expect(structure).toBeVisible();
  await expect(structure.getByRole('combobox',{name:'Nhóm trên trang hiện tại'})).toHaveCount(0);
  await structure.getByRole('checkbox').first().check();
  await structure.getByRole('textbox',{name:'Lý do',exact:true}).fill('Reviewed source');
  await structure.getByRole('button',{name:'Preview tách',exact:true}).click();
  await expect(structure.getByRole('alert')).toContainText('HARD_CONSTRAINT');
  expect(mock.writes).toHaveLength(1);
  expect(mock.writes[0].body.cluster_ids).toEqual(['c1']);
  expect(mock.writes[0].body.partitions.flat().sort()).toEqual(['c1-a','c1-b']);
  await expect(structure.getByRole('button',{name:/Xác nhận thay đổi/})).toHaveCount(0);
  expect(mock.errors).toEqual([]);
});

test('old group links redirect to the list and a new generation resets the old cursor and group',async({page})=>{
  await page.clock.install();
  const mock=await fixture(page);
  await page.goto('/app/sessions/r/clusters?cluster=c1');
  await expect(page).toHaveURL(/\/clusters\?cluster=c1$/);
  await expect(page.getByRole('heading',{name:'Chi tiết nhóm c1',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Đóng chi tiết nhóm'}).click();
  await page.getByRole('button',{name:'Trang tiếp',exact:true}).first().click();
  await expect(page.getByRole('button',{name:/^Đối chiếu nhóm c2$/})).toBeVisible();
  mock.nextGeneration();
  await page.clock.fastForward(31000);
  await expect(page.getByRole('heading',{name:'Chi tiết nhóm c1',exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:/^Đối chiếu nhóm c1$/})).toBeVisible();
  await expect(page).toHaveURL(/\/clusters$/);
  expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
});

test('a read-only session keeps group details readable without structure mutations',async({page})=>{
  const mock=await fixture(page,true);
  await page.goto('/app/sessions/r/clusters/records?cluster=c1');
  await expect(page.getByRole('heading',{name:'Chi tiết nhóm c1',exact:true})).toBeVisible();
  await expect(page.getByRole('table',{name:'Đối chiếu thành viên nhóm'})).toBeVisible();
  await expect(page.getByRole('button',{name:'Tách/Gộp nhóm',exact:true})).toHaveCount(0);
  expect(mock.reads.filter(path=>path.endsWith('/structure'))).toHaveLength(0);
  expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
});

test('group list stays readable at scale, preserves selection and highlights comparison fields',async({page})=>{
  const mock=await fixture(page);
  await page.route(/\/runs\/r\/workflow\/clusters\?/,async route=>{
    const second=new URL(route.request().url()).searchParams.has('cursor');
    await route.fulfill({json:{items:Array.from({length:25},(_,i)=>({cluster_id:`c${i+1+(second?25:0)}`,display_name:`Nguyễn Văn An ${i+1+(second?25:0)}`,member_count:2,singleton:false,status:'REVIEW_APPROVED',member_record_ids:[],evidence:{items:[],total:0,next_cursor:null}})),total:1721,next_cursor:second?null:'next'}});
  });
  await page.setViewportSize({width:1440,height:1000});
  await page.goto('/app/sessions/r/clusters/records');
  const table=page.getByRole('table',{name:'Danh sách nhóm hồ sơ'});
  await expect(table.getByRole('row')).toHaveCount(26);
  await expect(page.getByText('Hiển thị 1–25 / 1.721 nhóm')).toBeVisible();
  await page.getByRole('checkbox',{name:'Chọn trang hiện tại'}).check();
  await expect(page.getByText('Đã chọn 25 nhóm')).toBeVisible();
  await page.screenshot({path:path.resolve('../artifacts/cluster-ui-20261017/list-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:path.resolve('../artifacts/cluster-ui-20261017/workspace-mobile.png'),fullPage:true});
  await page.setViewportSize({width:1440,height:1000});
  const pager=page.getByRole('navigation',{name:'Phân trang nhóm'});
  await pager.getByRole('button',{name:'Trang tiếp',exact:true}).click();
  await expect(page.getByText('Hiển thị 26–50 / 1.721 nhóm')).toBeVisible();
  await pager.getByRole('button',{name:'Trang trước',exact:true}).click();
  await expect(page.getByRole('checkbox',{name:'Chọn nhóm Nguyễn Văn An 1',exact:true})).toBeChecked();
  await page.getByRole('button',{name:'Đối chiếu nhóm Nguyễn Văn An 1',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Chi tiết nhóm c1',exact:true})).toBeFocused();
  const comparison=page.getByRole('table',{name:'Đối chiếu thành viên nhóm'});
  const nameRow=comparison.getByRole('rowheader',{name:/Họ tên/}).locator('..');
  const sameRow=comparison.getByRole('rowheader',{name:/Ngày sinh/}).locator('..');
  const diffColor=await nameRow.getByRole('cell').nth(1).evaluate(cell=>getComputedStyle(cell).backgroundColor);
  const sameColor=await sameRow.getByRole('cell').nth(1).evaluate(cell=>getComputedStyle(cell).backgroundColor);
  expect(diffColor).not.toBe(sameColor);
  await page.getByRole('checkbox',{name:'Chỉ hiện trường khác nhau'}).check();
  await expect(comparison.getByRole('rowheader',{name:/Ngày sinh/})).toHaveCount(0);
  await page.screenshot({path:path.resolve('../artifacts/cluster-ui-20261017/comparison-desktop.png'),fullPage:true});
  await page.getByLabel('Nhập giá trị khác Họ tên',{exact:true}).click();
  await page.getByRole('textbox',{name:'Nhập trực tiếp name'}).fill('Unsaved draft');
  page.once('dialog',dialog=>dialog.dismiss());
  await page.getByRole('button',{name:'Đóng chi tiết nhóm'}).click();
  await expect(page.getByRole('textbox',{name:'Nhập trực tiếp name'})).toHaveValue('Unsaved draft');
  await expect(page).toHaveURL(/cluster=c1$/);
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByRole('dialog',{name:'Chi tiết nhóm c1'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:path.resolve('../artifacts/cluster-ui-20261017/list-mobile.png'),fullPage:true});
  expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
});

test('before the first generation status is read without manual actions or unpublished groups',async({page})=>{
  const mock=await fixture(page);
  await page.route('**/runs/r/workflow',route=>route.fulfill({json:{run_id:'r',workflow_version:2,workflow_state:'DECIDE_REVIEW',workflow_revision:1,decision_revision:1,cluster_revision:0,golden_revision:0,counts:{},allowed_actions:['build-cluster-queue'],blockers:[],active_operation:null,retryable_operation:null}}));
  await page.goto('/app/sessions/r/clusters');
  await expect(page.getByRole('button',{name:'Tạo nhóm mới'})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Cập nhật toàn bộ'})).toHaveCount(0);
  await expect(page.getByText('Chưa có nhóm hồ sơ.',{exact:false})).toBeVisible();
  await expect(page.getByRole('table',{name:'Danh sách nhóm hồ sơ'})).toHaveCount(0);
  expect(mock.reads.some(path=>/\/clusters(?:\?|$)/.test(path))).toBe(false);
  expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
});

test('the group list appears after the server finishes a background refresh without browser writes',async({page})=>{
  await page.clock.install();
  const mock=await fixture(page);
  let complete=false;
  await page.route('**/runs/r/workflow',route=>route.fulfill({json:{run_id:'r',workflow_version:2,workflow_state:complete?'GOLDEN_REVIEW':'CLUSTER_RUNNING',workflow_revision:complete?3:2,decision_revision:1,cluster_revision:complete?1:0,golden_revision:0,counts:{},allowed_actions:complete?['save-golden']:['queue-decisions'],blockers:[],active_operation:complete?null:{operation_id:'auto-clusters',operation_type:'CLUSTER',status:'RUNNING',stage:'cluster',progress:{},result:{},error_code:null},retryable_operation:null}}));
  await page.goto('/app/sessions/r/clusters');
  await expect(page.getByRole('status').filter({hasText:'Đang tự động cập nhật nhóm hồ sơ'})).toBeVisible();
  await expect(page.getByRole('button',{name:'Cập nhật toàn bộ'})).toHaveCount(0);
  complete=true;
  await page.clock.fastForward(2000);
  await expect(page.getByRole('table',{name:'Danh sách nhóm hồ sơ'})).toBeVisible();
  expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
});

test('stale groups show an actionable warning and keep customer edits disabled',async({page})=>{
  const mock=await fixture(page,false,true);
  await page.goto('/app/sessions/r/clusters?cluster=c1');
  await expect(page.getByRole('status').filter({hasText:'Kết quả chưa cập nhật'})).toBeVisible();
  await expect(page.getByRole('button',{name:/^Giữ Họ tên từ CRM/})).toBeDisabled();
  await expect(page.getByRole('button',{name:'Lưu thông tin đại diện'})).toBeDisabled();
  await expect(page.getByRole('button',{name:'Xác nhận hồ sơ đại diện',exact:true})).toBeDisabled();
  expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
});

test('one table selects and highlights source cells, saves them and restores the chosen source',async({page})=>{
  const mock=await fixture(page);
  await page.setViewportSize({width:1440,height:1000});
  await page.goto('/app/sessions/r/clusters?cluster=c1');
  const dialog=page.getByRole('dialog',{name:'Chi tiết nhóm c1'});
  await expect(dialog.getByRole('heading',{name:'Thông tin đại diện của khách hàng',exact:true})).toHaveCount(1);
  await expect(dialog.getByRole('heading',{name:/Hồ sơ trong nhóm/})).toHaveCount(0);
  const table=dialog.getByRole('table',{name:'Đối chiếu thành viên nhóm'});
  const row=table.getByRole('rowheader',{name:/Họ tên/}).locator('..');
  const csv=row.getByRole('button',{name:/^Giữ Họ tên từ CSV/}),crm=row.getByRole('button',{name:/^Giữ Họ tên từ CRM/});
  await expect(csv).toHaveAttribute('aria-pressed','true');
  await crm.click();
  await expect(crm).toHaveAttribute('aria-pressed','true');await expect(csv).toHaveAttribute('aria-pressed','false');
  const chosenStyle=await crm.locator('..').evaluate(cell=>({background:getComputedStyle(cell).backgroundColor,shadow:getComputedStyle(cell).boxShadow}));
  const otherStyle=await csv.locator('..').evaluate(cell=>({background:getComputedStyle(cell).backgroundColor,shadow:getComputedStyle(cell).boxShadow}));
  expect(chosenStyle.background).not.toBe(otherStyle.background);expect(chosenStyle.shadow).not.toBe(otherStyle.shadow);
  await expect(row.getByRole('cell',{name:'Thông tin giữ lại Họ tên',exact:true})).toContainText('Phan Minh Khoa');
  await expect(table.getByRole('button',{name:/^Giữ Điện thoại từ CSV/})).toHaveAttribute('aria-pressed','true');
  expect(mock.writes).toEqual([]);
  await dialog.getByRole('button',{name:'Lưu thông tin đại diện'}).click();
  await expect.poll(()=>mock.writes.length).toBe(1);
  expect(mock.writes[0].body.selections).toEqual({name:{selection_mode:'SOURCE_CHOICE',source_record_id:'c1-b'}});
  await expect(crm).toHaveAttribute('aria-pressed','true');
  await page.screenshot({path:path.resolve('../artifacts/golden-cell-selection-20261018/desktop.png'),fullPage:true});
  await page.getByRole('button',{name:'Đóng chi tiết nhóm'}).click();
  await page.getByRole('button',{name:'Đối chiếu nhóm c1',exact:true}).click();
  await expect(page.getByRole('button',{name:/^Giữ Họ tên từ CRM/})).toHaveAttribute('aria-pressed','true');
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:path.resolve('../artifacts/golden-cell-selection-20261018/mobile.png'),fullPage:true});
  expect(mock.errors).toEqual([]);
});

test('source pagination and evidence remain readable if the hồ sơ đại diện request fails',async({page})=>{
  const mock=await fixture(page);
  await page.route('**/workflow/clusters/c1/golden',route=>route.fulfill({status:503,json:{detail:'hồ sơ đại diện temporarily unavailable'}}));
  await page.route(/\/workflow\/clusters\/c1(?:\?|$)/,route=>{
    const second=new URL(route.request().url()).searchParams.has('cursor');
    return route.fulfill({json:{cluster_id:'c1',display_name:'c1',member_count:26,singleton:false,status:'DRAFT',member_record_ids:[],members:{items:[{record_id:second?'second':'first',source_system:'CRM',raw_fields:{name:second?'Hồ sơ trang sau':'Hồ sơ trang đầu'},normalized_fields:{},extra_fields:{}}],total:26,next_cursor:second?null:'next'},pair_evidence:{items:[],total:0,next_cursor:null},workflow_context:{},llm_stats:{}}});
  });
  await page.goto('/app/sessions/r/clusters?cluster=c1');
  const dialog=page.getByRole('dialog',{name:'Chi tiết nhóm c1'});
  await expect(dialog.getByRole('columnheader',{name:/Hồ sơ trang đầu/})).toBeVisible();
  await expect(dialog.getByText('Xem dữ liệu nguồn đầy đủ',{exact:true})).toBeVisible();
  await expect(dialog.getByText('Thông tin đối chiếu cặp (0 cặp)',{exact:true})).toBeVisible();
  await dialog.getByRole('button',{name:'Trang tiếp',exact:true}).click();
  await expect(dialog.getByRole('columnheader',{name:/Hồ sơ trang sau/})).toBeVisible();
  expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
});

test('popup traps keyboard focus, closes with Escape and returns focus to the selected group',async({page})=>{
  const mock=await fixture(page);
  await page.goto('/app/sessions/r/clusters/records');
  const trigger=page.getByRole('button',{name:'Đối chiếu nhóm c1',exact:true});
  await trigger.click();
  const dialog=page.getByRole('dialog',{name:'Chi tiết nhóm c1'});
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveJSProperty('open',true);
  for(let i=0;i<12;i++){
    await page.keyboard.press('Tab');
    expect(await dialog.evaluate(element=>element.contains(document.activeElement))).toBe(true);
  }
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await expect(page).toHaveURL(/\/clusters$/);
  expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
});
