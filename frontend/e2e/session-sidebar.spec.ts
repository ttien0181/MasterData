import { expect, test, type Page } from '@playwright/test';

async function session(page: Page, state = 'GOLDEN_REVIEW') {
  const writes: string[] = [];
  await page.route('**/api/v1/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    if (req.method() !== 'GET') writes.push(path);
    let body: unknown = { items: [], total: 0, next_cursor: null };
    if (path.endsWith('/auth/me')) body = { id: 'sidebar-owner', email: 'sidebar@example.test', role: 'STEWARD', csrf_token: 'synthetic', capabilities: ['read'] };
    else if (path.endsWith('/workflow')) body = { run_id: 'r', workflow_version: 2, workflow_state: state, workflow_revision: 1, decision_revision: 1, cluster_revision: state === 'GOLDEN_REVIEW' ? 1 : 0, golden_revision: 0, counts: {}, allowed_actions: [], blockers: [], active_operation: null, retryable_operation: null };
    else if (path.endsWith('/data')) body = {
      files: [], schema: ['name'], profile: { record_count: 10, source_counts: { CRM: 10 }, quality_flags: {}, records_with_quality_flags: 0 },
      samples: { items: Array.from({ length: 10 }, (_, i) => ({ record_id: `r${i}`, source_system: 'CRM', raw_fields: { name: `Synthetic person ${i}`, address: 'Synthetic address' }, normalized_fields: {}, extra_fields: {}, quality_flags: [] })), total: 10, next_cursor: null },
    };
    else if (path.endsWith('/pairs/summary')) body = { total: 0, needs_review: 0, accepted: 0, denied: 0, kept_separate: 0, deferred: 0, blocked: 0, manual_available: 0, auto_available: 0, decision_revision: 1, groups: [] };
    else if (path.endsWith('/golden/resolution')) body = { resolution: null, undo_operation_id: null, stale: false };
    else if (path.endsWith('/golden')) body = { items: [{ cluster_id: 'c', status: 'DRAFT', fields: { name: { value: 'Synthetic person', provenance: 'SURVIVORSHIP' } } }], total: 1, next_cursor: null };
    else if (path.endsWith('/structure')) body = { enabled: true, can_manage: true };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  return writes;
}

const sidebar = (page: Page) => page.getByRole('complementary', { name: 'Điều hướng phiên' });
const roots = (page: Page) => page.getByRole('navigation', { name: 'Các bước trong phiên' }).locator('ol > li > div > a');

test('desktop sidebar sticks, collapses and retains its width across all four pages', async ({ page }) => {
  const writes = await session(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/app/sessions/r/data');
  await expect(roots(page)).toHaveCount(4);
  await page.getByRole('heading', { name: 'Phân tích dữ liệu', exact: true }).waitFor();
  await page.getByRole('link', { name: 'Dữ liệu tải lên', exact: true }).click();
  await page.getByRole('table', { name: 'Bản ghi dữ liệu' }).waitFor();
  await page.evaluate(() => window.scrollTo(0, 600));
  await expect.poll(async () => Math.round((await sidebar(page).boundingBox())!.y)).toBe(94);
  const width = (await page.locator('.workflow-session-content').boundingBox())!.width;
  await page.getByRole('button', { name: 'Thu gọn thanh điều hướng', exact: true }).click();
  expect((await page.locator('.workflow-session-content').boundingBox())!.width).toBeGreaterThan(width);
  for (const [index, tab] of ['data', 'decisions', 'clusters', 'golden'].entries()) {
    await roots(page).nth(index).click();
    await expect(page).toHaveURL(new RegExp(`/${tab}$`));
    await expect(page.getByRole('button', { name: 'Mở rộng thanh điều hướng', exact: true })).toBeVisible();
    await expect(roots(page).nth(index)).toHaveAttribute('aria-current', 'page');
  }
  expect(writes).toEqual([]);
});

test('nested links navigate to real sections, support reload and never compute', async ({ page }) => {
  const writes = await session(page);
  await page.goto('/app/sessions/r/data');
  const records = page.getByRole('link', { name: 'Dữ liệu tải lên', exact: true });
  await expect(page.getByRole('link', { name: 'Phân tích dữ liệu', exact: true })).toBeVisible();
  await records.click();
  await expect(page).toHaveURL(/\/data\/records$/);
  await expect(page.locator('#data-records')).toBeInViewport();
  await page.reload();
  await expect(page.locator('#data-records')).toBeInViewport();
  await page.getByRole('button', { name: 'Thu gọn mục con Tổng quan dữ liệu', exact: true }).click();
  await expect(records).not.toBeVisible();
  await page.getByRole('button', { name: 'Mở mục con Tổng quan dữ liệu', exact: true }).click();
  await expect(records).toBeVisible();
  await page.getByRole('button', { name: 'Mở mục con Golden record', exact: true }).click();
  await page.getByRole('link', { name: 'Giá trị và nguồn gốc', exact: true }).click();
  await expect(page).toHaveURL(/\/golden#golden-records$/);
  await expect(page.locator('#golden-records')).toBeInViewport();
  expect(writes).toEqual([]);
});

test('pending steps stay readable and show readiness without nested actions', async ({ page }) => {
  const writes = await session(page, 'DATA_READY');
  await page.goto('/app/sessions/r/data');
  await expect(roots(page)).toHaveCount(4);
  await expect(roots(page).filter({ hasText: 'Chưa sẵn sàng' })).toHaveCount(3);
  await expect(page.getByRole('button', { name: /mục con Golden record/ })).toHaveCount(0);
  await roots(page).nth(2).click();
  await expect(page.getByText('Chưa có nhóm được tạo.',{exact:true})).toBeVisible();
  expect(writes).toEqual([]);
});

test('mobile menu closes on Escape or navigation and preserves the four pages in both themes', async ({ page }) => {
  const writes = await session(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/app/sessions/r/data');
  const toggle = sidebar(page).getByRole('button', { name: /ĐIỀU HƯỚNG PHIÊN/ });
  const nav = page.getByRole('navigation', { name: 'Các bước trong phiên' });
  await expect(nav).not.toBeVisible();
  await toggle.click();
  await page.keyboard.press('Escape');
  await expect(toggle).toBeFocused();
  await expect(nav).not.toBeVisible();
  for (const [index, tab] of ['data', 'decisions', 'clusters', 'golden'].entries()) {
    await toggle.click();
    await roots(page).nth(index).click();
    await expect(page).toHaveURL(new RegExp(`/${tab}$`));
    await expect(nav).not.toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.getByRole('button', { name: 'Bật giao diện tối', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await toggle.click();
  await expect(roots(page)).toHaveCount(4);
  await page.mouse.click(8, 10);
  await expect(nav).not.toBeVisible();
  expect(writes).toEqual([]);
});
