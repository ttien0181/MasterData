import { chromium, expect } from '@playwright/test';
const browser = await chromium.launch({ headless: true, executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe' });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  await context.addCookies([{ name: 'masterone_session', value: process.env.PAIR_ORIGINAL_TOKEN, url: 'http://localhost:3000', httpOnly: true, sameSite: 'Lax' }]);
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`http://localhost:3000/app/sessions/${process.env.PAIR_ORIGINAL_RUN}/decisions`);
  await expect(page.getByText('Tổng cặp', { exact: true })).toBeVisible({ timeout: 60000 });
  await expect(page.getByRole('table', { name: 'Danh sách cặp bản ghi', exact: true }).locator('tbody tr')).toHaveCount(10, { timeout: 60000 });
  await page.screenshot({ path: 'artifacts/pair-review-20261012/live/deployed-final.png', fullPage: true });
  if (errors.length) throw new Error('Deployed frontend runtime error');
  console.log('Fresh deployed frontend screenshot verified.');
} finally { await browser.close(); }
