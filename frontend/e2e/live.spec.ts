import { execSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { createServer } from "node:http";
import type { IncomingMessage } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const PASSWORD = process.env.PW_DEMO_PASSWORD ?? "";
const COMPOSE = process.env.PW_COMPOSE ?? "docker compose";
const RECEIVER_PORT = Number(process.env.PW_RECEIVER_PORT ?? 18765);
const SHOTS = process.env.SCREENSHOT_DIR ?? join(tmpdir(), "pulsewatch-screens", "live");
mkdirSync(SHOTS, { recursive: true });

test.skip(!PASSWORD, "Для живого сценария нужны поднятый стек и PW_DEMO_PASSWORD");
test.use({ viewport: { width: 1440, height: 900 } });
test.describe.configure({ mode: "serial" });

let shared: Page;

test.beforeAll(async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
  });
  shared = await context.newPage();
  await signIn(shared);
});

test.afterAll(async () => {
  await shared.context().close();
});

async function signIn(page: Page, email = "demo@example.com", password = PASSWORD) {
  await page.goto("/login");
  await page.getByLabel("Электронная почта").fill(email);
  await page.getByLabel("Пароль").fill(password);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page).toHaveURL(/\/$/);
}

async function openMonitor(page: Page, name: string) {
  await page.goto("/");
  await page.getByRole("link", { name: new RegExp(`^${name}`) }).click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
}

test("история за 90 дней, waterfall, SLO и регионы", async () => {
  const page = shared;
  await page.goto("/");
  await expect(page.getByText("1 инцидент")).toBeVisible();
  await openMonitor(page, "Публичный API");
  await page.getByRole("radio", { name: "90 дней" }).click();
  await expect(page).toHaveURL(/range=90d/);
  await expect(page.getByText("По суточным свёрткам")).toBeVisible();
  await expect(page.getByRole("img", { name: /Отклик «Публичный API» за 90 дней/ })).toBeVisible();
  await expect(page.getByRole("figure").or(page.locator("figure")).first()).toBeVisible();
  await expect(page.getByText(/^SLO 99,50/)).toBeVisible();
  await expect(page.getByText("Франкфурт")).toBeVisible();
  await page.screenshot({ path: join(SHOTS, "monitor-90d.png"), fullPage: true });
  await page.getByRole("radio", { name: "24 ч" }).click();
  await page.getByRole("region", { name: "Проверки" }).getByRole("row").nth(1).click();
  await expect(page.getByText(/проверка \d+ ноя/)).toBeVisible();
});

test("инцидент с лентой и диагностикой", async () => {
  const page = shared;
  await page.goto("/incidents?state=resolved");
  await page
    .getByRole("row")
    .filter({ hasText: "Публичный API" })
    .filter({ hasText: "HTTP 503" })
    .getByRole("link")
    .first()
    .click();
  await expect(page.getByRole("heading", { name: /Недоступность: Публичный API/ })).toBeVisible();
  await expect(
    page.getByRole("list", { name: "Лента событий" }).getByText("Сбой подтверждён"),
  ).toBeVisible();
  await expect(
    page.getByRole("list", { name: "Лента событий" }).getByText("Причина найдена"),
  ).toBeVisible();
  await expect(page.getByText("[redacted]").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Что произошло" })).toBeVisible();
  await page.screenshot({ path: join(SHOTS, "incident.png"), fullPage: true });
});

test("проверка «сейчас» приходит через SSE", async () => {
  const page = shared;
  await expect(page.getByText("В эфире")).toBeVisible({ timeout: 20_000 });
  await openMonitor(page, "Витрина магазина");
  await page.getByRole("button", { name: "Проверить сейчас" }).click();
  await expect(page.getByText(/ответ за .* мс|ответ за .* с/)).toBeVisible({ timeout: 40_000 });
});

test("heartbeat: создание и пинг через curl", async () => {
  const page = shared;
  await page.goto("/sites/new");
  await page.getByRole("radio", { name: /Heartbeat/ }).click();
  await page.getByLabel("Название").fill(`Импорт прайса ${Date.now() % 10000}`);
  await page.getByRole("button", { name: "Создать монитор" }).click();
  await expect(page.getByText("Сохраните адрес. Он показывается один раз")).toBeVisible();
  const url =
    (await page.locator("code[title*='/api/heartbeat/hb_']").first().getAttribute("title")) ?? "";
  expect(url).toContain("/api/heartbeat/hb_");
  const answer = execSync(`curl -fsS -X POST "${url}?duration=1840"`).toString();
  expect(JSON.parse(answer)).toMatchObject({ ok: true });
  await page.reload();
  await expect(page.getByText(/только что|1 мин назад/).first()).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: join(SHOTS, "heartbeat.png"), fullPage: true });
  await page.getByRole("button", { name: "Удалить монитор" }).click();
  await expect(page.getByText(/удалён/)).toBeVisible();
});

test("канал Slack доставляет на локальный приёмник", async () => {
  test.setTimeout(150_000);
  const page = shared;
  const received: unknown[] = [];
  const server = createServer((request: IncomingMessage, response) => {
    let body = "";
    request.on("data", (chunk) => (body += chunk));
    request.on("end", () => {
      received.push(JSON.parse(body || "{}"));
      response.writeHead(200).end("ok");
    });
  });
  await new Promise<void>((resolve) => server.listen(RECEIVER_PORT, "0.0.0.0", resolve));
  try {
    await page.goto("/channels");
    await page.getByRole("button", { name: "Добавить канал" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("radio", { name: "Slack" }).click();
    await dialog.getByLabel("Название").fill("Slack: локальный приёмник");
    await dialog
      .getByLabel("Адрес вебхука")
      .fill(`http://host.docker.internal:${RECEIVER_PORT}/services/T/B/demo`);
    await dialog.getByRole("button", { name: "Подключить" }).click();
    await expect.poll(() => received.length, { timeout: 120_000 }).toBeGreaterThan(0);
    expect(JSON.stringify(received[0])).toContain("blocks");
    await page.reload();
    await expect(
      page.locator("li").filter({ hasText: "Slack: локальный приёмник" }).first(),
    ).toBeVisible();
    await page.screenshot({ path: join(SHOTS, "channels.png"), fullPage: true });
  } finally {
    server.close();
  }
});

test("регистрация по письму из лога и тестовая оплата", async ({ page }) => {
  const email = `buyer-${Date.now()}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Электронная почта").fill(email);
  await page.getByLabel("Пароль").fill("buyer-password-2026");
  await page.getByRole("button", { name: "Создать аккаунт" }).click();
  await expect(page.getByText("Проверьте почту")).toBeVisible();
  let link = "";
  await expect
    .poll(
      () => {
        const logs = execSync(`${COMPOSE} logs --since 2m worker backend`).toString();
        const match = logs
          .split("\n")
          .filter((line) => line.includes(email) && line.includes("account/signup"));
        const found = match.length
          ? match[match.length - 1].match(/https?:\/\/[^\s"\\]+account\/signup\?token=[\w.-]+/)
          : null;
        link = found ? found[0] : "";
        return link;
      },
      { timeout: 60_000 },
    )
    .not.toBe("");
  const url = new URL(link);
  await page.goto(`${url.pathname}${url.search}`);
  await page.getByLabel("Новый пароль").fill("buyer-password-2026");
  await page.getByRole("button", { name: "Сохранить пароль" }).click();
  await expect(page.getByText("Готово. Войдите с новым паролем.")).toBeVisible();
  await signIn(page, email, "buyer-password-2026");
  await expect(page.getByRole("heading", { name: "Что будем отслеживать?" })).toBeVisible();
  await page.goto("/pricing");
  await page.getByRole("button", { name: "Перейти на Business" }).click();
  await expect(page.getByRole("heading", { name: "Тестовая оплата" })).toBeVisible();
  await page.getByRole("button", { name: "Подтвердить тестовую оплату" }).click();
  await expect(page.getByText("Оплата прошла")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/Тариф Business активен/)).toBeVisible();
});

test("публичная страница, бейдж и RSS", async ({ page, request }) => {
  await page.goto("/public/polka");
  await expect(page.getByText("Частичный сбой")).toBeVisible();
  await expect(page.getByText("История инцидентов")).toBeVisible();
  const badge = await request.get("/api/badge/polka.svg");
  expect(badge.headers()["content-type"]).toContain("image/svg+xml");
  expect(await badge.text()).toContain("uptime");
  const feed = await request.get("/api/public/pages/polka/feed.xml");
  expect(feed.headers()["content-type"]).toContain("application/rss+xml");
  expect(await feed.text()).toContain('<rss version="2.0">');
  await page.screenshot({ path: join(SHOTS, "public.png"), fullPage: true });
});
