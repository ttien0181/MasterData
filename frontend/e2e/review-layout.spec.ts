import { expect, test } from "@playwright/test";

const records = ["CRM", "ERP"].map((source, index) => ({
  id: `record-${index}`, source_system: source, external_id: `${source}-${"0123456789".repeat(5)}`,
  raw_fields: { name: `Nguyễn Thị Hoàng Minh Anh ${index + 1}`, phone: `090123456${index}`,
    email: `nguyenthihoangminhanh.phongchamsockhachhang${index}@congty.example.test`,
    address: `Tầng ${index + 1}, Tòa nhà dịch vụ khách hàng, 123 Nguyễn Thị Minh Khai, Thành phố Hồ Chí Minh` },
}));
const item = { id: "pair-1", bucket: "review_pairs", kind: "pair", origin: "REVIEW",
  member_record_ids: records.map((record) => record.id), member_count: 2, pair_count: 1,
  label: records[0].raw_fields.name, record_labels: records.map((record) => record.raw_fields.name),
  primary_group: "A", subgroup: "A1_NAME_PHONE", reason: "POLICY_BLOCKED", revision: 1 };
const detail = { ...item, records, choices: {}, field_options: {}, missing_fields: [], affected_record_count: 3 };

for (const theme of ["light", "dark"]) {
  test(`pair review and three groups in ${theme} mode`, async ({ page }) => {
    await page.addInitScript((value) => localStorage.setItem("masterone.theme", value), theme);
    let rejected = false;
    let lastFilter = "";
    let saved: unknown = null;
    await page.route("**/api/v1/**", async (route) => {
      const url = new URL(route.request().url());
      const path = url.pathname;
      let body: unknown = {};
      if (path.endsWith("/auth/me")) body = { id: "layout-user", email: "steward@example.test",
        role: "STEWARD", csrf_token: "test", capabilities: ["read", "create_run", "approve_pair", "reject_pair"] };
      else if (path.endsWith("/runs") || path.endsWith("/upload-sessions")) body = { items: [], total: 0 };
      else if (path.endsWith("/overview")) body = { run_id: "layout-run", status: "COMPLETED", phase: "COMPLETED",
        record_count: 100, file_count: 2, history_count: 0, candidate_count: 26, source_counts: { CRM: 50, ERP: 50 },
        counts: { auto: 1, review: 25, review_pairs: 26, separate: 100, insufficient: 0,
          group_a: 26, group_c: 0, group_d: 0, rejected: rejected ? 1 : 0, review_cases: 25, manual: 0, hard_blocked: 0 } };
      else if (path.endsWith("/pair-categories")) body = [{ primary_group: "A", subgroup: "A1_NAME_PHONE", pair_count: 26 }];
      else if (path.endsWith("/items/review_pairs")) {
        lastFilter = url.searchParams.get("subgroup") || "";
        body = { total: 26, items: Array.from({ length: 25 }, (_, index) => ({
          ...item, id: `pair-${index + 1}`,
          record_labels: index ? [`Khách hàng ${index + 1}`, `Khách hàng nguồn ERP ${index + 1}`] : item.record_labels,
        })).filter((row) => !rejected || row.id !== "pair-1") };
      } else if (path.endsWith("/items/review_pairs/pair-1") || path.endsWith("/items/no_merge/pair-1")) body = detail;
      else if (path.endsWith("/items/no_merge")) body = { total: rejected ? 1 : 0, items: rejected ? [{ ...item, bucket: "no_merge" }] : [] };
      else if (path.endsWith("/review-pairs/pair-1/decision")) {
        saved = route.request().postDataJSON(); rejected = true;
        body = { pair_id: "pair-1", proposal_id: null, affected_record_count: 2 };
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });

    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.route("**/api/v1/mdm/runs/*/workflow", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ workflow_version: 1 }) }));
  await page.goto("/app/legacy?run=layout-run&view=review");
    await expect(page.locator(".pair-list-table tbody tr")).toHaveCount(25);
    await page.getByRole("combobox", { name: "Nhóm xem xét", exact: true }).selectOption("A");
    await page.getByRole("combobox", { name: "Phân nhóm chi tiết", exact: true }).selectOption("A1_NAME_PHONE");
    await expect.poll(() => lastFilter).toBe("A1_NAME_PHONE");

    for (const width of [1440, 1024, 768, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: `test-results/review-table-${theme}-${width}.png` });
      expect(await page.locator(".pair-list-table tbody tr").evaluateAll((rows) => rows.every((row, index) => {
        const box = row.getBoundingClientRect();
        return box.height >= 55 && (!index || box.top >= rows[index - 1].getBoundingClientRect().bottom - 1);
      }))).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.getByRole("button", { name: "So sánh cặp pair-1", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "So sánh hai bản ghi" });
      await expect(dialog.locator("thead th")).toHaveCount(3);
      await expect(dialog.locator("tbody tr")).toHaveCount(4);
      await expect(dialog.getByText(/tổng cộng 3 bản ghi/)).toBeVisible();
      await expect(dialog.getByRole("button", { name: "Xác nhận gộp", exact: true })).toBeEnabled();
      await page.screenshot({ path: `test-results/review-detail-${theme}-${width}.png` });
      expect(await dialog.locator(".pair-comparison-dialog").evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
      await page.keyboard.press("Escape");
      await expect(dialog).toHaveCount(0);
      await expect(page.getByRole("button", { name: "So sánh cặp pair-1", exact: true })).toBeFocused();
    }
    await page.getByRole("button", { name: "So sánh cặp pair-1", exact: true }).click();
    await page.getByRole("button", { name: "Xác nhận không gộp", exact: true }).click();
    expect(saved).toEqual({ decision: "no_merge", revision: 1 });
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "So sánh cặp pair-1", exact: true })).toHaveCount(0);
    await page.locator(".workspace-dock").getByRole("button", { name: "Không gộp", exact: true }).click();
    await page.getByRole("button", { name: "So sánh cặp pair-1", exact: true }).click();
    await expect(page.getByRole("dialog").getByRole("button", { name: "Xác nhận gộp", exact: true })).toHaveCount(0);
  });
}
