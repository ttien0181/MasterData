import { expect, test } from "@playwright/test";

for (const theme of ["light", "dark"]) {
  test(`session analytics use measured values in ${theme}`, async ({ page }) => {
    await page.addInitScript((value) => localStorage.setItem("masterone.theme", value), theme);
    await page.route("**/api/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = { items: [], total: 0 };
      if (path.endsWith("/auth/me")) body = { id: "u", email: "viewer@example.test", role: "VIEWER", capabilities: ["read"], csrf_token: "csrf" };
      if (path.endsWith("/overview")) body = { run_id: "r", status: "COMPLETED", phase: "COMPLETED", record_count: 4, file_count: 3, candidate_count: 2, history_count: 0,
        source_counts: { CRM: 2, ERP: 1, BILL: 1 }, estimated_entity_count: 3, duplicate_record_count: 1,
        counts: { auto: 1, review: 1, review_pairs: 1, rejected: 0, separate: 2, review_cases: 1, manual: 0, insufficient: 0, group_a: 2, group_c: 0, group_d: 0, hard_blocked: 0 } };
      if (path.endsWith("/analytics")) body = { run_id: "r", metric_version: "1",
        decisions: { basis: "initial_classification", total: 4, automatic: 1, review: 2, no_duplicate_detected: 1, automatic_percent: 25 },
        conflicts: { review_pair_count: 1, fields: [{ field: "address", count: 1 }, { field: "name", count: 1 }, { field: "phone", count: 0 }, { field: "email", count: 0 }] },
        quality: { sources: [{ source: "CRM", record_count: 2, valid_fields: 6, assessed_fields: 8, score_percent: 75, coverage_percent: 100 },
          { source: "ERP", record_count: 1, valid_fields: 3, assessed_fields: 4, score_percent: 75, coverage_percent: 100 },
          { source: "BILL", record_count: 1, valid_fields: 0, assessed_fields: 0, score_percent: null, coverage_percent: 0 }] } };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });
    await page.setViewportSize({ width: 1500, height: 1000 });
    await page.route("**/api/v1/mdm/runs/*/workflow", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ workflow_version: 1 }) }));
  await page.goto("/app/legacy?run=r");
    await expect(page.getByRole("img", { name: "25% bản ghi được phân loại tự xử lý" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "SỐ LƯỢNG BẢN GHI THEO NGUỒN" })).toBeVisible();
    await expect(page.getByTitle("CRM: 2 bản ghi")).toContainText("2");
    await expect(page.getByTitle("BILL: 1 bản ghi")).toContainText("1");
    for (const width of [1500, 1024, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(await page.locator(".analytics-cards section").evaluateAll((cards) => cards.every((card) => card.scrollWidth <= card.clientWidth + 1))).toBe(true);
      await page.screenshot({ path: `test-results/analytics-${theme}-${width}.png`, fullPage: true });
    }
  });
}
