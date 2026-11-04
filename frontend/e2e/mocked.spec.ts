import { mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { apiError, fixtures, json, mockApi, siteNamed } from "./api-mock";

const SHOTS = process.env.SCREENSHOT_DIR ?? join(tmpdir(), "pulsewatch-screens", "mocked");
mkdirSync(SHOTS, { recursive: true });

const api = siteNamed("Публичный API");
const payments = siteNamed("Платёжный шлюз");
const pages = fixtures["/status-pages?cursor=0&limit=100"] as { id: number; slug: string }[];
const longIncident = (
  fixtures["/incidents?limit=50&cursor=0"] as {
    id: number;
    site_name: string;
    kind: string;
    is_open: boolean;
  }[]
).find((item) => item.site_name === "Публичный API" && item.kind === "down" && !item.is_open);

const SCREENS: [string, string, RegExp][] = [
  ["dashboard", "/", /инцидент/],
  ["monitor", `/sites/${api.id}`, /Этапы запроса/],
  ["monitor-90d", `/sites/${api.id}?range=90d`, /Аптайм по дням/],
  ["incident", `/incidents/${longIncident?.id}`, /Лента событий/],
  ["incidents", "/incidents", /Инциденты/],
  ["monitor-form", "/sites/new", /Тип проверки/],
  ["channels", "/channels", /Каналы уведомлений/],
  ["status-pages", "/status-pages", /Статус-страницы/],
  ["status-editor", `/status-pages/${pages[0].id}`, /Предпросмотр/],
  ["public-page", `/public/${pages[0].slug}`, /История инцидентов/],
  ["pricing", "/pricing", /Тарифы/],
  ["settings", "/settings?tab=security", /Активные сессии/],
  ["audit", "/settings?tab=audit", /Журнал действий/],
  ["billing", "/settings?tab=billing", /Платежи/],
  ["import", "/settings?tab=import", /Импорт/],
  ["keys", "/api-keys", /API-ключи/],
  ["api-docs", "/api-docs", /Pulsewatch API/],
  ["admin", "/admin", /Регистрации/],
];

async function settle(page: Page) {
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
}

for (const width of [1440, 360]) {
  test.describe(`экраны ${width}px`, () => {
    test.use({ viewport: { width, height: width === 360 ? 780 : 900 } });
    for (const [name, path, marker] of SCREENS) {
      test(`${name}: вёрстка, доступность, без горизонтальной прокрутки`, async ({ page }) => {
        const problems: string[] = [];
        page.on("pageerror", (error) => problems.push(error.message));
        page.on("console", (message) => {
          if (message.type() === "error" && !message.text().includes("status of 404"))
            problems.push(message.text());
        });
        await mockApi(page);
        await page.goto(path);
        await expect(page.getByRole("main").getByText(marker).first()).toBeVisible();
        if (name === "dashboard") await expect(page).toHaveTitle("Журнал мониторов. pulsewatch");
        await settle(page);
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        expect(overflow, "горизонтальная прокрутка страницы").toBeLessThanOrEqual(0);
        const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
        const serious = results.violations.filter(
          (item) => item.impact === "serious" || item.impact === "critical",
        );
        expect(serious.map((item) => `${item.id}: ${item.nodes[0]?.target.join(" ")}`)).toEqual([]);
        await page.screenshot({ path: join(SHOTS, `${width}-${name}.png`), fullPage: true });
        expect(problems).toEqual([]);
      });
    }
  });
}

test.describe("взаимодействия", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("командная палитра: поиск монитора и переход", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");
    await expect(page.getByTestId("monitor-row").first()).toBeVisible();
    await page.keyboard.press("Control+K");
    const input = page.getByRole("combobox", { name: "Команда или монитор" });
    await expect(input).toBeFocused();
    await input.fill("Платёж");
    await expect(page.getByRole("option", { name: /Платёжный шлюз/ }).first()).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(new RegExp(`/sites/${payments.id}$`));
    await page.keyboard.press("Control+K");
    await page.keyboard.press("Escape");
    await expect(input).toBeHidden();
  });

  test("горячие клавиши: переходы и справка", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");
    await expect(page.getByTestId("monitor-row").first()).toBeVisible();
    await page.keyboard.press("g");
    await page.keyboard.press("c");
    await expect(page).toHaveURL(/\/channels$/);
    await page.keyboard.press("Shift+?");
    await expect(page.getByRole("dialog", { name: "Горячие клавиши" })).toBeVisible();
    await page.keyboard.press("Escape");
    await page.keyboard.press("n");
    await expect(page).toHaveURL(/\/sites\/new$/);
  });

  test("отмена удаления: запрос не уходит, монитор возвращается", async ({ page }) => {
    const mock = await mockApi(page);
    await page.goto(`/sites/${payments.id}`);
    await page.getByRole("button", { name: "Удалить монитор" }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByText(`Монитор «${payments.name}» удалён`)).toBeVisible();
    await expect(page.getByTestId("monitor-row").filter({ hasText: payments.name })).toHaveCount(0);
    await page.getByRole("button", { name: "Отменить" }).click();
    await expect(page.getByTestId("monitor-row").filter({ hasText: payments.name })).toHaveCount(1);
    await page.waitForTimeout(7500);
    expect(mock.requests.filter((item) => item.method === "DELETE")).toEqual([]);
  });

  test("удаление уходит только через 7 секунд", async ({ page }) => {
    const mock = await mockApi(page);
    await page.goto(`/sites/${payments.id}`);
    await page.getByRole("button", { name: "Удалить монитор" }).click();
    await page.waitForTimeout(5000);
    expect(mock.requests.some((item) => item.method === "DELETE")).toBe(false);
    await expect
      .poll(
        () => mock.requests.filter((item) => item.method === "DELETE").map((item) => item.path),
        { timeout: 5000 },
      )
      .toEqual([`/sites/${payments.id}`]);
  });

  test("SSE: событие меняет строку без перезагрузки списка", async ({ page }) => {
    const mock = await mockApi(page);
    mock.streamEvents.push(
      `id: 1\nevent: check_completed\ndata: ${JSON.stringify({
        site_id: payments.id,
        ok: true,
        status: "up",
        status_code: 200,
        response_ms: 187,
        checked_at: new Date().toISOString(),
        region: "msk",
      })}\n\n`,
    );
    let listLoads = 0;
    mock.on("GET", /^\/sites\?cursor=0&limit=100$/, (route) => {
      listLoads += 1;
      return json(route, fixtures["/sites?cursor=0&limit=100"]);
    });
    await page.goto("/");
    const row = page.getByTestId("monitor-row").filter({ hasText: payments.name });
    await expect(row.getByText(", Работает")).toBeAttached();
    await expect(page.getByText("Обновления приостановлены")).toBeVisible();
    expect(listLoads).toBe(1);
  });

  test("оптимистичная пауза и откат при ошибке", async ({ page }) => {
    const mock = await mockApi(page);
    mock.on("PATCH", new RegExp(`^/sites/${api.id}$`), async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1200));
      return apiError(route, 402, "plan_limit_sites", "Превышен лимит активных сайтов");
    });
    await page.goto(`/sites/${api.id}`);
    await page.getByRole("button", { name: "Пауза" }).click();
    await expect(page.getByText("Пауза", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Возобновить" })).toBeVisible();
    await expect(page.getByText(/Лимит тарифа исчерпан/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Пауза" })).toBeVisible();
  });

  test("быстрый старт на пустом аккаунте создаёт монитор", async ({ page }) => {
    const mock = await mockApi(page);
    const created = {
      ...(fixtures[`/sites/${api.id}`] as object),
      id: 999,
      name: "shop.example.com",
      last_checked_at: null,
    };
    mock.on("GET", /^\/sites\?cursor=0&limit=100$/, (route) => json(route, []));
    mock.on("POST", /^\/sites$/, (route) => json(route, created, 201));
    mock.on("GET", /^\/sites\/999$/, (route) => json(route, created));
    await page.goto("/");
    await page.getByLabel("Адрес сайта или API").fill("shop.example.com");
    await page.getByRole("button", { name: "Начать следить" }).click();
    await expect(page).toHaveURL(/\/sites\/999\?new=1$/);
    const post = mock.requests.find((item) => item.method === "POST" && item.path === "/sites");
    expect(post?.body).toMatchObject({ url: "https://shop.example.com", name: "shop.example.com" });
  });

  test("выделение с Shift и панель массовых действий", async ({ page }) => {
    const mock = await mockApi(page);
    mock.on("POST", /^\/sites\/bulk$/, (route, body) =>
      json(route, { updated: (body as { ids: number[] }).ids.length, results: [] }),
    );
    await page.goto("/");
    const boxes = page.getByTestId("monitor-row").getByRole("checkbox");
    await boxes.nth(0).click();
    await boxes.nth(3).click({ modifiers: ["Shift"] });
    const toolbar = page.getByRole("toolbar", { name: "Действия с выбранными мониторами" });
    await expect(toolbar.getByText("Выбрано: 4")).toBeVisible();
    await toolbar.getByRole("button", { name: "Пауза" }).click();
    await expect
      .poll(() => mock.requests.find((item) => item.path === "/sites/bulk")?.body)
      .toMatchObject({ action: "pause" });
  });

  test("фильтры сохраняются в адресе", async ({ page }) => {
    await mockApi(page);
    await page.goto("/?status=problems");
    await expect(page.getByTestId("monitor-row")).toHaveCount(1);
    await expect(page.getByLabel("Активные фильтры").getByText("Проблемы")).toBeVisible();
    await page.getByRole("button", { name: "Сбросить" }).click();
    await expect(page).toHaveURL(/\/$/);
  });
});
