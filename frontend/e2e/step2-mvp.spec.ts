import { expect, test } from '@playwright/test';

test('step two uses four coordinated groups and shows real agent evidence without triggering LLM', async ({ page }) => {
  const writes: string[] = [], queries: string[] = [], scopes: unknown[] = [];
  const groups = ['A_STRONG', 'A_CONTACT', 'B_SUPPORT', 'B_CONFLICT'].map(key => ({ key, total: 1, pending: 1, unresolved: 1, manual_available: 1, subgroups: [] }));
  const pair = { pair_id: 'p', revision: 0, decision: 'UNDECIDED', mvp_group: 'B_CONFLICT', review_group: 'B', review_subgroup: 'B_AGENT_DISAGREEMENT', score_unit: 'p119_rule_v1', assessment_id: 'assessed',
    cluster_context:{revision:4,stale:false,left_cluster:null,right_cluster:null},
    eligibility: { manual_allowed: true, auto_allowed: false, blocked_reasons: [] },
    left_record: { record_id: 'a', source_system: 'CRM', raw_fields: { name: 'Nguyễn An', phone: '0912345678' } },
    right_record: { record_id: 'b', source_system: 'ERP', raw_fields: { name: 'Nguyễn An', phone: '0912345678' } },
    baseline: { id: 'p', left_id: 'a', right_id: 'b', decision: 'REVIEW', rule_conclusion: 'MERGEABLE', score: 7, primary_group: 'A', subgroup: 'A1_NAME_PHONE', coordinator_version: 'r1_v1', agent_disagreement: true, field_evidence: [], embedding_evidence: { name: { status: 'OK', cosine: .3 }, address: { status: 'NOT_AVAILABLE', cosine: null } } } };
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (request.method() !== 'GET') writes.push(path);
    if (path.endsWith('/pairs') || path.endsWith('/pairs/summary')) queries.push(url.searchParams.get('taxonomy') || '');
    let body: unknown = { items: [], total: 0, next_cursor: null };
    if (path.endsWith('/bulk-decisions/preview')) { scopes.push(request.postDataJSON().scope); body = { total: 1, applicable: 0, blocked: 1, skipped: 0, workflow_revision: 1, decision_revision: 1 }; }
    else if (path.endsWith('/auth/me')) body = { id: 'mvp-ui', email: 'mvp@example.test', role: 'STEWARD', csrf_token: 'synthetic', capabilities: ['read'] };
    else if (path.endsWith('/workflow')) body = { run_id: 'r', workflow_version: 2, workflow_state: 'DECIDE_REVIEW', workflow_revision: 1, decision_revision: 1, assessment_id: 'assessed', cluster_revision: 0, golden_revision: 0, counts: { records: { value: 8, unit: 'records' }, candidates: { value: 4, unit: 'pairs' }, pairs: { value: 4, unit: 'pairs' } }, allowed_actions: ['decide-pair', 'bulk-decisions'], blockers: [], active_operation: null, retryable_operation: null, matching_agents: { status: 'OK', coverage: { expected_pairs: 4, rule_pairs: 4, embedding_pairs: 4 } } };
    else if (path.endsWith('/pairs/summary')) body = { total: 4, needs_review: 4, accepted: 0, denied: 0, kept_separate: 0, deferred: 0, blocked: 0, manual_available: 4, auto_available: 0, decision_revision: 1, assessment_id: 'assessed', assessment: { mvp_step2: true, coordinator: { version: 'r1_v1' } }, groups };
    else if (path.endsWith('/pairs/p')) body = pair;
    else if (path.endsWith('/pairs')) body = { items: [pair], total: 1, next_cursor: null, offset: 0 };
    await route.fulfill({ json: body });
  });
  await page.goto('/app/sessions/r/decisions');
  const menu = page.getByRole('group', { name: 'Nhóm', exact: true });
  await expect(menu.getByRole('button')).toHaveCount(5);
  await expect(page.locator('#decisions-pairs').getByRole('textbox', { name: 'Tìm hồ sơ' })).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Quyết định' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Kết quả duyệt' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Thông tin hỗ trợ đối chiếu' })).toHaveCount(0);
  await expect(page.getByRole('group', { name: 'Giảm so sánh' })).toHaveCount(0);
  await page.screenshot({ path: '../artifacts/step2-minimal-desktop.png', fullPage: true });
  await expect(menu.getByText('Nhóm C', { exact: true })).toHaveCount(0);
  await menu.getByRole('button', { name: /Có thông tin mâu thuẫn/ }).click();
  await expect(menu.getByRole('button', { name: /Có thông tin mâu thuẫn/ })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'Đối chiếu cặp p', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Theo quy tắc', exact:true })).toBeVisible();
  await expect(page.getByText(/Ngữ cảnh nhóm|chưa có nhóm được công bố/)).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'So sánh tên và địa chỉ' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Tổng hợp' })).toBeVisible();
  await page.screenshot({path:'../artifacts/step2-compact-evidence.png',fullPage:true});
  await expect(page.getByText('Tên: 0,300', { exact: true })).toBeVisible();
  await page.screenshot({ path: '../artifacts/step2-mvp-evidence.png', fullPage: true });
  await page.getByRole('button', { name: 'Đóng', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: '../artifacts/step2-minimal-mobile.png', fullPage: true });
  expect(queries.length).toBeGreaterThan(1);
  expect(queries.every(value => value === 'mvp_v1')).toBe(true);
  expect(writes).toEqual([]);
  await page.getByRole('button', { name: 'Đồng ý gộp Có thông tin mâu thuẫn', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByText('Không có cặp đủ điều kiện Đồng ý gộp')).toBeVisible();
  expect(scopes.length).toBeGreaterThan(0);
  expect(scopes.every(scope => JSON.stringify(scope) === JSON.stringify({ filter: { group: 'B_CONFLICT', visibility: 'review', taxonomy: 'mvp_v1' } }))).toBe(true);
  expect(writes.every(path => path.endsWith('/bulk-decisions/preview'))).toBe(true);
});
