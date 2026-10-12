import { defineConfig } from "@playwright/test";

const qa = process.env.SESSION_WORKFLOW_QA === "1";
const port = qa ? 13000 : 3000;
const edgePath = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  reporter: "list",
  use: {
    baseURL: `http://localhost:${port}`,
    browserName: "chromium",
    launchOptions: { executablePath: edgePath },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: `npm run ${qa ? 'start' : 'dev'} -- --hostname 127.0.0.1 --port ${port}`,
    url: `http://localhost:${port}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: { NEXT_PUBLIC_API_BASE_URL: "http://localhost:8001" },
  },
});
