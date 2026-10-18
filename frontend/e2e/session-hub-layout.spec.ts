import {expect,test} from '@playwright/test';
for(const width of [390,1380])test(`session overview stays compact without redundant upload drafts at ${width}px`,async({page},info)=>{
 await page.setViewportSize({width,height:900});
 const requests:string[]=[];
 await page.addInitScript(()=>localStorage.setItem('masterbot-introductions:hub-test','off'));
 await page.route('**/api/v1/**',async route=>{
  const url=new URL(route.request().url()),path=url.pathname;requests.push(path+url.search);let body:unknown={};
  if(path.endsWith('/auth/me'))body={id:'hub-test',email:'steward@example.test',role:'STEWARD',csrf_token:'test',capabilities:['read','create_run','manage_run']};
  else if(path.endsWith('/upload-sessions'))body={items:[{session_id:'draft1',status:'DRAFT',name:null},{session_id:'draft2',status:'DRAFT',name:null}]};
  else if(path.endsWith('/runs')){
   const runs=[{run_id:'r1',name:null,files:['customers (1).csv','customers (2).csv','customers.csv'],record_count:10022,file_count:3,status:'COMPLETED',created_at:'2026-10-17T16:30:00Z',archived_at:null},{run_id:'r2',name:'Khách hàng quý 3',files:['customers.csv'],record_count:1721,file_count:1,status:'RUNNING',created_at:'2026-10-18T02:00:00Z',archived_at:null}];
   const filtered=runs.filter(run=>(!url.searchParams.get('q')||(run.name??run.files.join(' ')).includes(url.searchParams.get('q')!))&&(!url.searchParams.get('run_status')||url.searchParams.get('run_status')!.split(',').includes(run.status)));
   body={items:filtered,total:filtered.length,next_cursor:null};
  }else if(path.endsWith('/workflow'))body={workflow_version:2};
  await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
 });
 await page.goto('/app');
 await expect(page.getByRole('heading',{name:'Tổng quan',exact:true})).toBeVisible();
 await expect(page.getByText('2 phiên',{exact:true})).toBeVisible();
 await expect(page.getByText('Dữ liệu đang chuẩn bị',{exact:true})).toHaveCount(0);
 await expect(page.getByRole('link',{name:'Upload đang chuẩn bị',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Đóng danh sách phiên',exact:true})).toHaveCount(0);
 await expect(page.getByRole('link',{name:'Tải tệp mới',exact:true})).toHaveAttribute('href','/app/uploads/new');
 const list=page.locator('.sessions-list'),body=page.locator('.sessions-hub-body');
 expect((await list.boundingBox())!.width).toBeCloseTo((await body.boundingBox())!.width,0);
 const search=page.getByRole('textbox',{name:'Tìm phiên',exact:true});expect((await search.boundingBox())!.height).toBeLessThan(50);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:info.outputPath('session-hub.png'),fullPage:true});
 await page.locator('.sessions-row').filter({hasText:'customers (1).csv'}).click();
 await expect(page.getByRole('complementary',{name:'Xem nhanh phiên'})).toBeVisible();
 await page.screenshot({path:info.outputPath('session-hub-preview.png'),fullPage:true});
 await page.getByRole('button',{name:'Đang chạy',exact:true}).click();
 await expect(page.getByText('1 phiên',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Tất cả',exact:true}).click();
 await search.fill('quý 3');await expect(page.getByText('1 phiên',{exact:true})).toBeVisible();
 expect(requests.some(path=>path.includes('/upload-sessions'))).toBe(false);
});
