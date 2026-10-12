import { chromium, expect as baseExpect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
const expect = baseExpect.configure({ timeout: 60000 });
const out = process.env.PAIR_OUTPUT;
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
const checks = [], errors = [];
async function open(token, run) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  await context.addCookies([{ name: 'masterone_session', value: token, url: 'http://localhost:3000', httpOnly: true, sameSite: 'Lax' }]);
  const page = await context.newPage();
  page.setDefaultTimeout(60000);
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(`http://localhost:3000/app/sessions/${run}/decisions`);
  const table = page.getByRole('table', { name: 'Danh sách cặp bản ghi', exact: true });
  await expect(table.locator('tbody tr')).toHaveCount(10);
  return { context, page, table };
}
try {
  const original = await open(process.env.PAIR_ORIGINAL_TOKEN, process.env.PAIR_ORIGINAL_RUN);
  const { page, table } = original;
  const originalWrites = [];
  await original.context.route(`**/api/v1/mdm/runs/${process.env.PAIR_ORIGINAL_RUN}/workflow/**`, async route => {
    if (route.request().method() !== 'GET') {
      originalWrites.push(new URL(route.request().url()).pathname);
      if (!route.request().url().endsWith('/bulk-decisions/preview')) throw new Error('Original run write prohibited in verification');
    }
    await route.continue();
  });
  await expect(page.getByText('79.364', { exact: true })).toBeVisible();
  await expect(page.locator('pre')).toHaveCount(0);
  await page.screenshot({ path: `${out}/tc4-desktop.png`, fullPage: true });
  await page.getByRole('button', { name: 'Trang tiếp', exact: true }).click();
  await expect(page.getByText(/Trang 2 \/ 7937/)).toBeVisible();
  await expect(table.locator('tbody tr')).toHaveCount(10);
  await page.getByRole('button', { name: 'Trang cuối', exact: true }).click();
  await expect(page.getByText(/Trang 7937 \/ 7937/)).toBeVisible();
  await expect(table.locator('tbody tr')).toHaveCount(4);
  await page.getByRole('button', { name: 'Trang đầu', exact: true }).click();
  await expect(table.locator('tbody tr')).toHaveCount(10);
  await table.locator('tbody tr').first().getByRole('button', { name: /Đối chiếu cặp/ }).click();
  const comparison = page.getByRole('dialog', { name: 'Đối chiếu cặp bản ghi' });
  await expect(comparison).toBeVisible();
  await expect(comparison.getByRole('columnheader', { name: /^Bản ghi 1/ })).toBeVisible();
  await comparison.getByRole('button', { name: 'Chuẩn hóa', exact: true }).click();
  await comparison.getByRole('checkbox', { name: 'Chỉ hiện khác biệt' }).check();
  await page.screenshot({ path: `${out}/tc4-comparison.png` });
  await page.keyboard.press('Escape');
  await expect(comparison).toHaveCount(0);
  await page.getByLabel('Phạm vi thao tác').selectOption('all');
  const accept = page.getByRole('button', { name: 'Accept theo phạm vi', exact: true });
  if (await accept.isEnabled()) {
    await accept.click();
    const preview = page.getByRole('dialog', { name: 'Xác nhận Accept' });
    await expect(preview.getByText(/cặp có thể áp dụng/)).toBeVisible();
    await page.screenshot({ path: `${out}/tc4-preview-readonly.png` });
    await preview.getByRole('button', { name: 'Hủy', exact: true }).click();
    checks.push('real_TC4_manual_preview_cancel');
  } else {
    await expect(page.getByRole('button', { name: 'Deny theo phạm vi', exact: true })).toBeDisabled();
    checks.push('real_TC4_active_job_review_writes_locked');
  }
  checks.push('real_TC4_79364_pairs_ten_per_page_last_four_source_comparison');
  await page.getByLabel('Group', { exact: true }).selectOption('A');
  await expect(page.getByText(/34 cặp theo bộ lọc/)).toBeVisible();
  await page.getByLabel('SubGroup', { exact: true }).selectOption('A1_NAME_PHONE');
  await expect(page.getByText(/31 cặp theo bộ lọc/)).toBeVisible();
  await page.getByRole('button', { name: 'Bật giao diện tối', exact: true }).click();
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({ path: `${out}/tc4-dark.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  if (!await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)) throw new Error('Mobile page overflow');
  await page.screenshot({ path: `${out}/tc4-mobile.png`, fullPage: true });
  checks.push('real_group_subgroup_filters_dark_mobile_no_document_overflow');
  await original.context.close();

  const fixture = await open(process.env.PAIR_FIXTURE_TOKEN, process.env.PAIR_FIXTURE_RUN);
  const p = fixture.page, t = fixture.table;
  async function bulk(scope, verb, applicable) {
    await p.getByLabel('Phạm vi thao tác').selectOption(scope);
    const previewResponse = p.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/bulk-decisions/preview'));
    await p.getByRole('button', { name: `${verb} theo phạm vi`, exact: true }).click();
    const dialog = p.getByRole('dialog', { name: `Xác nhận ${verb}` });
    const previewStatus = (await previewResponse).status();
    if (previewStatus === 409) {
      await expect(dialog.getByText(/Phiên đã thay đổi. Đóng hộp thoại/)).toBeVisible();
      await expect(dialog.getByRole('button', { name: `Xác nhận ${verb}`, exact: true })).toBeDisabled();
      await dialog.getByRole('button', { name: 'Hủy', exact: true }).click();
      await p.getByRole('button', { name: `${verb} theo phạm vi`, exact: true }).click();
      checks.push('stale_preview_refreshed_then_explicit_repreview_no_automatic_decision_retry');
    }
    await expect(dialog.getByText(`${applicable} cặp có thể áp dụng`, { exact: true })).toBeVisible();
    const done = p.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/bulk-decisions'));
    await dialog.getByRole('button', { name: `Xác nhận ${verb}`, exact: true }).click();
    const result = await (await done).json();
    await expect.poll(async () => {
      const response = await fixture.context.request.get(`http://localhost:8001/api/v1/mdm/runs/${process.env.PAIR_FIXTURE_RUN}/workflow/operations/${result.operation_id}`);
      return (await response.json()).status;
    }, { timeout: 120000, intervals: [1000, 2000] }).toBe('COMPLETED');
    await expect(p.getByText('Đã hoàn thành thao tác hàng loạt', { exact: true })).toBeVisible();
    checks.push(`live_${verb}_${scope}_${applicable}_completed`);
  }
  await t.locator('tbody tr').nth(2).getByRole('checkbox').check();
  await t.locator('tbody tr').nth(3).getByRole('checkbox').check();
  await bulk('selected', 'Accept', 2);
  await p.getByLabel('Group', { exact: true }).selectOption('C');
  await expect(t.locator('tbody tr')).toHaveCount(6);
  await bulk('group', 'Deny', 6);
  await p.getByLabel('Group', { exact: true }).selectOption('A');
  await p.getByLabel('SubGroup', { exact: true }).selectOption('A3_NAME_PHONE_DOB');
  await expect(t.locator('tbody tr')).toHaveCount(4);
  await bulk('subgroup', 'Accept', 4);
  await bulk('all', 'Accept', 5);
  await bulk('all', 'Deny', 2);
  await fixture.context.close();
  await writeFile(`${out}/browser.json`, JSON.stringify({ checks, errors, originalWrites }, null, 2));
  if (errors.length) throw new Error('Browser runtime errors');
} finally { await browser.close(); }
