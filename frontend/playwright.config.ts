import { defineConfig, devices } from "@playwright/test";

const PORT = 5180;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "mocked",
      testMatch: /mocked\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${PORT}` },
    },
    {
      name: "live",
      testMatch: /live\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        baseURL: process.env.PW_LIVE_URL ?? "http://localhost:8080",
      },
    },
  ],
  webServer: process.env.PW_SKIP_SERVER
    ? undefined
    : {
        command: `npx vite --host 127.0.0.1 --port ${PORT} --strictPort`,
        url: `http://127.0.0.1:${PORT}/login`,
        reuseExistingServer: true,
        timeout: 60_000,
      },
});
