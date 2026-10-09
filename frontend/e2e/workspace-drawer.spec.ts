import { expect, test } from "@playwright/test";

test("upload and session history open in a slide-out panel with full-screen expansion", async ({ page }) => {
  const user = {
    id: "e2e-user",
    email: "steward@example.test",
    csrf_token: "e2e-csrf",
    role: "STEWARD",
    capabilities: ["read", "read_audit", "create_run", "start_run", "approve_pair", "reject_pair", "edit_golden", "explain", "export_data"],
  };
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(user),
  }));
  await page.route("**/api/v1/mdm/runs**", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({ total: 0, items: [], next_cursor: null }),
  }));
  await page.route("**/api/v1/mdm/upload-sessions**", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }),
  }));

  await page.goto("/app");
  await expect(page.locator(".upload-orbit")).toBeVisible();
  await page.locator(".upload-orbit").click();

  const uploadPanel = page.getByRole("dialog", { name: "Nạp dữ liệu" });
  await expect(uploadPanel).toBeVisible();
  await expect(uploadPanel.getByRole("region", { name: "Dữ liệu đang chuẩn bị" })).toBeVisible();
  await expect(uploadPanel.getByRole("button", { name: "Chọn file để bắt đầu" })).toBeDisabled();
  expect((await uploadPanel.boundingBox())!.x).toBeLessThan(30);
  await page.screenshot({ path: "test-results/workspace-upload-drawer.png" });
  await uploadPanel.getByRole("button", { name: "Phóng to toàn màn hình" }).click();
  await expect(uploadPanel).toHaveClass(/is-expanded/);
  const viewportWidth = page.viewportSize()?.width ?? 0;
  await expect.poll(async () => Math.round((await uploadPanel.boundingBox())?.width ?? 0)).toBe(viewportWidth);
  await uploadPanel.getByRole("button", { name: "Đóng bảng bên" }).click();
  await expect(uploadPanel).toBeHidden();

  await page.locator(".sessions-orbit").click();
  const sessionsPanel = page.getByRole("dialog", { name: "Lịch sử phiên" });
  await expect(sessionsPanel).toBeVisible();
  await expect.poll(async () => (await sessionsPanel.boundingBox())!.x).toBeGreaterThan(600);
  await expect(sessionsPanel.getByRole("region", { name: "Phiên phân giải" })).toBeVisible();
  await sessionsPanel.getByRole("button", { name: "Phóng to toàn màn hình" }).click();
  await expect(sessionsPanel).toHaveClass(/is-expanded/);
  await expect.poll(async () => Math.round((await sessionsPanel.boundingBox())?.width ?? 0)).toBe(viewportWidth);
  await sessionsPanel.getByRole("button", { name: "Thu nhỏ bảng bên" }).click();
  await expect(sessionsPanel).not.toHaveClass(/is-expanded/);
});
