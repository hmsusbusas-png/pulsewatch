import { ApiError, parseErrorResponse } from "./errors";
import type { components } from "./schema";

const API_PREFIX = "/api";

let accessToken: string | null = null;
let refreshPromise: Promise<string | null> | null = null;
let sessionExpiredHandler: (() => void) | null = null;

export type AccessTokenPayload = components["schemas"]["AccessTokenResponse"];

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function onSessionExpired(handler: (() => void) | null): void {
  sessionExpiredHandler = handler;
}

export async function refreshAccessToken(): Promise<string | null> {
  if (!refreshPromise) {
    refreshPromise = requestNewAccessToken().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

async function requestNewAccessToken(): Promise<string | null> {
  let response: Response;
  try {
    response = await fetch(`${API_PREFIX}/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: { Accept: "application/json" },
    });
  } catch {
    accessToken = null;
    return null;
  }

  if (!response.ok) {
    accessToken = null;
    return null;
  }

  try {
    const payload = await readJson<AccessTokenPayload>(response);
    accessToken = typeof payload.access_token === "string" ? payload.access_token : null;
    return accessToken;
  } catch {
    accessToken = null;
    return null;
  }
}

type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  authenticated?: boolean;
  responseType?: "json" | "blob" | "text";
  idempotencyKey?: string;
  keepalive?: boolean;
};

async function send(path: string, options: RequestOptions): Promise<Response> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (options.authenticated !== false && accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  if (options.idempotencyKey) {
    headers["Idempotency-Key"] = options.idempotencyKey;
  }

  return fetch(`${API_PREFIX}${path}`, {
    method: options.method ?? "GET",
    credentials: "include",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
    keepalive: options.keepalive,
  });
}

async function readJson<T>(response: Response): Promise<T> {
  const raw = await response.text();
  if (!raw) {
    return undefined as T;
  }
  return JSON.parse(raw) as T;
}

async function checkedResponse(path: string, options: RequestOptions): Promise<Response> {
  const authenticated = options.authenticated !== false;
  const response = await send(path, options);

  if (response.status === 401 && authenticated) {
    const token = await refreshAccessToken();
    if (!token) {
      sessionExpiredHandler?.();
      throw new ApiError(401, "unauthorized", "Access token is invalid or expired");
    }
    const retried = await send(path, options);
    if (retried.status === 401) {
      sessionExpiredHandler?.();
      throw await parseErrorResponse(retried);
    }
    if (!retried.ok) {
      throw await parseErrorResponse(retried);
    }
    return retried;
  }

  if (!response.ok) {
    throw await parseErrorResponse(response);
  }

  return response;
}

export function expireSession(): void {
  accessToken = null;
  sessionExpiredHandler?.();
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  try {
    const response = await checkedResponse(path, options);
    if (options.responseType === "blob") return (await response.blob()) as T;
    if (options.responseType === "text") return (await response.text()) as T;
    return await readJson<T>(response);
  } catch (error) {
    if (
      error instanceof ApiError &&
      (error.code.startsWith("plan_limit_") || error.code === "tariff_limit_reached")
    ) {
      window.dispatchEvent(new CustomEvent("pulsewatch:limit", { detail: error }));
    }
    throw error;
  }
}
