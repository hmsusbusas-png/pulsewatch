import { readFileSync } from "node:fs";
import type { Page, Route } from "@playwright/test";

export const API = /^http:\/\/127\.0\.0\.1:5180\/api\//;

type Fixtures = Record<string, unknown>;
type Handler = (route: Route, body: unknown) => Promise<void> | void;

export const fixtures: Fixtures = JSON.parse(
  readFileSync(new URL("./fixtures/demo.json", import.meta.url), "utf8"),
) as Fixtures;

export function json(
  route: Route,
  body: unknown,
  status = 200,
  headers: Record<string, string> = {},
) {
  return route.fulfill({
    status,
    contentType: "application/json",
    headers: { "x-request-id": "e2e", ...headers },
    body: JSON.stringify(body),
  });
}

export function apiError(route: Route, status: number, code: string, message: string) {
  return json(route, { code, message, details: null, request_id: "e2e" }, status);
}

export type Mock = {
  requests: { method: string; path: string; body: unknown }[];
  on: (method: string, path: RegExp, handler: Handler) => void;
  streamEvents: string[];
};

export async function mockApi(
  page: Page,
  options: { authenticated?: boolean } = {},
): Promise<Mock> {
  const authenticated = options.authenticated ?? true;
  const handlers: { method: string; path: RegExp; handler: Handler }[] = [];
  const mock: Mock = {
    requests: [],
    on: (method, path, handler) => handlers.unshift({ method, path, handler }),
    streamEvents: [],
  };
  if (authenticated) {
    await page.addInitScript(() => localStorage.setItem("pw.session", "1"));
  }
  await page.route(API, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api/, "");
    const key = `${path}${url.search}`;
    const method = request.method();
    let body: unknown = null;
    try {
      body = request.postDataJSON();
    } catch {
      body = request.postData();
    }
    mock.requests.push({ method, path: key, body });
    for (const item of handlers) {
      if (item.method === method && item.path.test(key)) return item.handler(route, body);
    }
    if (path === "/auth/refresh") {
      return authenticated
        ? json(route, { access_token: "e2e-token", token_type: "bearer" })
        : apiError(route, 401, "refresh_missing", "no cookie");
    }
    if (path === "/auth/login")
      return json(route, { access_token: "e2e-token", token_type: "bearer" });
    if (path === "/stream/token")
      return json(route, { token: "stream-token-for-e2e-tests", expires_in: 60 });
    if (path === "/stream") {
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: `retry: 600000\nevent: ready\ndata: {"heartbeat":20}\n\n${mock.streamEvents.join("")}`,
      });
    }
    if (method === "GET") {
      const exact = fixtures[key] ?? fixtures[path];
      if (exact !== undefined) return json(route, exact);
      const prefix = Object.keys(fixtures).find((name) => name.split("?")[0] === path);
      if (prefix) return json(route, fixtures[prefix]);
      if (/^\/sites\/\d+\/checks/.test(path)) return json(route, []);
      if (/^\/sites\/\d+\/stats/.test(path)) {
        const site = path.split("/")[2];
        return json(route, fixtures[`/sites/${site}/stats?range=24h`]);
      }
      return apiError(route, 404, "not_found", `no fixture for ${key}`);
    }
    return json(route, {}, method === "DELETE" ? 204 : 200);
  });
  return mock;
}

export function siteNamed(name: string): { id: number; name: string } {
  const sites = fixtures["/sites?cursor=0&limit=100"] as { id: number; name: string }[];
  const site = sites.find((item) => item.name === name);
  if (!site) throw new Error(`fixture has no site ${name}`);
  return site;
}
