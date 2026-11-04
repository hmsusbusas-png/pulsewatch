import type { Assertion, MonitorType, Site, SiteCreate } from "../../api/types";

export type Window =
  | { kind: "once"; date: string; start: string; endDate: string; end: string; timezone: string }
  | { kind: "weekly"; weekdays: number[]; start: string; end: string; timezone: string };

export type Draft = {
  monitor_type: MonitorType;
  name: string;
  url: string;
  interval_seconds: number;
  timeout_seconds: number;
  method: "GET" | "HEAD" | "POST";
  codes: string;
  keyword: string;
  keyword_present: boolean;
  assertions: Assertion[];
  headers: { key: string; value: string }[];
  body: string;
  follow_redirects: boolean;
  tcp_send: string;
  tcp_expect: string;
  dns_record_type: "A" | "AAAA" | "CNAME" | "MX" | "TXT" | "NS";
  dns_expected: string;
  heartbeat_period_seconds: number;
  heartbeat_grace_seconds: number;
  failure_threshold: number;
  cooldown_seconds: number;
  channel_ids: number[];
  regions: string[];
  slo_target: string;
  expiry_thresholds: number[];
  check_domain: boolean;
  maintenance: Window[];
  tags: string;
  group: string;
  is_public: boolean;
  escalation_minutes: string;
  escalation_channel: string;
};

export const EMPTY: Draft = {
  monitor_type: "http",
  name: "",
  url: "",
  interval_seconds: 300,
  timeout_seconds: 10,
  method: "GET",
  codes: "200-399",
  keyword: "",
  keyword_present: true,
  assertions: [],
  headers: [],
  body: "",
  follow_redirects: true,
  tcp_send: "",
  tcp_expect: "",
  dns_record_type: "A",
  dns_expected: "",
  heartbeat_period_seconds: 3600,
  heartbeat_grace_seconds: 300,
  failure_threshold: 2,
  cooldown_seconds: 3600,
  channel_ids: [],
  regions: [],
  slo_target: "",
  expiry_thresholds: [30, 14, 7],
  check_domain: false,
  maintenance: [],
  tags: "",
  group: "",
  is_public: false,
  escalation_minutes: "",
  escalation_channel: "",
};

export function codesText(codes: number[]): string {
  if (!codes.length) return "200-399";
  const sorted = [...codes].sort((a, b) => a - b);
  const ranges: string[] = [];
  let start = sorted[0];
  let previous = sorted[0];
  for (const code of [...sorted.slice(1), Number.NaN]) {
    if (code === previous + 1) {
      previous = code;
      continue;
    }
    ranges.push(start === previous ? String(start) : `${start}-${previous}`);
    start = code;
    previous = code;
  }
  return ranges.join(", ");
}

export function parseCodes(text: string): number[] | null {
  const codes = new Set<number>();
  for (const part of text.split(/[,\s]+/).filter(Boolean)) {
    const match = part.match(/^(\d{3})(?:-(\d{3}))?$/);
    if (!match) return null;
    const first = Number(match[1]);
    const last = Number(match[2] ?? match[1]);
    if (first < 100 || last > 599 || last < first) return null;
    for (let code = first; code <= last; code += 1) codes.add(code);
  }
  return codes.size ? [...codes] : null;
}

function toWindow(raw: Record<string, string>): Window {
  if (raw.weekdays !== undefined) {
    return {
      kind: "weekly",
      weekdays: raw.weekdays.split(",").filter(Boolean).map(Number),
      start: raw.start_time ?? "03:00",
      end: raw.end_time ?? "04:00",
      timezone: raw.timezone ?? "UTC",
    };
  }
  const start = new Date(raw.start);
  const end = new Date(raw.end);
  const pad = (value: number) => String(value).padStart(2, "0");
  const day = (date: Date) =>
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const time = (date: Date) => `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  return {
    kind: "once",
    date: day(start),
    start: time(start),
    endDate: day(end),
    end: time(end),
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  };
}

export function fromWindow(window: Window): Record<string, string> {
  if (window.kind === "weekly") {
    return {
      weekdays: window.weekdays.join(","),
      start_time: window.start,
      end_time: window.end,
      timezone: window.timezone,
    };
  }
  return {
    start: new Date(`${window.date}T${window.start}:00`).toISOString(),
    end: new Date(`${window.endDate}T${window.end}:00`).toISOString(),
  };
}

export function draftFrom(site: Site): Draft {
  return {
    ...EMPTY,
    monitor_type: site.monitor_type ?? "http",
    name: site.name,
    url: site.monitor_type === "tcp" ? site.url.replace(/^https?:\/\//, "") : site.url,
    interval_seconds: site.interval_seconds,
    timeout_seconds: site.timeout_seconds ?? 10,
    method: site.method ?? "GET",
    codes: codesText(site.expected_codes ?? []),
    keyword: site.keyword ?? "",
    keyword_present: site.keyword_present ?? true,
    assertions: site.assertions ?? [],
    headers: Object.keys(site.headers ?? {}).map((key) => ({ key, value: "********" })),
    body: site.body ?? "",
    follow_redirects: site.follow_redirects ?? true,
    tcp_send: site.tcp_send ?? "",
    tcp_expect: site.tcp_expect ?? "",
    dns_record_type: site.dns_record_type ?? "A",
    dns_expected: site.dns_expected ?? "",
    heartbeat_period_seconds: site.heartbeat_period_seconds ?? 3600,
    heartbeat_grace_seconds: site.heartbeat_grace_seconds ?? 300,
    failure_threshold: site.failure_threshold ?? 2,
    cooldown_seconds: site.cooldown_seconds ?? 3600,
    channel_ids: site.channel_ids ?? [],
    regions: site.regions ?? [],
    slo_target:
      site.slo_target === null || site.slo_target === undefined ? "" : String(site.slo_target),
    expiry_thresholds: site.expiry_thresholds ?? [30, 14, 7],
    check_domain: site.check_domain ?? false,
    maintenance: (site.maintenance ?? []).map(toWindow),
    tags: (site.tags ?? []).join(", "),
    group: site.group ?? "",
    is_public: site.is_public,
  };
}

export function payloadFrom(draft: Draft): SiteCreate {
  const type = draft.monitor_type;
  const http = type === "http" || type === "keyword";
  return {
    name: draft.name.trim(),
    url: type === "heartbeat" ? "" : draft.url.trim(),
    monitor_type: type,
    interval_seconds:
      type === "heartbeat" ? Math.max(60, draft.heartbeat_period_seconds) : draft.interval_seconds,
    timeout_seconds: draft.timeout_seconds,
    method: http ? draft.method : "GET",
    expected_codes:
      parseCodes(draft.codes) ?? Array.from({ length: 200 }, (_, index) => 200 + index),
    keyword: type === "keyword" ? draft.keyword || null : null,
    keyword_present: draft.keyword_present,
    assertions: http ? draft.assertions : [],
    headers: http
      ? Object.fromEntries(
          draft.headers.filter((row) => row.key.trim()).map((row) => [row.key.trim(), row.value]),
        )
      : {},
    body: http && draft.method === "POST" && draft.body ? draft.body : null,
    follow_redirects: draft.follow_redirects,
    tcp_send: type === "tcp" ? draft.tcp_send || null : null,
    tcp_expect: type === "tcp" ? draft.tcp_expect || null : null,
    dns_record_type: type === "dns" ? draft.dns_record_type : null,
    dns_expected: type === "dns" ? draft.dns_expected || null : null,
    heartbeat_period_seconds: type === "heartbeat" ? draft.heartbeat_period_seconds : null,
    heartbeat_grace_seconds: type === "heartbeat" ? draft.heartbeat_grace_seconds : null,
    failure_threshold: type === "heartbeat" ? 1 : draft.failure_threshold,
    cooldown_seconds: draft.cooldown_seconds,
    channel_ids: draft.channel_ids,
    regions: draft.regions.length > 1 ? draft.regions : [],
    slo_target: draft.slo_target ? Number(draft.slo_target) : null,
    expiry_thresholds: draft.expiry_thresholds,
    check_domain: draft.check_domain,
    maintenance: draft.maintenance.map(fromWindow),
    tags: draft.tags
      .split(",")
      .map((tag) => tag.trim())
      .filter(Boolean),
    group: draft.group.trim() || null,
    is_public: draft.is_public,
    show_url_publicly: false,
    paused: false,
  };
}
