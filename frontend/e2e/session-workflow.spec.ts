import { expect, test, type Page } from '@playwright/test';
async function mockWorkflow(page: Page, viewer = false) {
  let state = 'DATA_READY', revision = 1, goldenRevision = 0;
  const mutations: string[] = []; let selections: Record<string, any> = {}; let stale = false;
  const actions = () => viewer ? [] : state === 'DATA_READY' ? ['start-decide'] : state === 'DECIDE_REVIEW' ? ['decide-pair','bulk-decisions','finalize-decisions','build-cluster-queue'] : state === 'GOLDEN_REVIEW' ? ['save-golden','auto-select','confirm-golden','finalize-golden'] : ['export'];
  await page.route('**/api/v1/**', async route => {
    const req = route.request(), url = new URL(req.url()), pathname = url.pathname; let status = 200, body: any = {};
    if (req.method() !== 'GET') mutations.push(pathname);
    if (pathname.endsWith('/auth/me')) body = { id: 'mock-owner', email: 'synthetic@example.test', csrf_token: 'invalid-mock', role: viewer ? 'VIEWER' : 'STEWARD', capabilities: viewer ? ['read'] : ['read','create_run','start_run','approve_pair','edit_golden','export_data'] };
    else if (pathname.endsWith('/upload-sessions')) body = { items: [] };
    else if (pathname.endsWith('/runs')) body = { items: [{ run_id: 'r', name: 'Synthetic session', status: 'COMPLETED', created_at: new Date().toISOString(), record_count: 2, file_count: 1, source_counts: {} }], total: 1, next_cursor: null };
    else if (pathname.endsWith('/workflow')) body = { run_id: 'r', workflow_version: 2, workflow_state: state, workflow_revision: revision, decision_revision: revision, cluster_revision: state === 'GOLDEN_REVIEW' ? 1 : 0, golden_revision: goldenRevision, counts: { records: { value: 2, unit: 'records' }, pairs: { value: 1, unit: 'pairs' } }, allowed_actions: actions(), blockers: [], active_operation: null, retryable_operation: null };
    else if (pathname.endsWith('/start-decide')) { state = 'DECIDE_REVIEW'; revision++; status = 202; body = { operation_id: 'decide' }; }
    else if (pathname.endsWith('/cluster-queue')) {
      if(req.method()==='GET') body={pending_count:state==='DECIDE_REVIEW'?1:0,accepted_count:1,published_count:state==='DECIDE_REVIEW'?0:1,decision_revision:revision,cluster_revision:state==='GOLDEN_REVIEW'?1:0};
      else {state='GOLDEN_REVIEW';revision++;status=202;body={operation_id:'cluster',run_id:'r',operation_type:'CLUSTER',status:'COMPLETED',stage:'completed',progress:{},result:{},error_code:null};}
    }
    else if (pathname.endsWith('/cluster-preview')) body = { operation_id: 'preview', run_id: 'r', status: 'COMPLETED', result: { source_decision_revision: revision, source_assessment_id: null, allow_partial: false, preview_token: 'synthetic-preview', publication: { publication_status: 'COMPLETE', covered_records: 2, total_records: 2, unresolved_records: 0, multi_record_groups: 0, singletons: 2, clusters: 2 }, groups: [], conflicts: [], conflict_count: 0, pending_regions: [] } };
    else if (pathname.endsWith('/finalize-decisions')) { state = 'GOLDEN_REVIEW'; revision++; status = 202; body = { operation_id: 'cluster' }; }
    else if (pathname.endsWith('/data')) body = { files: [{ filename: 'synthetic.csv', row_count: 2, sha256: 'mock' }], schema: ['name'], profile: { record_count: 2, unit: 'records', quality_flags: {}, field_quality: {}, source_counts: { CRM: 2 } }, samples: { items: [], total: 0, next_cursor: null } };
    else if (pathname.endsWith('/pairs')) body = { items: [{ pair_id: 'p', baseline: { id: 'p', left_id: 'a', right_id: 'b', primary_group: 'D', subgroup: 'D1', score: 5, decision: 'INSUFFICIENT_EVIDENCE', field_evidence: [] }, decision: 'REJECTED', revision: 1, score_unit: 'p119_rule_v1', eligibility: { manual_allowed: false, auto_allowed: false, blocked_reasons: ['NO_EVIDENCE'] } }], total: 1, next_cursor: null };
    else if (pathname.endsWith('/pairs/summary')) body = {total:1,needs_review:0,accepted:0,denied:0,kept_separate:1,deferred:0,blocked:1,manual_available:0,auto_available:0,decision_revision:revision,groups:[{key:'D',total:1,subgroups:[]}]};
    else if (pathname.endsWith('/clusters')) body = { items: [{ cluster_id: 'c', member_count: 1, singleton: true, member_record_ids: ['a'], status: 'READY', evidence: { items: [], total: 0, next_cursor: null } }], total: 1, next_cursor: null };
    else if (pathname.endsWith('/clusters/c/golden')) {
      if (req.method() === 'PUT') { if (stale) { stale = false; revision++; goldenRevision++; status = 409; body = { detail: { code: 'STALE_GOLDEN_REVISION' } }; } else { selections = req.postDataJSON().selections; revision++; goldenRevision++; body = { items: [], workflow_revision: revision, golden_revision: goldenRevision }; } }
      else body = { cluster_id: 'c', cluster_revision: 1, golden_revision: goldenRevision, revision: goldenRevision, status: 'DRAFT', fields: { name: { value: selections.name?.value || 'Server name', provenance: 'SURVIVORSHIP' }, phone: { value: '090', values: [{ value: '090' }, { value: '091' }] } }, alternatives: { name: [{ source_record_id: 'a', source_system: 'CRM', value: 'Source name', auto_rule_available: true }] }, selections };
    } else if (pathname.endsWith('/clusters/c')) body = { cluster_id: 'c', member_record_ids: ['a'], member_count: 1, singleton: true, status: 'READY', evidence: { items: [], total: 0, next_cursor: null }, members: { items: [{ record_id: 'a', source_system: 'CRM', raw_fields: { name: 'Source name' }, normalized_fields: { name: 'source name' }, extra_fields: {} }], total: 1, next_cursor: null }, pair_evidence: { items: [], total: 0, next_cursor: null }, llm_stats: {}, workflow_context: {} };
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  });
  return { mutations, setState: (value: string) => { state = value; }, staleNextSave: () => { stale = true; } };
}
test('prepare progress follows backend updates and unlocks classification at DATA_READY', async ({ page }) => {
  const mock = await mockWorkflow(page);
  let state = 'DATA_PREPARING', status = 'QUEUED', processed = 0;
  await page.route(/\/workflow$/, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
    run_id: 'r', workflow_version: 2, workflow_state: state, workflow_revision: state === 'DATA_READY' ? 2 : 1,
    decision_revision: 0, cluster_revision: 0, golden_revision: 0, counts: {}, blockers: [], retryable_operation: null,
    allowed_actions: state === 'DATA_READY' ? ['start-decide'] : [],
    active_operation: state === 'DATA_READY' ? null : { operation_id: 'prepare', run_id: 'r', operation_type: 'PREPARE', status, stage: 'prepare-read', progress: { processed, total: 1000 }, error_code: null, result: {} },
  }) }));
  await page.goto('/app/sessions/r/data');
  const progress = page.getByRole('progressbar', { name: 'Đang tải dữ liệu', exact: true });
  const start = page.getByRole('button', { name: 'Bắt đầu phân loại cặp', exact: true });
  await expect(progress).toHaveAttribute('value', '0');
  await expect(start).toBeDisabled();
  status = 'RUNNING'; processed = 500;
  await expect(progress).toHaveAttribute('value', '50');
  await expect(start).toBeDisabled();
  state = 'DATA_READY';
  await expect(progress).toHaveAttribute('value', '100');
  await expect(start).toBeEnabled();
  expect(mock.mutations).toEqual([]);
});

test('decision progress replaces candidate count with classification progress and published overview', async ({ page }) => {
  const mock = await mockWorkflow(page);
  let complete = false, started = false, candidates = 0, scored = 0;
  await page.route(/\/workflow$/, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
    run_id: 'r', workflow_version: 2, workflow_state: complete ? 'DECIDE_REVIEW' : 'DECIDE_RUNNING', workflow_revision: complete ? 3 : 2,
    decision_revision: 1, cluster_revision: 0, golden_revision: 0, counts: { candidates: { value: 20, unit: 'pairs' } }, allowed_actions: [], blockers: [], retryable_operation: null,
    active_operation: complete ? null : { operation_id: 'd', run_id: 'r', operation_type: 'DECIDE', status: 'RUNNING', stage: started ? 'pair-classification' : 'candidate-generation', progress: { candidate_count: candidates, classified_count: scored, classification_started: started }, error_code: null, result: {} },
  }) }));
  await page.goto('/app/sessions/r/decisions');
  // The semantic section is named; progress never comes from a client-side timer.
  const status = page.locator('section[aria-label="Tiến trình phân loại cặp"]');
  await expect(status.getByRole('status')).toHaveText('0 cặp');
  candidates = 20;
  await expect(status.getByRole('status')).toHaveText('20 cặp');
  started = true; scored = 10;
  await expect(status.getByRole('progressbar')).toHaveAttribute('value', '50');
  await expect(status.getByText('Đang tạo các cặp bản ghi')).toHaveCount(0);
  complete = true;
  await expect(status.getByRole('progressbar')).toHaveAttribute('value', '100');
  await expect(page.getByRole('heading', { name: 'Phân bố nhóm', exact: true })).toBeVisible();
  await expect(page.getByRole('table')).toHaveCount(0);
  expect(mock.mutations).toEqual([]);
});

test('hub, URL tabs, reload and Back/Forward read only until explicit finalize', async ({ page }) => {
  const mock = await mockWorkflow(page); await page.addInitScript(() => localStorage.setItem('masterone.resolution-run.v1.mock-owner', 'old-run'));
  await page.goto('/app'); await expect(page.getByRole('heading', { name: 'Tổng quan', exact: true })).toBeVisible(); expect(page.url()).not.toContain('old-run');
  await page.goto('/app/sessions/r/data'); await expect(page.getByRole('button', { name: 'Bắt đầu phân loại cặp' })).toBeEnabled();
  await page.getByRole('link', { name: /Các nhóm bản ghi/ }).click(); await expect(page.getByText(/Chưa có nhóm/)).toBeVisible(); await page.reload(); await page.goBack(); await page.goForward(); expect(mock.mutations).toEqual([]);
  await page.getByRole('link', { name: /Tổng quan dữ liệu/ }).click(); await page.getByRole('button', { name: 'Bắt đầu phân loại cặp' }).click();
  await page.getByRole('navigation', { name: 'Các bước trong phiên' }).getByRole('link', { name: /^Duyệt các cặp bản ghi$/ }).click(); await expect(page.getByRole('table', { name: 'Danh sách cặp bản ghi' }).getByText('Thiếu bằng chứng', { exact: true })).toBeVisible(); await expect(page.getByRole('button', { name: 'Accept cặp p', exact: true })).toBeDisabled();
  await page.reload(); expect(mock.mutations.filter(p => p.endsWith('/finalize-decisions'))).toHaveLength(0);
  await page.getByRole('link',{name:'Sang B\u01b0\u1edbc 3',exact:true}).click();
  expect(mock.mutations).toHaveLength(1);
  await page.getByRole('button',{name:'T\u1ea1o nh\u00f3m m\u1edbi',exact:true}).click();
  await expect(page).toHaveURL(/\/clusters$/); await expect(page.getByRole('button', { name: /Singleton c/ })).toBeVisible(); expect(mock.mutations.filter(p => p.endsWith('/cluster-queue'))).toHaveLength(1);
});
test('stale Golden saves reload server and preserve draft without automatic retry', async ({ page }) => {
  const mock = await mockWorkflow(page); mock.setState('GOLDEN_REVIEW'); await page.goto('/app/sessions/r/clusters?cluster=c');
  await page.getByRole('textbox', { name: 'Chỉnh tay name' }).fill('My unsaved draft'); mock.staleNextSave(); await page.getByRole('button', { name: 'Lưu lựa chọn Golden' }).click();
  await expect(page.getByRole('textbox', { name: 'Chỉnh tay name' })).toHaveValue('My unsaved draft'); await expect(page.getByRole('button', { name: 'Lưu lựa chọn Golden' })).toBeDisabled();
  await expect(page.getByText(/Server đã có revision mới/)).toBeVisible(); expect(mock.mutations.filter(p => p.endsWith('/golden'))).toHaveLength(1);
  await expect(page.getByText('091', { exact: true })).toBeVisible(); await page.getByRole('button', { name: 'Bỏ bản nháp' }).click(); await expect(page.getByRole('textbox', { name: 'Chỉnh tay name' })).toHaveValue('');
});
test('viewer has no upload or compute permission; desktop/mobile themes retain four tabs', async ({ page }) => {
  const mock = await mockWorkflow(page, true); await page.goto('/app'); await expect(page.getByRole('heading', { name: 'Tổng quan', exact: true })).toBeVisible(); await expect(page.getByRole('link', { name: 'Upload / tạo phiên' })).toHaveCount(0);
  await page.goto('/app/sessions/r/data'); await expect(page.getByRole('button', { name: 'Bắt đầu phân loại cặp' })).toBeDisabled(); await expect(page.getByRole('navigation', { name: 'Các bước trong phiên' }).locator('ol > li > div > a')).toHaveCount(4);
  await page.getByRole('button', { name: 'Bật giao diện tối' }).click(); await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark'); await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); await page.getByRole('button', { name: 'Bật giao diện sáng' }).click(); await expect(page.locator('html')).toHaveAttribute('data-theme', 'light'); expect(mock.mutations).toEqual([]);
});
test('failed data reads recover explicitly and keyboard tabs remain read only', async ({ page }) => {
  const mock = await mockWorkflow(page); let unavailable = true;
  await page.route(/\/workflow\/data(?:\?|$)/, route => unavailable
    ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Synthetic read unavailable' }) })
    : route.fallback());
  await page.goto('/app/sessions/r/data');
  await expect(page.locator('.workflow-main').getByRole('alert')).toContainText('Synthetic read unavailable');
  unavailable = false; await page.getByRole('button', { name: 'Thử đọc lại' }).click();
  await page.getByText('1 tệp tải lên · 1 trường dữ liệu', { exact: true }).click();
  await expect(page.getByText('synthetic.csv', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Bắt đầu phân loại cặp' })).toBeEnabled();
  const tabs = page.getByRole('navigation', { name: 'Các bước trong phiên' });
  await tabs.getByRole('link', { name: /Tổng quan dữ liệu/ }).focus(); await page.keyboard.press('Tab');
  await expect(tabs.getByRole('button', { name: 'Thu gọn mục con Tổng quan dữ liệu', exact: true })).toBeFocused();
  await page.keyboard.press('Enter'); await page.keyboard.press('Tab');
  await expect(tabs.getByRole('link', { name: /Phân loại các cặp bản ghi/ })).toBeFocused();
  await page.keyboard.press('Enter'); await expect(page).toHaveURL(/\/decisions$/);
  expect(mock.mutations).toEqual([]);
});
test('final Golden values remain readable on mobile and export action stays above the dock', async ({ page }) => {
  const mock = await mockWorkflow(page); mock.setState('FINALIZED');
  await page.route(/\/workflow\/golden(?:\?|$)/, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
    items: [{ cluster_id: 'synthetic-cluster', status: 'FINALIZED', fields: { name: { value: 'Server name', provenance: 'MANUAL_OVERRIDE' }, phone: { values: [{ value: '090', source_system: 'CRM' }, { value: '091', source_system: 'BILL' }] } } }], total: 1, next_cursor: null,
  }) }));
  await page.setViewportSize({ width: 390, height: 844 }); await page.goto('/app/sessions/r/golden');
  await expect(page.getByText('091', { exact: true })).toBeVisible();
  await page.getByText('091', { exact: true }).scrollIntoViewIfNeeded(); await expect(page.getByText('091', { exact: true })).toBeInViewport();
  for (const mode of ['dark', 'light']) {
    await page.getByRole('button', { name: mode === 'dark' ? 'Bật giao diện tối' : 'Bật giao diện sáng' }).click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', mode);
    const exportButton = page.getByRole('button', { name: 'Chuẩn bị xuất CSV' }); await exportButton.scrollIntoViewIfNeeded();
    expect(await exportButton.evaluate(element => { const box = element.getBoundingClientRect(); return element.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)); })).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  expect(mock.mutations).toEqual([]);
});
test('old explicit links and the legacy guard route v2 back to its workspace without engine actions', async ({ page }) => {
  const mock = await mockWorkflow(page);
  await page.goto('/app?run=r&view=review'); await expect(page).toHaveURL(/\/app\/sessions\/r\/data$/);
  await expect(page.getByRole('button', { name: 'Bắt đầu phân loại cặp' })).toBeEnabled();
  await page.goto('/app/legacy?run=r'); await expect(page).toHaveURL(/\/app\/sessions\/r\/data$/);
  await expect(page.getByRole('button', { name: 'Bắt đầu phân loại cặp' })).toBeEnabled();
  expect(mock.mutations).toEqual([]);
});
