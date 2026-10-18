import { expect as baseExpect, test, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
const root = path.resolve(import.meta.dirname, '../..');
const out = path.join(root, 'artifacts/session-workflow-2sessions/20261012-fe');
const base = 'http://localhost:8001/api/v1/mdm';
function fixture(input: object) { return JSON.parse(execFileSync(path.join(root, '.venv/Scripts/python.exe'), ['scripts/session_workflow_browser_fixture.py'], { cwd: root, input: JSON.stringify(input), env: { ...process.env, PYTHONPATH: path.join(root, 'backend/src') }, encoding: 'utf8' })); }
const expect = baseExpect.configure({ timeout: 20_000 });
test.use({ trace: 'off', screenshot: 'off' }); // Auth cookies, CSRF and signed upload URLs must never enter traces.
test('live QA upload through finalized hệ thống hồ sơ đại diện, fresh context and CSV history', async ({ page, browser }) => {
  test.skip(process.env.SESSION_WORKFLOW_QA !== '1', 'Requires existing .env.qa API/worker and QA frontend port13000');
  test.setTimeout(600_000); mkdirSync(out, { recursive: true });
  const credentials = fixture({ action: 'seed' }); const checks: Record<string, unknown> = {};
  const network: object[] = [];
  page.on('response', response => { const address = new URL(response.url()); if (address.origin === 'http://localhost:8001') { network.push({ method: response.request().method(), path: address.pathname, status: response.status() }); writeFileSync(path.join(out, 'browser-network-redacted.json'), JSON.stringify(network, null, 2)); } });
  page.on('response', async response => {
    const address = new URL(response.url());
    if (address.origin === 'http://localhost:8001' && address.pathname.includes('/workflow/') && response.status() === 409) {
      const detail = (await response.json()).detail; const payload = response.request().postDataJSON();
      network.push({ status: 409, code: detail.code, revision: detail.revision, current: detail.current, expected: Object.fromEntries(Object.entries(payload).filter(([key]) => key.startsWith('expected_'))) });
      writeFileSync(path.join(out, 'browser-network-redacted.json'), JSON.stringify(network, null, 2));
    }
  });
  page.on('requestfailed', request => { if (request.method() === 'PUT' && new URL(request.url()).origin !== 'http://localhost:8001') { network.push({ method: 'PUT', target: 'object-storage', failure: request.failure()?.errorText }); writeFileSync(path.join(out, 'browser-network-redacted.json'), JSON.stringify(network, null, 2)); } });
  async function login(p: Page) { await p.goto('/login'); await p.getByLabel('Email', { exact: true }).fill(credentials.email); await p.getByLabel('Mật khẩu', { exact: true }).fill(credentials.password); await p.getByRole('button', { name: 'Đăng nhập', exact: true }).click(); await expect(p.getByRole('heading', { name: 'Tổng quan', exact: true })).toBeVisible(); }
  await login(page);
  await page.getByRole('link', { name: 'Upload / tạo phiên' }).click();
  const csv = 'source_record_id,source_system,name,phone,email,address,loyalty\n1,CRM,Nguyen Van An,0901234567,a@example.test,123 Nguyen Trai Hanoi,Gold\n2,BILL,Nguyen Van An,0901234567,a@example.test,123 Nguyen Trai Hanoi,Silver\n3,CRM,Tran Thi Binh,0987654321,b@example.test,456 Le Loi HCM,Bronze\n';
  await expect(page.locator('input[type=file]').first()).toBeEnabled();
  await page.locator('input[type=file]').first().setInputFiles({ name: 'browser-workflow.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) });
  await expect(page).toHaveURL(/\/app\/uploads\/(?!new)/, { timeout: 90_000 });
  await page.reload();
  await expect(page.getByRole('button', { name: 'Tạo phiên từ dữ liệu đã kiểm tra' })).toBeEnabled({ timeout: 90_000 });
  await page.getByRole('button', { name: 'Tạo phiên từ dữ liệu đã kiểm tra' }).click();
  await expect(page).toHaveURL(/\/app\/sessions\/[^/]+\/data/, { timeout: 90_000 });
  const run = /\/sessions\/([^/]+)/.exec(page.url())![1]; const url = `${base}/runs/${run}/workflow`;
  const db = () => fixture({ action: 'inspect', email: credentials.email, run_id: run });
  const state = async () => { const r = await page.request.get(url); expect(r.status()).toBe(200); return r.json(); };
  await expect(page.getByRole('button', { name: 'Bắt đầu phân loại cặp' })).toBeEnabled({ timeout: 60_000 });
  checks.pre_decide = db(); expect((checks.pre_decide as any).cluster_operations).toBe(0);
  await page.getByRole('button', { name: 'Bắt đầu phân loại cặp' }).click();
  await expect.poll(async () => (await state()).workflow_state, { timeout: 90_000 }).toBe('DECIDE_REVIEW');
  await page.getByRole('link', { name: /Phân loại các cặp bản ghi/ }).click();
  const pairs = (await (await page.request.get(url + '/pairs')).json()).items;
  for (const pair of pairs) if (!['MERGEABLE','BLOCKED','NON_MATCH','NOT_MERGEABLE'].includes(pair.baseline.decision)) {
    await page.getByRole('button', { name: `Không gộp cặp ${pair.pair_id}`, exact: true }).click();
    await expect.poll(async () => (await (await page.request.get(url + '/pairs')).json()).items.find((p: any) => p.pair_id === pair.pair_id).decision).toBe('REJECTED');
  }
  await page.getByRole('button', { name: 'Chọn toàn bộ bộ lọc' }).click();
  await page.getByRole('button', { name: 'Auto gộp cặp đủ điều kiện' }).click();
  const bulkResponse = page.waitForResponse(response => response.url().endsWith('/bulk-decisions') && response.status() === 202);
  await page.getByRole('button', { name: 'Xác nhận thao tác hàng loạt' }).click();
  const bulkOperation = await (await bulkResponse).json();
  await expect.poll(async () => (await (await page.request.get(url + '/operations/' + bulkOperation.operation_id)).json()).status, { timeout: 60_000 }).toBe('COMPLETED');
  checks.bulk_progress = (await (await page.request.get(url + '/operations/' + bulkOperation.operation_id)).json()).progress;
  expect((checks.bulk_progress as any).unit).toBe('pairs');
  await expect.poll(async () => (await state()).active_operation, { timeout: 60_000 }).toBeNull();
  await page.reload(); await page.getByRole('link', { name: /Các nhóm bản ghi/ }).click();
  await expect(page.getByText(/Chưa có nhóm chính thức/)).toBeVisible();
  await page.reload(); await page.goBack(); await page.goForward();
  checks.before_finalize = db(); expect((checks.before_finalize as any).cluster_operations).toBe(0); expect((checks.before_finalize as any).memberships).toBe(0); expect((checks.before_finalize as any).clusters).toBe(0);
  await page.getByRole('link', { name: /Phân loại các cặp bản ghi/ }).click();
  await page.getByRole('button', { name: 'Hoàn tất phân loại và tạo nhóm' }).click();
  await expect.poll(async () => (await state()).workflow_state, { timeout: 90_000 }).toBe('GOLDEN_REVIEW');
  await page.reload();
  const clusters = (await (await page.request.get(url + '/clusters')).json()).items;
  expect(clusters.reduce((n: number, c: any) => n + c.member_count, 0)).toBe(3); expect(clusters.some((c: any) => c.singleton)).toBe(true);
  const duplicate = clusters.find((c: any) => c.member_count > 1); expect(duplicate).toBeTruthy();
  await page.getByRole('button', { name: new RegExp(`Nhóm ${duplicate.cluster_id}`) }).click();
  await page.getByRole('textbox', { name: 'Nhập trực tiếp name', exact: true }).fill('Browser Hệ thống hồ sơ đại diện');
  await page.getByRole('button', { name: /Chọn loyalty từ CRM: Gold/ }).click();
  await page.getByRole('button', { name: /Chọn phone từ CRM:/ }).first().click();
  await page.getByRole('button', { name: 'Lưu lựa chọn hồ sơ đại diện' }).click();
  await expect(page.getByText(/Đã lưu lựa chọn trên server/)).toBeVisible();
  await page.getByRole('button', { name: 'Auto chọn hồ sơ đại diện của nhóm' }).click();
  await expect.poll(async () => (await (await page.request.get(url + `/clusters/${duplicate.cluster_id}/golden`)).json()).fields.name.value).toBe('Browser Hệ thống hồ sơ đại diện');
  await page.reload(); await expect(page.getByText('Browser Hệ thống hồ sơ đại diện', { exact: true }).first()).toBeVisible();
  const fresh = await browser.newContext({ baseURL: 'http://localhost:13000' }); const freshPage = await fresh.newPage();
  await login(freshPage); await freshPage.goto(`/app/sessions/${run}/clusters?cluster=${duplicate.cluster_id}`);
  await expect(freshPage.getByText('Browser Hệ thống hồ sơ đại diện', { exact: true }).first()).toBeVisible();
  const persisted = await (await freshPage.request.get(url + `/clusters/${duplicate.cluster_id}/golden`)).json();
  expect(persisted.selections.loyalty.selection_mode).toBe('SOURCE_CHOICE'); expect(persisted.selections.name.selection_mode).toBe('MANUAL_OVERRIDE'); await fresh.close(); checks.fresh_context_choices = true;
  for (const c of clusters) {
    await page.getByRole('button', { name: new RegExp(`${c.singleton ? 'Singleton' : 'Nhóm'} ${c.cluster_id}`) }).click();
    await expect(page.getByRole('heading', { name: `Chi tiết nhóm ${c.cluster_id}`, exact: true })).toBeVisible();
    const confirm = page.getByRole('button', { name: 'Xác nhận hồ sơ đại diện của nhóm' });
    const firstResponse = page.waitForResponse(r => r.url().endsWith(`/clusters/${c.cluster_id}/golden/confirm`) && r.request().method() === 'POST');
    await confirm.click(); const response = await firstResponse;
    if (response.status() === 409) {
      const detail = (await response.json()).detail; expect(detail.code).toBe('STALE_REVISION');
      await expect(page.locator('.workflow-main').getByRole('alert')).toContainText('Đã tải lại dữ liệu');
      await expect(confirm).toBeEnabled();
      // A second explicit user confirmation after the stale read; FE never replays it.
      const reviewedResponse = page.waitForResponse(r => r.url().endsWith(`/clusters/${c.cluster_id}/golden/confirm`) && r.request().method() === 'POST');
      await confirm.click(); expect((await reviewedResponse).status()).toBe(200); checks.stale_confirmation_reviewed = detail;
    } else expect(response.status()).toBe(200);
    await expect.poll(async () => (await (await page.request.get(url + `/clusters/${c.cluster_id}/golden`)).json()).status).toBe('CONFIRMED');
    await expect(confirm).toBeEnabled();
  }
  await page.getByRole('link', { name: 'Hồ sơ đại diện', exact: true }).click(); await page.getByRole('button', { name: 'Chốt phiên' }).click();
  await expect.poll(async () => (await state()).workflow_state).toBe('FINALIZED');
  await page.getByRole('button', { name: 'Chuẩn bị xuất CSV' }).click();
  const download = page.waitForEvent('download', { timeout: 60_000 }); await page.getByRole('button', { name: 'Xuất CSV', exact: true }).click(); const exported = await download; await exported.saveAs(path.join(out, 'browser-export.csv'));
  const text = readFileSync(path.join(out, 'browser-export.csv'), 'utf8'); expect(text).toContain('Browser Hệ thống hồ sơ đại diện'); expect(text).toContain('Tran Thi Binh');
  checks.csv_server_equivalence = fixture({ action: 'csv', email: credentials.email, run_id: run });
  await page.screenshot({ path: path.join(out, 'qa-final-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: 'Bật giao diện tối' }).click(); await page.setViewportSize({ width: 390, height: 844 }); await page.screenshot({ path: path.join(out, 'qa-final-mobile-dark.png'), fullPage: true });
  await page.getByRole('link', { name: 'Lịch sử xuất / Lịch sử thao tác / Chính sách' }).click();
  const historyDownload = page.waitForEvent('download', { timeout: 60_000 }); await page.getByRole('button', { name: /Tải CSV lịch sử/ }).first().click(); await (await historyDownload).saveAs(path.join(out, 'browser-history.csv'));
  expect(readFileSync(path.join(out, 'browser-history.csv'), 'utf8')).toBe(text);
  const legacy = fixture({ action: 'legacy', ...credentials });
  const legacyBefore = fixture({ action: 'inspect', email: credentials.email, run_id: legacy.run_id });
  await page.goto(`/app/legacy?run=${legacy.run_id}`); await expect(page.locator('.app-header')).toBeVisible();
  await page.reload(); await expect(page.locator('.app-header')).toBeVisible();
  const legacyAfter = fixture({ action: 'inspect', email: credentials.email, run_id: legacy.run_id });
  expect(legacyAfter.state).toBe(legacyBefore.state); expect(legacyAfter.cluster_operations).toBe(0); checks.legacy_read_only = true;
  await page.goto(`/app/sessions/${run}/golden`);
  await page.getByRole('link', { name: 'Lịch sử phiên' }).click(); await expect(page.getByRole('heading', { name: 'Tổng quan', exact: true })).toBeVisible();
  checks.final = db(); expect((checks.final as any).cluster_operations).toBe(1); expect((checks.final as any).memberships).toBe(3); expect((checks.final as any).generations).toBe(1);
  writeFileSync(path.join(out, 'browser-qa.json'), JSON.stringify({ status: 'PASS', run_id: run, checks }, null, 2));
});
