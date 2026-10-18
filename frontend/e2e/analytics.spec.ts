import { expect, test } from "@playwright/test";

for (const theme of ["light", "dark"]) {
  test(`session analytics use measured values in ${theme}`, async ({ page }) => {
    await page.addInitScript((value) => localStorage.setItem("masterone.theme", value), theme);
    await page.route("**/api/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = { items: [], total: 0 };
      if (path.endsWith("/auth/me")) body = { id: "u", email: "viewer@example.test", role: "VIEWER", capabilities: ["read"], csrf_token: "csrf" };
      if (path.endsWith("/overview")) body = { run_id: "r", status: "COMPLETED", phase: "COMPLETED", record_count: 100000, file_count: 8, candidate_count: 22800, history_count: 0,
        source_counts: { CRM: 25000, ERP: 25000, BILL: 25000, CSV: 25000 }, estimated_entity_count: 77700, duplicate_record_count: 22300,
        counts: { auto: 22300, review: 500, review_pairs: 500, rejected: 0, separate: 55400, review_cases: 500, manual: 0, insufficient: 0, group_a: 22800, group_c: 0, group_d: 0, hard_blocked: 0 } };
      if (path.endsWith("/analytics")) body = { run_id: "r", metric_version: "1",
        decisions: { basis: "initial_classification", total: 100000, automatic: 44600, review: 1000, no_duplicate_detected: 54400, automatic_percent: 44.6 },
        conflicts: { review_pair_count: 500, fields: [{ field: "name", count: 100 }, { field: "address", count: 0 }, { field: "phone", count: 0 }, { field: "email", count: 0 }] },
        quality: { sources: ["CRM", "ERP", "BILL", "CSV"].map((source) => ({ source, record_count: 25000,
          valid_fields: 0, assessed_fields: 0, score_percent: null, coverage_percent: 0 })) } };
      if (path.endsWith("/items/review_pairs")) body = { items: [], total: 500 };
      if (path.endsWith("/pair-categories")) body = [];
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });
    await page.setViewportSize({ width: 1500, height: 1000 });
    await page.goto("/app?run=r");
    await expect(page.getByRole("img", { name: "44,6% dòng thông tin được đề xuất gộp tự động ban đầu" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "SỐ LƯỢNG DÒNG THÔNG TIN THEO NGUỒN" })).toBeVisible();
    await expect(page.getByTitle("CRM: 25.000 dòng thông tin")).toContainText("25.000");
    await expect(page.getByTitle("BILL: 25.000 dòng thông tin")).toContainText("25.000");
    await expect(page.getByLabel("Phân loại dòng thông tin ban đầu").locator(".analytics-legend > div").filter({ hasText: "Cần xem xét ban đầu" })).toContainText("1.000 dòng");
    await expect(page.locator(".overview-kpi-review")).toContainText("500");
    await expect(page.locator(".overview-kpi-review")).toContainText("Cặp đang chờ xem xét");
    for (const width of [1500, 1024, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(await page.locator(".analytics-cards section").evaluateAll((cards) => cards.every((card) => card.scrollWidth <= card.clientWidth + 1))).toBe(true);
      await page.screenshot({ path: `test-results/analytics-${theme}-${width}.png`, fullPage: true });
    }
    await page.setViewportSize({ width: 1500, height: 1000 });
    await page.locator(".overview-kpi-review").click();
    await expect(page.getByText("500 cặp đang chờ xem xét", { exact: true })).toBeVisible();
  });
}
