import { chromium } from '../frontend/node_modules/@playwright/test/index.mjs';
import { readFile, writeFile } from 'node:fs/promises';
const output = 'artifacts/full-system-20261009/permissions';
const { run_id } = JSON.parse(await readFile(`${output}/browser-fixture.json`, 'utf8'));
const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
const page = await browser.newPage();
const result = { scope: 'real Docker frontend/API, synthetic Viewer', cases: [], console_errors: [] };
page.on('pageerror', error => result.console_errors.push({ type: error.name }));
try {
  await page.goto('http://127.0.0.1:13000/login');
  await page.locator('#email').fill('qa-viewer@example.com');
  await page.locator('#password').fill('local-qa-synthetic-password');
  await page.locator('button[type=submit]').click();
  await page.waitForURL('**/app');
  await page.goto(`http://127.0.0.1:13000/app?run=${run_id}&view=auto`);
  await page.waitForLoadState('networkidle');
  if (await page.getByRole('button', { name: '＋ Nạp dữ liệu', exact: true }).count()) throw new Error('Viewer upload control exposed');
  const exportButton = page.getByRole('button', { name: 'Xem trước CSV kết quả →', exact: true });
  await exportButton.waitFor();
  if (!await exportButton.isDisabled()) throw new Error('Viewer export is enabled');
  await page.locator('.resolution-item-open').first().click();
  const save = page.getByRole('button', { name: 'Lưu lựa chọn', exact: true });
  await save.waitFor();
  if (!await save.isDisabled()) throw new Error('Viewer golden mutation enabled');
  for (const radio of await page.locator('input[type=radio]').all()) {
    if (!await radio.isDisabled()) throw new Error('Viewer golden radio enabled');
  }
  result.cases.push({ name: 'viewer_read_only_workspace', status: 'PASS', run_id, upload_hidden: true, export_disabled: true, golden_disabled: true });
  await page.screenshot({ path: `${output}/viewer.png`, fullPage: true });
} catch (error) {
  result.cases.push({ name: 'viewer_read_only_workspace', status: 'FAIL', error_type: error.name, diagnostic: error.message.split('\n')[0].slice(0, 300) });
} finally {
  await writeFile(`${output}/browser-results.json`, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
  await browser.close();
}
process.exitCode = result.cases.some(c => c.status === 'FAIL') ? 1 : 0;
