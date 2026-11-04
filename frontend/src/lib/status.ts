import type { DayUptime, Site } from "../api/types";

export type Tone = "up" | "down" | "degraded" | "paused" | "maintenance" | "unknown";

export function siteTone(
  site: Pick<Site, "status" | "paused" | "degraded" | "maintenance_active">,
): Tone {
  if (site.paused) return "paused";
  if (site.maintenance_active) return "maintenance";
  if (site.status === "down") return "down";
  if (site.degraded) return "degraded";
  if (site.status === "up") return "up";
  return "unknown";
}

export const TONE_LABEL: Record<Tone, string> = {
  up: "Работает",
  down: "Недоступен",
  degraded: "Медленно",
  paused: "Пауза",
  maintenance: "Обслуживание",
  unknown: "Ожидает проверки",
};

export const TONE_DOT: Record<Tone, string> = {
  up: "bg-accent",
  down: "bg-down",
  degraded: "bg-warn",
  paused: "bg-unknown",
  maintenance: "bg-info",
  unknown: "bg-inkFaint",
};

export const TONE_TEXT: Record<Tone, string> = {
  up: "text-accent",
  down: "text-down",
  degraded: "text-warn",
  paused: "text-inkMuted",
  maintenance: "text-info",
  unknown: "text-inkMuted",
};

export const TONE_PILL: Record<Tone, string> = {
  up: "border-accent/30 bg-accent/10 text-accent",
  down: "border-down/35 bg-down/10 text-down",
  degraded: "border-warn/35 bg-warn/10 text-warn",
  paused: "border-line bg-panelHover text-inkMuted",
  maintenance: "border-info/35 bg-info/10 text-info",
  unknown: "border-line bg-panelHover text-inkMuted",
};

export function uptimeColor(day: Pick<DayUptime, "uptime" | "checks">): string {
  if (day.uptime === null || !day.checks) return "bg-line";
  if (day.uptime >= 99.9) return "bg-accent";
  if (day.uptime >= 99) return "bg-yellow";
  if (day.uptime >= 95) return "bg-orange";
  return "bg-down";
}

export function uptimeText(value: number | null | undefined): string {
  if (value === null || value === undefined) return "text-inkMuted";
  if (value >= 99.9) return "text-accent";
  if (value >= 99) return "text-yellow";
  if (value >= 95) return "text-orange";
  return "text-down";
}

export const MONITOR_LABEL: Record<string, string> = {
  http: "HTTP",
  keyword: "Ключевое слово",
  tcp: "TCP",
  "ssl-only": "Сертификат",
  heartbeat: "Heartbeat",
  dns: "DNS",
};
