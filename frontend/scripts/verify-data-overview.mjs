import { chromium, expect as baseExpect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
const out = process.env.OVERVIEW_OUTPUT || 'artifacts/data-overview-20261012';
const expect = baseExpect.configure({ timeout: 60000 });
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const login = await context.request.post('http://localhost:8001/api/v1/auth/login', { headers: { Origin: 'http://localhost:3000' }, data: { email: process.env.OVERVIEW_EMAIL, password: process.env.OVERVIEW_PASSWORD } });
  if (!login.ok()) throw new Error('Test account login failed');
  const page = await context.newPage();
  page.setDefaultTimeout(60000);
  const errors = [], checks = [], dataReads = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { const url = new URL(request.url()); if (request.method() === 'GET' && url.pathname.endsWith('/workflow/data')) dataReads.push(url.pathname + url.search); });
  await page.goto(`http://localhost:3000/app/sessions/${process.env.OVERVIEW_RUN}/data`);
  const table = page.getByRole('table', { name: 'Bản ghi dữ liệu', exact: true });
  await expect(table.locator('tbody tr')).toHaveCount(10);
  await expect(page.getByText('Tổng bản ghi', { exact: true })).toBeVisible();
  await expect(page.locator('pre')).toHaveCount(0);
  await expect(page.getByText('Trang 1 / 1000', { exact: true })).toBeVisible();
  checks.push('metrics_and_10_readable_rows_no_json');
  await page.getByRole('button', { name: 'Trang sau', exact: true }).click();
  await expect(page.getByText('Trang 2 / 1000', { exact: true })).toBeVisible();
  await expect(table.locator('tbody tr').first().locator('td').first()).toHaveText('11');
  await page.getByRole('button', { name: 'Trang cuối', exact: true }).click();
  await expect(page.getByText('Trang 1000 / 1000', { exact: true })).toBeVisible();
  await expect(table.locator('tbody tr')).toHaveCount(10);
  await expect(page.getByRole('button', { name: 'Trang sau', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Trang trước', exact: true }).click();
  await expect(page.getByText('Trang 999 / 1000', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Trang đầu', exact: true }).click();
  await expect(page.getByText('Trang 1 / 1000', { exact: true })).toBeVisible();
  checks.push('next_last_previous_first_navigation');
  await page.getByRole('button', { name: 'Chuẩn hóa', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Chuẩn hóa', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await table.locator('tbody tr').first().getByText('Chi tiết', { exact: true }).click();
  await expect(table.locator('tbody tr').first().getByText('Mã bản ghi hệ thống', { exact: true })).toBeVisible();
  checks.push('normalized_and_record_detail');
  if (process.env.OVERVIEW_CHECK_CACHE === '1') {
    await page.getByRole('button', { name: 'Trang sau', exact: true }).click();
    await expect(page.getByText('Trang 2 / 1000', { exact: true })).toBeVisible();
    await table.locator('tbody tr').first().getByText('Chi tiết', { exact: true }).click();
    const readsBefore = dataReads.length;
    const tabs = page.getByRole('navigation', { name: 'Các bước trong phiên' });
    for (const [label, heading] of [[/Phân loại các cặp bản ghi/, 'Phân loại các cặp bản ghi'], [/Các nhóm bản ghi/, 'Các nhóm bản ghi'], [/Golden record/, 'Golden record'], [/Tổng quan dữ liệu/, 'Tổng quan dữ liệu']]) {
      await tabs.getByRole('link', { name: label }).evaluate(element => element.click());
      await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
    }
    await expect(page.getByText('Trang 2 / 1000', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Chuẩn hóa', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(table.locator('tbody tr').first().locator('td').first()).toHaveText('11');
    await expect(table.locator('tbody tr').first().getByText('Mã bản ghi hệ thống', { exact: true })).toBeVisible();
    if (dataReads.length !== readsBefore) throw new Error('Loaded benchmark page was fetched again after tab navigation');
    checks.push('real_10k_session_four_tabs_return_page2_normalized_details_without_refetch');
  }
  await table.locator('tbody tr').first().getByText('Chi tiết', { exact: true }).click();
  await page.getByRole('button', { name: 'Dữ liệu gốc', exact: true }).click();
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({ path: `${out}/desktop.png`, fullPage: true });
  await page.getByRole('button', { name: 'Bật giao diện tối', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.screenshot({ path: `${out}/dark.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  if (!await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)) throw new Error('Mobile horizontal page overflow');
  await page.screenshot({ path: `${out}/mobile.png`, fullPage: true });
  checks.push('dark_theme_mobile_390_no_page_overflow');
  await writeFile(`${out}/browser.json`, JSON.stringify({ run_id: process.env.OVERVIEW_RUN, checks, dataReads, errors }, null, 2));
  if (errors.length) throw new Error('Browser JavaScript errors');
} finally {
  await browser.close();
}
