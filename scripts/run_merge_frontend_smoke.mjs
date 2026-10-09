// Disposable browser configuration: never reuse a developer server.
import { mkdir, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { createServer } from "node:net";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const frontend = path.join(root, "frontend");
const production = process.argv.includes("--production");
const report = path.join(root, "artifacts", "merge-validation", `frontend-${Date.now()}`);
await mkdir(report, { recursive: true });
const server = createServer();
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const port = server.address().port;
await new Promise(resolve => server.close(resolve));
const edge = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const config = path.join(report, "playwright.config.mjs");
const literal = value => JSON.stringify(value);
await writeFile(config, `import { defineConfig } from ${literal(pathToFileURL(path.join(frontend, "node_modules/@playwright/test/index.mjs")).href)};
export default defineConfig({
  testDir: ${literal(path.join(frontend, "e2e"))}, workers: 2, fullyParallel: true,
  outputDir: ${literal(path.join(report, "test-results"))},
  reporter: [["list"], ["json", {outputFile: ${literal(path.join(report, "results.json"))}}]],
  use: {baseURL: "http://127.0.0.1:${port}", browserName: "chromium",
    launchOptions: ${literal(existsSync(edge) ? { executablePath: edge } : {})}, trace: "retain-on-failure", screenshot: "only-on-failure"},
  webServer: {command: "node node_modules/next/dist/bin/next ${production ? "start" : "dev"} --hostname 127.0.0.1 --port ${port}",
    cwd: ${literal(frontend)}, url: "http://127.0.0.1:${port}", reuseExistingServer: false, timeout: 120000,
    env: {NEXT_TELEMETRY_DISABLED: "1", NEXT_PUBLIC_API_BASE_URL: "http://127.0.0.1:9"}}
});
`);
console.log(`Browser evidence: ${report}`);
const child = spawn(process.execPath, [path.join(frontend, "node_modules/@playwright/test/cli.js"), "test", "--config", config],
  { cwd: frontend, stdio: "inherit", env: { ...process.env, CI: "1", NEXT_TELEMETRY_DISABLED: "1" } });
const code = await new Promise(resolve => child.once("exit", resolve));
await writeFile(path.join(report, "execution.json"), JSON.stringify({ exit_code: code,
  node: process.version, port, mode: `Next ${production ? "production build" : "dev"} + Edge; all API requests mocked by existing E2E specs; not a live full-stack test` }, null, 2));
process.exitCode = code ?? 1;
