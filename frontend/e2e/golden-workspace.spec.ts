import {expect,test} from '@playwright/test';
const generation={revision:1,source_decision_revision:1,stale:false,read_only:false,publication_status:'COMPLETE',clusters:3,multi_record_groups:1,singletons:2,covered_records:4,unresolved_records:0,total_records:4};
const names=['Nguyễn Minh An','Trần Thị Bình','Lê Văn Cường'];
const items=names.map((name,index)=>({cluster_id:`c${index+1}`,display_name:name,phone:`090123456${index}`,email:`user${index}@example.test`,member_count:index?1:2,status:'DRAFT',review_fields:[]}));
test.beforeEach(async({page})=>{
 await page.addInitScript(()=>localStorage.setItem('masterbot-introductions:golden-e2e','off'));
 await page.route('**/api/v1/**',async route=>{
  const url=new URL(route.request().url()),path=url.pathname;let body:unknown={};
  if(path.endsWith('/auth/me'))body={id:'golden-e2e',email:'golden@example.test',role:'STEWARD',csrf_token:'test',capabilities:['read','export_data']};
  else if(path.endsWith('/workflow'))body={run_id:'r',workflow_version:2,workflow_state:'GOLDEN_REVIEW',workflow_revision:3,decision_revision:1,assessment_id:'a',cluster_revision:1,golden_revision:2,counts:{records:{value:4,unit:'records'}},allowed_actions:[],blockers:[],active_operation:null,cluster_result:generation};
  else if(path.endsWith('/golden/index'))body={items:url.searchParams.has('cursor')?items.slice(2):items.slice(0,2),total:3,next_cursor:url.searchParams.has('cursor')?null:'c2',summary:{total:3,confirmed:0,needs_review:3},generation};
  else if(/\/clusters\/c\d\/golden$/.test(path)){const id=path.split('/').at(-2)!,item=items.find(value=>value.cluster_id===id)!;body={cluster_id:id,fields:{name:{value:item.display_name},phone:{value:item.phone},email:{value:item.email},address:{value:'123 Hà Nội'},dob:{value:'1990-01-01'},government_id:{value:'001190000001'}},status:'DRAFT',generation};}
  else if(path.endsWith('/golden/export'))return route.fulfill({status:200,contentType:'text/csv',headers:{'Content-Disposition':'attachment; filename="golden-records.csv"'},body:'Họ tên,Điện thoại\nNguyễn Minh An,0901234560'});
  else if(path.endsWith('/runs')||path.endsWith('/upload-sessions'))body={items:[],total:0,next_cursor:null};
  await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
 });
});
test('one hồ sơ đại diện screen supports read-only details and individual selection across pages',async({page},info)=>{
 await page.goto('/app/sessions/r/golden');
 await expect(page.getByRole('heading',{name:'Hồ sơ đại diện',exact:true})).toBeVisible();
 await expect(page.getByRole('link',{name:'Duyệt hồ sơ đại diện',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Chốt phiên',exact:true})).toHaveCount(0);
 await page.getByRole('checkbox',{name:'Chọn Nguyễn Minh An',exact:true}).check();
 await page.getByRole('button',{name:'Trang tiếp',exact:true}).click();
 await page.getByRole('checkbox',{name:'Chọn Lê Văn Cường',exact:true}).check();
 await page.getByRole('button',{name:'Xem chi tiết Lê Văn Cường',exact:true}).click();
 await expect(page.getByRole('dialog').getByText('123 Hà Nội')).toBeVisible();
 await expect(page.getByRole('dialog').getByRole('button',{name:/Lưu|Xác nhận/})).toHaveCount(0);
 await page.getByRole('dialog').getByRole('button',{name:'Đóng',exact:true}).click();
 const request=page.waitForRequest(r=>r.url().endsWith('/golden/export'));
 const download=page.waitForEvent('download');
 await page.getByRole('button',{name:'Xuất CSV (2)',exact:true}).click();
 expect((await request).postDataJSON()).toMatchObject({cluster_ids:['c1','c3'],expected_cluster_revision:1,expected_golden_revision:2});
 expect((await download).suggestedFilename()).toBe('golden-records.csv');
 await expect(page.getByText('Đã gửi tệp CSV đến trình duyệt.')).toBeVisible();
 await page.screenshot({path:info.outputPath('golden-workspace.png'),fullPage:true});
});
test('select all includes unvisited pages, exclusions persist, and failure preserves selection',async({page})=>{
 await page.goto('/app/sessions/r/golden');
 await page.getByRole('button',{name:'Chọn tất cả (3)',exact:true}).click();
 await page.getByRole('button',{name:'Trang tiếp',exact:true}).click();
 await expect(page.getByRole('checkbox',{name:'Chọn Lê Văn Cường',exact:true})).toBeChecked();
 await page.getByRole('checkbox',{name:'Chọn Lê Văn Cường',exact:true}).uncheck();
 await page.route('**/workflow/golden/export',route=>route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({detail:'Thử lại xuất CSV'})}));
 const request=page.waitForRequest(r=>r.url().endsWith('/golden/export'));
 await page.getByRole('button',{name:'Xuất CSV (2)',exact:true}).click();
 const body=(await request).postDataJSON();expect(body.excluded_cluster_ids).toEqual(['c3']);expect(body.cluster_ids).toBeUndefined();
 await expect(page.getByRole('alert').filter({hasText:'Thử lại xuất CSV'})).toBeVisible();
 await expect(page.getByRole('button',{name:'Xuất CSV (2)',exact:true})).toBeEnabled();
 await page.getByRole('button',{name:'Trang trước',exact:true}).click();
 await expect(page.getByRole('checkbox',{name:'Chọn Nguyễn Minh An',exact:true})).toBeChecked();
});
test('legacy hồ sơ đại diện subpages return to the single screen on mobile',async({page},info)=>{
 await page.setViewportSize({width:390,height:844});
 await page.goto('/app/sessions/r/golden/export');await expect(page).toHaveURL(/\/golden$/);
 await expect(page.getByRole('button',{name:'Chọn tất cả (3)',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Chọn tất cả (3)',exact:true}).click();
 await page.screenshot({path:info.outputPath('golden-mobile.png'),fullPage:true});
 await page.goto('/app/sessions/r/golden/records?cluster=c1');await expect(page).toHaveURL(/\/golden\?cluster=c1$/);
 await expect(page.getByRole('dialog')).toBeVisible();
});

test('viewers can inspect records but cannot export',async({page})=>{
 await page.route('**/auth/me',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({id:'golden-e2e',email:'viewer@example.test',role:'VIEWER',capabilities:['read'],csrf_token:'test'})}));
 await page.goto('/app/sessions/r/golden');
 await page.getByRole('button',{name:'Chọn tất cả (3)',exact:true}).click();
 await expect(page.getByRole('button',{name:'Xuất CSV (3)',exact:true})).toBeDisabled();
 await page.getByRole('button',{name:'Xem chi tiết Nguyễn Minh An',exact:true}).click();
 await expect(page.getByRole('dialog').getByText('123 Hà Nội')).toBeVisible();
});

test('Bot introduces only the first child window and stays quiet when returning or opening another record',async({page})=>{
 await page.goto('/app/sessions/r/golden');
 await page.getByRole('button',{name:'Master Bot giới thiệu',exact:true}).click();
 const bubble=page.getByRole('region',{name:'Trò chuyện với Master Bot'});
 await expect(bubble).toContainText('Bước 4');
 await page.getByRole('button',{name:'Thu gọn Master Bot',exact:true}).click();
 await page.getByRole('button',{name:'Xem chi tiết Nguyễn Minh An',exact:true}).click();
 await expect(bubble).toContainText('thông tin đã được giữ lại');
 await page.getByRole('button',{name:'Thu gọn Master Bot',exact:true}).click();
 await page.getByRole('dialog').getByRole('button',{name:'Đóng',exact:true}).click();
 await expect(bubble).not.toBeVisible();
 await page.getByRole('button',{name:'Xem chi tiết Trần Thị Bình',exact:true}).click();
 await expect(page.getByRole('dialog')).toBeVisible();
 await expect(bubble).not.toBeVisible();
 await page.getByRole('dialog').getByRole('button',{name:'Đóng',exact:true}).click();
 await expect(bubble).not.toBeVisible();
 await page.getByRole('button',{name:'Mở trò chuyện Master Bot'}).click();
 await expect(bubble).toContainText('thông tin đã được giữ lại');
});

for(const width of [390,1280])test(`hồ sơ đại diện selection toolbar aligns with large counts at ${width}px`,async({page},info)=>{
 await page.setViewportSize({width,height:844});
 await page.route('**/workflow/golden/index?**',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({items:items.slice(0,2),total:1721,next_cursor:'c2',summary:{total:1721,confirmed:0,needs_review:1721},generation:{...generation,clusters:1721,covered_records:2000,total_records:2000}})}));
 await page.goto('/app/sessions/r/golden');
 const select=page.getByRole('button',{name:'Chọn tất cả (1.721)',exact:true}),counter=page.getByText('Đã chọn 0 / 1.721',{exact:true});
 await expect(select).toBeVisible();await expect(counter).toBeVisible();
 expect(await counter.evaluate(el=>Number(getComputedStyle(el).fontWeight))).toBeGreaterThanOrEqual(600);
 const controls=select.locator('..');
 expect(await controls.evaluate(el=>getComputedStyle(el).alignItems)).toBe('center');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:info.outputPath('toolbar-empty.png'),fullPage:true});
 await select.click();await expect(page.getByText('Đã chọn 1.721 / 1.721',{exact:true})).toBeVisible();
 await page.screenshot({path:info.outputPath('toolbar-selected.png'),fullPage:true});
});
test('metric explanation disappears five seconds after its last typed character and can be reopened',async({page})=>{
 await page.clock.install();
 await page.goto('/app/sessions/r/golden');
 await expect(page.getByRole('button',{name:'Giải thích chỉ số Đã chọn để xuất',exact:true})).toBeVisible();
 await page.clock.pauseAt(await page.evaluate(()=>Date.now()+1000));
 await page.getByRole('button',{name:'Giải thích chỉ số Đã chọn để xuất',exact:true}).click();
 const bubble=page.getByRole('region',{name:'Trò chuyện với Master Bot'});
 await page.clock.runFor(4200);
 await expect(bubble).toContainText('những bản bạn đã bỏ chọn.');await expect(bubble).toBeVisible();
 await page.clock.runFor(5000);await expect(bubble).not.toBeVisible();
 await page.getByRole('button',{name:'Mở trò chuyện Master Bot'}).click();await page.clock.runFor(4200);
 await expect(bubble).toContainText('những bản bạn đã bỏ chọn.');
});
