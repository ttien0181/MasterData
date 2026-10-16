// Read the actual Next.js website against Docker API/PG, with a synthetic QA actor.
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');
const report = JSON.parse(readFileSync('artifacts/final-mvp-20261015/docker-live-tc-04/report.json', 'utf8'));
if (!/^[0-9a-f-]{36}$/.test(report.run_id)) throw new Error('QA_RUN_ID');
const email = execFileSync('docker', ['compose', 'exec', '-T', 'database', 'psql', '-U', 'masterone', '-d', 'masterone', '-At', '-c',
  `SELECT u.email FROM users u JOIN mdm_resolution_runs r ON r.owner_user_id=u.id WHERE r.id='${report.run_id}';`], { encoding: 'utf8' }).trim();
if (!email.startsWith('final-live-') || !email.endsWith('@example.com')) throw new Error('SYNTHETIC_QA_ACTOR_REQUIRED');
const out = resolve('artifacts/final-mvp-20261015/browser-05');
mkdirSync(out, { recursive: false });
const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = []; const checks = [];
page.on('pageerror', e => errors.push(e.message));
try {
  await page.goto('http://localhost:3000/login');
  await page.getByLabel('Email', { exact: true }).fill(email);
  await page.getByLabel('Mật khẩu', { exact: true }).fill('qa-only-synthetic-passphrase');
  await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  await page.waitForURL('**/app**', { timeout: 15000 });
  for (const tab of ['data', 'decisions', 'clusters', 'golden', 'utilities']) {
    await page.goto(`http://localhost:3000/app/sessions/${report.run_id}/${tab}`);
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(700);
    const text = await page.locator('body').innerText();
    if (/Application error|Internal Server Error/.test(text)) throw new Error('UI_RENDER_' + tab);
    if (!text.includes('Tổng quan') && !text.includes('Golden')) throw new Error('AUTHENTICATED_UI_' + tab);
    await page.screenshot({ path: resolve(out, tab + '.png'), fullPage: true });
    checks.push({ tab, status: 'PASS' });
  }
  await page.getByRole('button', { name: 'Kiểm tra điều kiện học ngưỡng', exact: true }).click();
  await page.getByText('Chưa đủ nhãn có provenance và TRAIN/DEV độc lập đã xác minh; không học hoặc đổi ngưỡng.', { exact: true }).waitFor();
  checks.push({ action: 'actual-learning-readiness-no-training', status: 'PASS' });
  await page.goto(`http://localhost:3000/app/sessions/${report.run_id}/clusters`);
  await page.getByRole('heading', { name: 'Split và Remerge', exact: true }).waitFor();
  const groupsResponse = await page.request.get(`http://localhost:8001/api/v1/mdm/runs/${report.run_id}/workflow/clusters`);
  if (!groupsResponse.ok()) throw new Error('CLUSTER_API');
  const groups = (await groupsResponse.json()).items;
  const target = groups.find(c => c.member_count === 2);
  await page.getByLabel(/Nhóm trên trang hiện tại/).selectOption(target.cluster_id);
  await page.getByRole('group', { name: 'Chọn thành viên partition 1; phần còn lại thuộc partition 2' }).getByRole('checkbox').first().check();
  await page.getByLabel('Lý do', { exact: true }).fill('Browser QA preview only; no structural execution from this read smoke');
  await page.getByRole('button', { name: 'Preview tách', exact: true }).click();
  await page.getByRole('button', { name: 'Xác nhận thay đổi cấu trúc và tạo generation mới', exact: true }).waitFor();
  await page.screenshot({ path: resolve(out, 'split-preview.png'), fullPage: true });
  checks.push({ action: 'actual-structure-preview-ui-and-backend-token', status: 'PASS', execute_clicked: false });
  if (errors.length) throw new Error('BROWSER_PAGE_ERRORS');
  writeFileSync(resolve(out, 'report.json'), JSON.stringify({ status: 'PASS', run_id: report.run_id,
    scope: 'Authenticated actual website, real API and Docker worker-produced run; learning readiness and structure preview only, no execution mutations',
    checks, page_errors: errors, screenshots_contain_synthetic_QA_data_only: true }, null, 2));
  console.log(JSON.stringify({ status: 'PASS', pages: checks.length }));
} catch (e) {
  writeFileSync(resolve(out, 'failure.json'), JSON.stringify({ status: 'FAIL', checks, page_errors: errors, error: e.message }, null, 2));
  throw e;
} finally { await browser.close(); }
