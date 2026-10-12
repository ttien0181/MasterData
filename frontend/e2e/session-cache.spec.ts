import { expect, test, type Page } from '@playwright/test';

// Real client routing and query/UI stores; only HTTP responses are synthetic.
async function fixture(page: Page) {
  const reads: string[] = [], writes: string[] = [];
  let workflowRevision = 3, goldenRevision = 1, savedName = '', forbidden = false, expired = false;
  const record = (id: number) => ({ record_id: `r${id}`, external_id: String(id), source_system: 'CRM', raw_fields: { name: `Raw ${id}`, email: `contact${id}@example.test`, phone: '0901234567', address: 'Synthetic address', dob: '2000-01-01', government_id: '012345678901' }, normalized_fields: { name: `Normalized ${id}` }, extra_fields: {}, quality_flags: [] });
  const fields = (name: string) => ({ name: { value: name, source_record_id: 'r0', source_system: 'CRM' }, phone: { value: '0901234567' }, email: { value: 'contact@example.test' }, address: { value: 'Synthetic address' }, dob: { value: '2000-01-01' }, government_id: { value: '012345678901' } });
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    let status = 200, body: any = {};
    if (request.method() === 'GET') reads.push(path + url.search); else writes.push(path);
    if (path.endsWith('/auth/me')) body = { id: 'cache-owner', email: 'cache@example.test', role: 'STEWARD', csrf_token: 'synthetic', capabilities: ['read', 'edit_golden'] };
    else if (path.endsWith('/upload-sessions') || path.endsWith('/runs')) body = { items: [], total: 0, next_cursor: null };
    else if (path.endsWith('/workflow')) {
      if (forbidden) { status = 403; body = { detail: 'Read access revoked' }; }
      else body = { run_id: 'r', workflow_version: 2, workflow_state: 'GOLDEN_REVIEW', workflow_revision: workflowRevision, decision_revision: 1, cluster_revision: 1, golden_revision: goldenRevision, counts: { records: { value: 12, unit: 'records' } }, allowed_actions: ['save-golden', 'reopen-decisions'], blockers: [], active_operation: null, retryable_operation: null };
    } else if (path.endsWith('/data')) {
      const offset = Number(url.searchParams.get('offset') || 0);
      body = { files: [], schema: ['name', 'email', 'phone', 'address', 'dob', 'government_id'], profile: { record_count: 12, unit: 'records', quality_flags: {}, field_quality: {}, source_counts: { CRM: 12 } }, samples: { offset, items: Array.from({ length: offset ? 2 : 10 }, (_, i) => record(i + offset)), total: 12, next_cursor: offset ? null : '10' } };
    } else if (path.endsWith('/pairs')) {
      const second = Number(url.searchParams.get('offset') || 0) > 0, id = second ? 'p2' : 'p1';
      body = { items: [{ pair_id: id, baseline: { id, left_id: 'r0', right_id: 'r1', primary_group: 'D', subgroup: 'D1', score: 5, decision: 'REVIEW', field_evidence: [{ field: 'name', value: 'Synthetic evidence' }] }, decision: 'UNDECIDED', revision: 1, score_unit: 'p119_rule_v1', eligibility: { manual_allowed: true, auto_allowed: false, blocked_reasons: [] } }], total: 20, next_cursor: second ? null : 'p2' };
    } else if (path.endsWith('/pairs/summary')) body = {total:20,needs_review:20,accepted:0,denied:0,kept_separate:0,deferred:0,blocked:0,manual_available:20,auto_available:0,decision_revision:1,groups:[{key:'D',total:20,subgroups:[]}]};
    else if (path.endsWith('/golden/resolution')) body = { resolution: null, undo_operation_id: null, stale: false };
    else if (path.endsWith('/clusters')) {
      const second = !!url.searchParams.get('cursor');
      body = { items: [{ cluster_id: second ? 'c2' : 'c', member_count: 2, singleton: false, status: 'READY', member_record_ids: [], evidence: { items: [], total: 0, next_cursor: null } }], total: 2, next_cursor: second ? null : 'c2' };
    } else if (/\/clusters\/[^/]+\/golden$/.test(path)) {
      if (request.method() === 'PUT') {
        if (expired) { status = 401; body = { detail: 'Login expired' }; }
        else { savedName = request.postDataJSON().selections.name.value; workflowRevision++; goldenRevision++; body = { workflow_revision: workflowRevision, golden_revision: goldenRevision }; }
      } else body = { cluster_id: path.split('/').at(-2), cluster_revision: 1, golden_revision: goldenRevision, revision: goldenRevision, status: 'DRAFT', fields: fields(savedName || 'Original Golden'), alternatives: {}, selections: savedName ? { name: { selection_mode: 'MANUAL_OVERRIDE', value: savedName } } : {} };
    } else if (/\/clusters\/[^/]+$/.test(path)) {
      const member = !!url.searchParams.get('cursor'), evidence = url.searchParams.get('evidence_cursor') !== '0';
      body = { cluster_id: path.split('/').at(-1), member_count: 2, singleton: false, status: 'READY', member_record_ids: [], members: { items: [record(member ? 1 : 0)], total: 2, next_cursor: member ? null : 'member2' }, pair_evidence: { items: [{ id: evidence ? 'e2' : 'e1', left_id: 'r0', right_id: 'r1', decision: 'REVIEW', score: 5 }], total: 2, next_cursor: evidence ? null : 'evidence2' }, evidence: {}, workflow_context: {}, llm_stats: {} };
    } else if (path.endsWith('/workflow/golden')) {
      const second = !!url.searchParams.get('cursor');
      body = { items: [{ cluster_id: second ? 'c2' : 'c', status: 'DRAFT', fields: fields(second ? 'Second Golden' : savedName || 'Original Golden') }], total: 2, next_cursor: second ? null : 'golden2' };
    }
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  });
  return { reads, writes, revoke: () => { forbidden = true; }, expire: () => { expired = true; } };
}
async function tab(page: Page, label: RegExp) {
  // Avoid Playwright scrolling to the top navigation before dispatching the click.
  const link = page.getByRole('navigation', { name: 'Các bước trong phiên' }).getByRole('link', { name: label });
  const href = await link.getAttribute('href');
  await link.evaluate(element => (element as HTMLElement).click());
  await expect(page).toHaveURL(new URL(href!, page.url()).href);
  const heading = href!.endsWith('/data') ? 'Tổng quan dữ liệu' : href!.endsWith('/decisions') ? 'Phân loại các cặp bản ghi' : href!.endsWith('/clusters') ? 'Các nhóm bản ghi' : 'Golden record';
  await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
}
const details = (page: Page, key: string) => page.locator(`details[data-view-key="${key}"]`);
async function expand(page: Page, key: string) {
  await details(page, key).evaluate(element => { (element as HTMLDetailsElement).open = true; element.dispatchEvent(new Event('toggle')); });
}
const count = (reads: string[], suffix: RegExp) => reads.filter(path => suffix.test(path)).length;

test('all four URL tabs retain loaded pages, selections, drafts and view state; save refreshes the affected Golden revision', async ({ page }) => {
  const mock = await fixture(page);
  await page.setViewportSize({ width: 900, height: 760 });
  await page.goto('/app/sessions/r/data');
  await expect(page.getByRole('cell', { name: 'Raw 0', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Trang sau', exact: true }).click();
  await expect(page.getByRole('cell', { name: 'Raw 10', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Chuẩn hóa', exact: true }).click();
  await expand(page, 'r10:detail');
  const table = page.locator('[data-scroll-key="records"]');
  await table.evaluate(element => { element.scrollLeft = 150; element.dispatchEvent(new Event('scroll')); });
  await page.evaluate(() => { window.scrollTo(0, 350); window.dispatchEvent(new Event('scroll')); });
  const dataY = await page.evaluate(() => window.scrollY);
  await tab(page, /Phân loại các cặp bản ghi/);
  await expect(page.getByRole('checkbox', { name: 'Chọn cặp p1', exact: true })).toBeVisible();
  await page.getByRole('combobox', { name: 'Group', exact: true }).selectOption('D');
  await expect(page.getByRole('button', { name: 'Trang tiếp', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Trang tiếp', exact: true }).click();
  await page.getByRole('checkbox', { name: 'Chọn cặp p2', exact: true }).check();
  await page.getByRole('textbox', { name: 'Tìm trong trang' }).fill('r0');
  await expand(page, 'pair:p2');
  await page.locator('[data-scroll-key="pairs"]').evaluate(element => { element.scrollLeft = 120; element.dispatchEvent(new Event('scroll')); });
  await tab(page, /Các nhóm bản ghi/);
  await page.getByRole('button', { name: /Nhóm c ·/ }).click();
  await page.getByRole('textbox', { name: 'Chỉnh tay name' }).fill('Draft across tabs');
  await expand(page, 'context:c');
  await page.getByRole('button', { name: 'Trang tiếp', exact: true }).nth(1).click();
  await expect(details(page, 'member:c:r1')).toBeVisible();
  await expand(page, 'member:c:r1');
  await page.getByRole('button', { name: 'Trang tiếp', exact: true }).nth(2).click();
  await expect(details(page, 'evidence:c:e2')).toBeVisible();
  await expand(page, 'evidence:c:e2');
  await page.getByRole('button', { name: 'Trang tiếp', exact: true }).first().click();
  await expect(page.getByRole('button', { name: /Nhóm c2 ·/ })).toBeVisible();
  await page.getByRole('checkbox', { name: 'Chọn nhóm c2' }).check();
  await tab(page, /Golden record/);
  await expect(page.getByText('Original Golden', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Trang tiếp', exact: true }).click();
  await expect(page.getByText('Second Golden', { exact: true })).toBeVisible();
  await expand(page, 'source:c2:name');
  await expand(page, 'reopen');
  await page.getByRole('textbox', { name: 'Lý do' }).fill('Preserved reason');
  const before = [...mock.reads];
  await page.goBack();
  await expect(page.getByRole('textbox', { name: 'Chỉnh tay name' })).toHaveValue('Draft across tabs');
  await expect(page.getByRole('checkbox', { name: 'Chọn nhóm c2' })).toBeChecked();
  await expect(details(page, 'context:c')).toHaveAttribute('open', '');
  await expect(details(page, 'member:c:r1')).toHaveAttribute('open', '');
  await expect(details(page, 'evidence:c:e2')).toHaveAttribute('open', '');
  await page.goForward();
  await expect(page.getByText('Second Golden', { exact: true })).toBeVisible();
  await expect(details(page, 'source:c2:name')).toHaveAttribute('open', '');
  await expect(page.getByRole('textbox', { name: 'Lý do' })).toHaveValue('Preserved reason');
  await tab(page, /Phân loại các cặp bản ghi/);
  await expect(page.getByRole('checkbox', { name: 'Chọn cặp p2' })).toBeChecked();
  await expect(page.getByRole('combobox', { name: 'Group', exact: true })).toHaveValue('D');
  await expect(page.getByRole('textbox', { name: 'Tìm trong trang' })).toHaveValue('r0');
  await expect(details(page, 'pair:p2')).toHaveAttribute('open', '');
  await expect.poll(() => page.locator('[data-scroll-key="pairs"]').evaluate(element => element.scrollLeft)).toBe(120);
  await tab(page, /Tổng quan dữ liệu/);
  await expect(page.getByRole('cell', { name: 'Normalized 10', exact: true })).toBeVisible();
  await expect(details(page, 'r10:detail')).toHaveAttribute('open', '');
  await expect.poll(() => table.evaluate(element => element.scrollLeft)).toBe(150);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(dataY);
  expect(mock.reads).toEqual(before);
  expect(mock.writes).toEqual([]);
  await tab(page, /Các nhóm bản ghi/);
  await page.getByRole('button', { name: 'Lưu lựa chọn Golden' }).click();
  await expect(page.getByText('Đã lưu lựa chọn trên server.', { exact: false })).toBeVisible();
  await tab(page, /Golden record/);
  await page.getByRole('button', { name: 'Trang đầu', exact: true }).click();
  await expect(page.getByText('Draft across tabs', { exact: true })).toBeVisible();
  expect(count(mock.reads, /\/workflow\/data\?/)).toBe(2);
  expect(mock.writes).toHaveLength(1);
});

test('expired login during save clears retained private draft and returns to login', async ({ page }) => {
  const mock = await fixture(page);
  await page.goto('/app/sessions/r/clusters?cluster=c');
  await page.getByRole('textbox', { name: 'Chỉnh tay name' }).fill('Private draft');
  await tab(page, /Golden record/);
  await expect(page.getByText('Original Golden', { exact: true })).toBeVisible();
  await tab(page, /Các nhóm bản ghi/);
  mock.expire();
  await page.getByRole('button', { name: 'Lưu lựa chọn Golden' }).click();
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByRole('textbox', { name: 'Chỉnh tay name' })).toHaveCount(0);
  await expect(page.getByText('Original Golden', { exact: true })).toHaveCount(0);
});

test('read access revocation evicts the tab and cached private records', async ({ page }) => {
  const mock = await fixture(page);
  await page.goto('/app/sessions/r/clusters?cluster=c');
  await page.getByRole('textbox', { name: 'Chỉnh tay name' }).fill('Private draft');
  mock.revoke();
  await page.getByRole('button', { name: 'Đọc lại từ server' }).click();
  await expect(page.locator('.workflow-main').getByRole('alert')).toContainText('Không còn quyền đọc phiên');
  await expect(page.getByRole('textbox', { name: 'Chỉnh tay name' })).toHaveCount(0);
});
