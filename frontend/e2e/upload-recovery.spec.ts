import { expect, test } from "@playwright/test";
import type { UploadSession } from "../src/lib/contracts";

test("closing panels keeps uploads alive; reload recovers a commit whose response was lost", async ({ page }) => {
  let upload: UploadSession | null = null;
  let releasePart!: () => void;
  const partGate = new Promise<void>((resolve) => { releasePart = resolve; });
  let partRequested = false;
  let creates = 0;
  let commits = 0;
  let starts = 0;
  let runStatus = "UPLOADED";
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let body: unknown = {};
    if (path.endsWith("/auth/me")) body = { id: "recovery-user", email: "steward@example.test", role: "STEWARD", csrf_token: "csrf", capabilities: ["read", "create_run", "start_run"] };
    else if (path.endsWith("/runs")) body = { items: [], total: 0, next_cursor: null };
    else if (path.endsWith("/overview")) body = { run_id: "recovered-run", status: runStatus, phase: "queued", record_count: 1, file_count: 1, history_count: 0, candidate_count: 0, source_counts: { CRM: 1 }, counts: { auto: 0, review: 0, separate: 0, insufficient: 0, group_a: 0, group_c: 0, group_d: 0, rejected: 0, review_cases: 0, manual: 0, hard_blocked: 0 } };
    else if (path.endsWith("/start")) { starts++; runStatus = "QUEUED"; body = { run_id: "recovered-run", status: "QUEUED" }; }
    else if (path.endsWith("/upload-sessions") && request.method() === "GET") body = { items: upload && upload.status !== "COMMITTED" ? [upload] : [] };
    else if (path.endsWith("/upload-sessions") && request.method() === "POST") {
      creates++;
      const input = request.postDataJSON();
      upload = { session_id: "recover-upload", name: input.name, status: "UPLOADING", total_bytes: input.files[0].size, committed_run_id: null, error_message: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), files: [{ file_id: "file-1", filename: input.files[0].filename, expected_size: input.files[0].size, received_size: 0, position: 0, status: "UPLOADING", sha256: null, row_count: null, source_counts: {}, validation_error: null, parts: [] }] };
      body = upload;
    } else if (upload && path.includes("/parts/")) {
      partRequested = true;
      await partGate;
      upload.files[0].received_size = upload.files[0].expected_size;
    } else if (upload && path.endsWith("/validate")) {
      upload.status = "READY"; upload.files[0].status = "READY"; upload.files[0].row_count = 1;
      body = upload;
    } else if (upload && path.endsWith("/commit")) {
      commits++;
      upload.status = "COMMITTED"; upload.committed_run_id = "recovered-run";
      if (commits === 1) { await route.abort("connectionreset"); return; }
      body = { run_id: "recovered-run", status: runStatus, profile: { row_count: 1, file_count: 1 } };
    } else if (upload && path.includes("/upload-sessions/")) body = upload;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/app");
  await page.locator(".upload-orbit").click();
  const panel = page.getByRole("dialog", { name: "Nạp dữ liệu" });
  await panel.getByLabel("Chọn các file CSV").setInputFiles({ name: "resume.csv", mimeType: "text/csv", buffer: Buffer.from("record_id,source,name\n1,CRM,Ada\n") });
  await expect.poll(() => partRequested).toBe(true);
  await panel.getByRole("button", { name: "Đóng bảng bên" }).click();
  await expect(panel).toBeHidden();
  await expect(page.locator(".upload-pending-dot")).toBeVisible();
  await page.locator(".sessions-orbit").click();
  await page.getByRole("dialog", { name: "Lịch sử phiên" }).getByRole("button", { name: "Đóng bảng bên" }).click();
  await page.locator(".upload-orbit").click();
  await expect(panel.getByText("resume.csv", { exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Chờ tải lên hoàn tất" })).toBeDisabled();
  expect(creates).toBe(1);
  releasePart();
  await expect(panel.getByRole("button", { name: "Bắt đầu phân giải", exact: true })).toBeEnabled();

  await page.reload();
  await page.locator(".upload-orbit").click();
  await expect(panel.getByText("resume.csv", { exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "Bắt đầu phân giải", exact: true }).click();
  await expect(panel.getByRole("button", { name: "Tiếp tục phiên phân giải" })).toBeEnabled();
  expect(starts).toBe(0);

  await page.reload();
  await page.locator(".upload-orbit").click();
  await panel.getByRole("button", { name: "Tiếp tục phiên phân giải" }).click();
  await expect(panel).toBeHidden();
  await expect(page).toHaveURL(/run=recovered-run/);
  expect(starts).toBe(1);
  expect(creates).toBe(1);
  expect(commits).toBe(2);
  await expect.poll(() => page.evaluate(() => localStorage.getItem("masterone.upload-session.v1.recovery-user"))).toBeNull();
});
