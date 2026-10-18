import {expect,test} from '@playwright/test';

test.use({baseURL:process.env.DEMO_BASE_URL||'http://localhost:3000'});
test('a real demo exposes groups, editable source choices, paginated golden records and CSV without reviewing pairs',async({page},info)=>{
 test.setTimeout(120000);
 const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
 const entered=await page.request.post('/api/v1/auth/demo',{headers:{Origin:'http://localhost:3000'}});
 expect(entered.status()).toBe(201);
 const identity=await entered.json();
 const headers={Origin:'http://localhost:3000','X-CSRF-Token':identity.csrf_token};
 try{
  const started=await page.request.post('/api/v1/mdm/demo/start',{headers,data:{use_embedding:false}});
  expect(started.ok()).toBe(true);
  const {run_id:run}=await started.json();
  await page.goto(`/app/sessions/${run}/clusters`);
  await expect(page.getByText('8 nhóm mẫu đã được chuẩn bị sẵn.')).toBeVisible({timeout:90000});
  await page.getByRole('button',{name:'Bỏ qua hướng dẫn',exact:true}).click();
  const open=page.locator('[data-demo="group-open"]').first();
  await expect(open).toBeVisible();await open.click();
  const dialog=page.getByRole('dialog');
  await expect(dialog.getByRole('table',{name:'Đối chiếu thành viên nhóm'})).toBeVisible();
  const choice=dialog.locator('[data-demo="source-choice"][aria-pressed="false"]').first();
  await expect(choice).toBeEnabled();await choice.click();
  const save=dialog.getByRole('button',{name:'Lưu thông tin đại diện',exact:true});
  await expect(save).toBeEnabled();await save.click();
  await expect(dialog.getByText(/Đã lưu thay đổi/)).toBeVisible();
  await dialog.getByRole('button',{name:'Đóng chi tiết nhóm',exact:true}).click();
  await page.screenshot({path:info.outputPath('demo-groups.png'),fullPage:true,animations:'disabled'});
  await page.goto(`/app/sessions/${run}/golden`);
  const table=page.getByRole('table',{name:'Hồ sơ đại diện để xuất CSV'});
  await expect(table).toBeVisible();
  await page.getByRole('button',{name:'Trang tiếp',exact:true}).click();
  await expect(page.getByText('Trang 2',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:/Chọn tất cả/}).click();
  await expect(page.locator('[data-demo="golden-export"]')).toBeEnabled();
  // Export one selected record, then the entire result including unopened pages.
  await page.getByRole('button',{name:'Bỏ chọn',exact:true}).click();
  await table.locator('tbody input[type="checkbox"]').first().check();
  const one=page.waitForEvent('download');await page.locator('[data-demo="golden-export"]').click();
  expect((await one).suggestedFilename()).toMatch(/\.csv$/);
  await page.getByRole('button',{name:/Chọn tất cả/}).click();
  const all=page.waitForEvent('download');await page.locator('[data-demo="golden-export"]').click();
  expect((await all).suggestedFilename()).toMatch(/\.csv$/);
  await page.screenshot({path:info.outputPath('demo-golden.png'),fullPage:true,animations:'disabled'});
  expect(errors).toEqual([]);
 }finally{await page.request.post('/api/v1/auth/logout',{headers,timeout:10000}).catch(()=>{});}
});
