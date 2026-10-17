import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';

async function fixture(page: Page,readOnly=false) {
  const reads:string[]=[],writes:{path:string;body:any}[]=[],errors:string[]=[];
  let revision=1;
  page.on('pageerror',error=>errors.push(error.message));
  const generation=()=>({revision,source_decision_revision:1,stale:false,read_only:false,publication_status:'COMPLETE',clusters:2,multi_record_groups:2,singletons:0,covered_records:4,unresolved_records:0,total_records:4});
  const group=(id:string)=>({cluster_id:id,display_name:id,member_count:2,member_record_ids:[`${id}-a`,`${id}-b`],singleton:false,status:'DRAFT',evidence:{items:[],total:0,next_cursor:null}});
  const records=(id:string)=>['a','b'].map(suffix=>({record_id:`${id}-${suffix}`,external_id:suffix,source_system:'CRM',raw_fields:{name:`Person ${suffix}`},normalized_fields:{},extra_fields:{}}));
  await page.route('**/api/v1/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    let body:any={};
    if(request.method()==='GET')reads.push(path+url.search);else writes.push({path,body:request.postDataJSON()});
    if(path.endsWith('/auth/me'))body={id:'cluster-windows-owner',email:'windows@example.test',role:'STEWARD',csrf_token:'synthetic',capabilities:['read','approve_pair','edit_golden']};
    else if(path.endsWith('/workflow'))body={run_id:'r',workflow_version:2,workflow_state:'GOLDEN_REVIEW',workflow_revision:revision+2,decision_revision:1,cluster_revision:revision,golden_revision:1,counts:{records:{value:4,unit:'records'}},cluster_result:generation(),allowed_actions:readOnly?[]:['save-golden','build-cluster-queue'],blockers:[],active_operation:null,retryable_operation:null};
    else if(path.endsWith('/cluster-queue'))body={pending_count:2};
    else if(path.endsWith('/clusters'))body={items:[group(url.searchParams.has('cursor')?'c2':'c1')],total:2,next_cursor:url.searchParams.has('cursor')?null:'next',generation:generation()};
    else if(/\/clusters\/[^/]+\/golden$/.test(path))body={cluster_id:path.split('/').at(-2),cluster_revision:revision,golden_revision:1,revision:1,status:'DRAFT',fields:{name:{value:'Original name',source_record_id:'c1-a',source_system:'CRM'}},alternatives:{},selections:{},generation:generation()};
    else if(/\/clusters\/[^/]+$/.test(path)){const id=path.split('/').at(-1)!;body={...group(id),members:{items:records(id),total:2,next_cursor:null},pair_evidence:{items:[],total:0,next_cursor:null},workflow_context:{},llm_stats:{},generation:generation()};}
    else if(path.endsWith('/golden/resolution'))body={resolution:null,undo_operation_id:null,stale:false};
    else if(path.endsWith('/structure'))body={enabled:true,can_manage:true,active_constraints:[]};
    else if(path.endsWith('/split/preview'))body={operation_id:'preview',run_id:'r',operation_type:'SPLIT_PREVIEW',status:'COMPLETED',stage:'complete',progress:{},result:{preview_token:'token',blocked_reasons:['HARD_CONSTRAINT'],intent:{partitions:[['c1-a'],['c1-b']]},cross_candidate_pair_ids:[],affected_golden:[]}};
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
  });
  return {reads,writes,errors,nextGeneration:()=>{revision++;}};
}
const nav=(page:Page)=>page.getByRole('navigation',{name:'Các bước trong phiên'});
const resourceReads=(paths:string[])=>paths.filter(path=>/\/clusters(?:\?|\/)|\/cluster-queue/.test(path));

test('two windows retain list page, open group and Golden draft without refetching after three minutes',async({page})=>{
  await page.clock.install();const mock=await fixture(page);
  await page.goto('/app/sessions/r/clusters');
  await expect(page.getByRole('heading',{name:'Tổng quan nhóm',exact:true})).toBeVisible();
  const children=nav(page).locator('ul[id$="-clusters"]');
  await expect(children.getByRole('link')).toHaveCount(2);
  await expect(children.getByRole('link',{name:'Tổng quan nhóm'})).toHaveAttribute('aria-current','page');
  await expect(page.getByRole('region',{name:'Tổng bản ghi',exact:true})).toContainText('4');
  await expect(page.getByRole('region',{name:'Nhóm nhiều bản ghi',exact:true})).toContainText('2');
  await expect(page.getByRole('progressbar',{name:'Bản ghi đã có kết quả',exact:true})).toHaveAttribute('value','4');
  await expect(page.getByRole('progressbar',{name:'Bản ghi đã có kết quả',exact:true})).toHaveAttribute('max','4');
  expect(mock.reads.filter(path=>path.endsWith('/clusters'))).toHaveLength(0);
  expect(mock.reads.filter(path=>path.endsWith('/structure'))).toHaveLength(0);
  await nav(page).getByRole('link',{name:'Danh sách nhóm',exact:true}).click();
  await expect(page).toHaveURL(/\/clusters\/records$/);
  await page.getByRole('button',{name:'Trang tiếp',exact:true}).first().click();
  await expect(page.getByRole('button',{name:/^Đối chiếu nhóm c2$/})).toBeVisible();
  await page.getByRole('button',{name:'Đối chiếu nhóm c2',exact:true}).click();
  await page.getByRole('textbox',{name:'Chỉnh tay name'}).fill('Draft preserved');
  page.once('dialog',dialog=>dialog.accept());
  await page.getByRole('button',{name:'Đóng chi tiết nhóm'}).click();
  const before=resourceReads(mock.reads);
  await nav(page).getByRole('link',{name:'Tổng quan nhóm',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Danh sách nhóm',exact:true})).toHaveCount(0);
  await page.clock.fastForward(180000);
  await nav(page).getByRole('link',{name:'Danh sách nhóm',exact:true}).click();
  await page.getByRole('button',{name:'Đối chiếu nhóm c2',exact:true}).click();
  await expect(page.getByRole('textbox',{name:'Chỉnh tay name'})).toHaveValue('Draft preserved');
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
  await expect(page).toHaveURL(/\/clusters\/records\?cluster=c1$/);
  await expect(page.getByRole('heading',{name:'Chi tiết nhóm c1',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Đóng chi tiết nhóm'}).click();
  await page.getByRole('button',{name:'Trang tiếp',exact:true}).first().click();
  await expect(page.getByRole('button',{name:/^Đối chiếu nhóm c2$/})).toBeVisible();
  mock.nextGeneration();
  await page.clock.fastForward(31000);
  await expect(page.getByRole('heading',{name:'Chi tiết nhóm c1',exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:/^Đối chiếu nhóm c1$/})).toBeVisible();
  await expect(page).toHaveURL(/\/clusters\/records$/);
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
  const table=page.getByRole('table',{name:'Danh sách nhóm bản ghi'});
  await expect(table.getByRole('row')).toHaveCount(26);
  await expect(page.getByText('Hiển thị 1–25 / 1.721 nhóm')).toBeVisible();
  await page.getByRole('checkbox',{name:'Chọn trang hiện tại'}).check();
  await expect(page.getByText('Đã chọn 25 nhóm')).toBeVisible();
  await page.screenshot({path:path.resolve('../artifacts/cluster-ui-20261017/list-desktop.png'),fullPage:true});
  const pager=page.getByRole('navigation',{name:'Phân trang nhóm'});
  await pager.getByRole('button',{name:'Trang tiếp',exact:true}).click();
  await expect(page.getByText('Hiển thị 26–50 / 1.721 nhóm')).toBeVisible();
  await pager.getByRole('button',{name:'Trang trước',exact:true}).click();
  await expect(page.getByRole('checkbox',{name:'Chọn nhóm Nguyễn Văn An 1',exact:true})).toBeChecked();
  await page.getByRole('button',{name:'Đối chiếu nhóm Nguyễn Văn An 1',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Chi tiết nhóm c1',exact:true})).toBeFocused();
  const comparison=page.getByRole('table',{name:'Đối chiếu thành viên nhóm'});
  const nameRow=comparison.getByRole('rowheader',{name:/Họ tên/}).locator('..');
  const phoneRow=comparison.getByRole('rowheader',{name:/Điện thoại/}).locator('..');
  const diffColor=await nameRow.getByRole('cell').first().evaluate(cell=>getComputedStyle(cell).backgroundColor);
  const sameColor=await phoneRow.getByRole('cell').first().evaluate(cell=>getComputedStyle(cell).backgroundColor);
  expect(diffColor).not.toBe(sameColor);
  await page.getByRole('checkbox',{name:'Chỉ hiện trường khác nhau'}).check();
  await expect(comparison.getByRole('rowheader',{name:/Điện thoại/})).toHaveCount(0);
  await page.screenshot({path:path.resolve('../artifacts/cluster-ui-20261017/comparison-desktop.png'),fullPage:true});
  await page.getByRole('textbox',{name:'Chỉnh tay name'}).fill('Unsaved draft');
  page.once('dialog',dialog=>dialog.dismiss());
  await page.getByRole('button',{name:'Đóng chi tiết nhóm'}).click();
  await expect(page.getByRole('textbox',{name:'Chỉnh tay name'})).toHaveValue('Unsaved draft');
  await expect(page).toHaveURL(/cluster=c1$/);
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByRole('dialog',{name:'Chi tiết nhóm c1'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:path.resolve('../artifacts/cluster-ui-20261017/list-mobile.png'),fullPage:true});
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
  await expect(page).toHaveURL(/\/clusters\/records$/);
  expect(mock.writes).toEqual([]);expect(mock.errors).toEqual([]);
});
