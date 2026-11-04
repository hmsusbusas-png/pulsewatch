const NBSP = " ";
let displayZone = "Europe/Moscow";
const cache = new Map<string, Intl.DateTimeFormat>();

export function setDisplayZone(zone: string) {
  if (zone && zone !== displayZone) {
    displayZone = zone;
    cache.clear();
  }
}

export function getDisplayZone() {
  return displayZone;
}

function formatter(key: string, options: Intl.DateTimeFormatOptions) {
  const id = `${displayZone}|${key}`;
  let value = cache.get(id);
  if (!value) {
    value = new Intl.DateTimeFormat("ru-RU", { timeZone: displayZone, ...options });
    cache.set(id, value);
  }
  return value;
}

const numberFormats = new Map<number, Intl.NumberFormat>();

export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "н/д";
  let format = numberFormats.get(digits);
  if (!format) {
    format = new Intl.NumberFormat("ru-RU", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
    numberFormats.set(digits, format);
  }
  return format.format(value).replace(/\u202f/g, NBSP);
}

export function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "н/д";
  if (ms < 1000) return `${formatNumber(Math.round(ms))}${NBSP}мс`;
  if (ms < 60_000) return `${formatNumber(ms / 1000, ms < 10_000 ? 2 : 1)}${NBSP}с`;
  return formatDuration(Math.round(ms / 1000));
}

export function formatPercent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "н/д";
  const fixed = value >= 100 ? 100 : value;
  const precision = fixed === 100 ? 0 : digits;
  return `${formatNumber(fixed, precision)}${NBSP}%`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "н/д";
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total}${NBSP}с`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes < 60) return rest ? `${minutes}${NBSP}мин ${rest}${NBSP}с` : `${minutes}${NBSP}мин`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  if (hours < 48)
    return restMinutes ? `${hours}${NBSP}ч ${restMinutes}${NBSP}мин` : `${hours}${NBSP}ч`;
  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  return restHours ? `${days}${NBSP}д ${restHours}${NBSP}ч` : `${days}${NBSP}д`;
}

function toDate(value: string | number | Date | null | undefined): Date | null {
  if (value === null || value === undefined) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(value: string | number | Date | null | undefined): string {
  const date = toDate(value);
  return date
    ? formatter("datetime", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      }).format(date)
    : "н/д";
}

export function formatFullDateTime(value: string | number | Date | null | undefined): string {
  const date = toDate(value);
  return date
    ? formatter("full", {
        day: "numeric",
        month: "long",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        timeZoneName: "short",
      }).format(date)
    : "н/д";
}

export function formatDate(value: string | number | Date | null | undefined): string {
  const date = toDate(value);
  return date
    ? formatter("date", { day: "numeric", month: "long", year: "numeric" }).format(date)
    : "н/д";
}

export function formatShortDate(value: string | number | Date | null | undefined): string {
  const date = toDate(value);
  return date ? formatter("short", { day: "numeric", month: "short" }).format(date) : "н/д";
}

export function formatClock(value: string | number | Date | null | undefined): string {
  const date = toDate(value);
  return date ? formatter("clock", { hour: "2-digit", minute: "2-digit" }).format(date) : "";
}

export function formatAxisTime(value: number, spanMs: number): string {
  if (spanMs <= 36 * 3600_000) return formatClock(value);
  return formatShortDate(value);
}

export function formatRelative(
  value: string | number | Date | null | undefined,
  now = Date.now(),
): string {
  const date = toDate(value);
  if (!date) return "нет данных";
  const diff = Math.round((date.getTime() - now) / 1000);
  const abs = Math.abs(diff);
  const future = diff > 0;
  const wrap = (text: string) => (future ? `через ${text}` : `${text} назад`);
  if (abs < 30) return future ? "сейчас" : "только что";
  if (abs < 90) return wrap(`1${NBSP}мин`);
  if (abs < 3600) return wrap(`${Math.round(abs / 60)}${NBSP}мин`);
  if (abs < 86_400) return wrap(`${Math.round(abs / 3600)}${NBSP}ч`);
  if (abs < 86_400 * 45) {
    const days = Math.round(abs / 86_400);
    return wrap(`${days}${NBSP}${plural(days, "день", "дня", "дней")}`);
  }
  return formatDate(date);
}

export function plural(count: number, one: string, few: string, many: string): string {
  const mod10 = Math.abs(count) % 10;
  const mod100 = Math.abs(count) % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

export function countLabel(count: number, one: string, few: string, many: string): string {
  return `${formatNumber(count)}${NBSP}${plural(count, one, few, many)}`;
}

export function daysUntil(value: string | null | undefined): number | null {
  const date = toDate(value);
  return date ? Math.floor((date.getTime() - Date.now()) / 86_400_000) : null;
}

export function formatInterval(seconds: number): string {
  if (seconds < 60) return `каждые ${seconds}${NBSP}с`;
  if (seconds < 3600) {
    const minutes = Math.round(seconds / 60);
    return minutes === 1 ? "каждую минуту" : `каждые ${minutes}${NBSP}мин`;
  }
  if (seconds < 86_400) {
    const hours = Math.round(seconds / 3600);
    return hours === 1 ? "каждый час" : `каждые ${hours}${NBSP}ч`;
  }
  const days = Math.round(seconds / 86_400);
  return days === 1 ? "раз в сутки" : `каждые ${days}${NBSP}${plural(days, "день", "дня", "дней")}`;
}

export function formatMoney(kop: number): string {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: kop % 100 === 0 ? 0 : 2,
  })
    .format(kop / 100)
    .replace(/\u202f/g, NBSP);
}

export function hostOf(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.port ? `${parsed.hostname}:${parsed.port}` : parsed.hostname;
  } catch {
    return url;
  }
}
