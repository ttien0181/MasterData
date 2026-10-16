// Actual local website + API + Docker worker. Never prints QA credentials.
import { createRequire } from 'node:module';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');
const base = resolve('artifacts/api-multiagent-20261015');
const out = resolve(base, process.argv[2] || 'browser-02');
mkdirSync(out, { recursive: false });
const credentials = JSON.parse(readFileSync(resolve(base, 'live-assistance-01/qa-access.private.json'), 'utf8'));
const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
let run;
try {
  await page.goto('http://localhost:3000/login');
  await page.locator('input[type=email]').fill(credentials.email);
  await page.locator('input[type=password]').fill(credentials.password);
  await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  await page.waitForURL('**/app**', { timeout: 15000 });
  // Login via the API as well to obtain the CSRF token without reading cookie secrets.
  const login = await page.request.post('http://localhost:8001/api/v1/auth/login', {
    headers: { Origin: 'http://localhost:3000' }, data: credentials,
  });
  if (!login.ok()) throw new Error('QA_LOGIN_FAILED');
  const { csrf_token } = await login.json();
  const source = readFileSync(resolve(base, 'fixture-check/tc3/source-only.csv'), 'utf8').trimEnd().split('\n').slice(0, 4).join('\n') + '\n';
  const uploaded = await page.request.post('http://localhost:8001/api/v1/mdm/runs', {
    headers: { Origin: 'http://localhost:3000', 'X-CSRF-Token': csrf_token },
    multipart: { workflow_version: '2', files: { name: 'synthetic-three-record-ui-smoke.csv', mimeType: 'text/csv', buffer: Buffer.from(source) } },
  });
  if (!uploaded.ok()) throw new Error('QA_UPLOAD_FAILED_' + uploaded.status());
  run = (await uploaded.json()).run_id;
  await page.goto(`http://localhost:3000/app/sessions/${run}/data`);
  const process = page.getByRole('button', { name: 'Xử lý dữ liệu', exact: true });
  await process.waitFor();
  await require('@playwright/test').expect(process).toBeEnabled({ timeout: 30000 });
  await page.screenshot({ path: resolve(out, 'ready.png'), fullPage: true });
  const requestPromise = page.waitForResponse(response => response.url().endsWith(`/runs/${run}/workflow/process`) && response.request().method() === 'POST');
  await process.click();
  const response = await requestPromise;
  if (response.status() !== 202) throw new Error('UI_PROCESS_FAILED_' + response.status());
  await page.waitForURL('**/decisions', { timeout: 15000 });
  let state;
  const deadline = Date.now() + 180000;
  while (Date.now() < deadline) {
    const current = await page.request.get(`http://localhost:8001/api/v1/mdm/runs/${run}/workflow`);
    state = await current.json();
    if (state.retryable_operation) throw new Error('UI_WORKER_FAILED');
    if (state.workflow_state === 'DECIDE_REVIEW' && !state.active_operation) break;
    await page.waitForTimeout(600);
  }
  if (state.workflow_state !== 'DECIDE_REVIEW' || state.active_operation) throw new Error('UI_TIMEOUT');
  await page.reload();
  await page.getByRole('heading', { name: 'Tổng quan phân loại' }).waitFor();
  await page.screenshot({ path: resolve(out, 'review.png'), fullPage: true });
  const telemetry = await page.request.get(`http://localhost:8001/api/v1/mdm/runs/${run}/workflow/telemetry`);
  if (!telemetry.ok()) throw new Error('TELEMETRY_READ_FAILED');
  if (errors.length) throw new Error('BROWSER_PAGE_ERRORS');
  writeFileSync(resolve(out, 'report.json'), JSON.stringify({ status: 'PASS', run_id: run, records: 3,
    scope: 'Browser functional smoke; excluded from truth benchmark', one_processing_click: true,
    process_http_status: response.status(), final_state: state.workflow_state, automatic_cluster_revision: state.cluster_revision,
    page_errors: errors, telemetry: await telemetry.json() }, null, 2));
  console.log(JSON.stringify({ status: 'PASS', run_id: run, one_processing_click: true }));
} catch (error) {
  writeFileSync(resolve(out, 'failure.json'), JSON.stringify({ status: 'FAIL', run_id: run, error: error.message, page_errors: errors }, null, 2));
  throw error;
} finally { await browser.close(); }
