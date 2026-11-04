import type { CSSProperties, ReactNode } from "react";
import { CheckCircle2, CircleAlert, Megaphone, Wrench } from "lucide-react";
import type { PublicPage } from "../../api/types";
import { UptimeBar } from "../../charts/UptimeBar";
import { formatDuration, formatFullDateTime, formatPercent } from "../../lib/format";
import { uptimeText } from "../../lib/status";
import { ACCENTS } from "./accents";

const NOTE: Record<string, string> = {
  investigating: "Расследуем",
  identified: "Причина найдена",
  monitoring: "Наблюдаем",
  resolved: "Решено",
};

export function StatusView({ page, footer }: { page: PublicPage; footer?: ReactNode }) {
  const accent = ACCENTS[page.accent as keyof typeof ACCENTS] ?? ACCENTS.emerald;
  const overall = page.overall.status;
  const banner =
    overall === "operational"
      ? "border-accent/30 bg-accent/10"
      : overall === "maintenance"
        ? "border-info/30 bg-info/10"
        : overall === "degraded"
          ? "border-warn/30 bg-warn/10"
          : "border-down/30 bg-down/10";
  const Icon =
    overall === "operational" ? CheckCircle2 : overall === "maintenance" ? Wrench : CircleAlert;
  const color =
    overall === "operational"
      ? "text-accent"
      : overall === "maintenance"
        ? "text-info"
        : overall === "degraded"
          ? "text-warn"
          : "text-down";
  return (
    <div style={{ "--brand": accent.rgb } as CSSProperties} className="min-w-0">
      <div
        className="mb-5 h-1 w-16 rounded-full"
        style={{ background: "rgb(var(--brand))" }}
        aria-hidden="true"
      />
      <header className="flex items-center gap-3">
        {page.logo_url ? (
          <img
            src={page.logo_url}
            alt=""
            className="h-9 w-9 rounded object-contain"
            referrerPolicy="no-referrer"
          />
        ) : null}
        <div className="min-w-0">
          <h1 className="truncate text-xl font-semibold tracking-[-0.02em]">{page.title}</h1>
          {page.description ? (
            <p className="mt-0.5 text-[13px] text-inkMuted">{page.description}</p>
          ) : null}
        </div>
      </header>
      <div
        className={`mt-5 flex items-center gap-3 rounded-lg border px-4 py-3.5 ${banner}`}
        role="status"
      >
        <Icon size={20} className={color} aria-hidden="true" />
        <p className="text-[15px] font-semibold">{page.overall.label}</p>
      </div>
      {page.announcements.map((item) => (
        <div
          key={item.id}
          className="mt-3 flex gap-3 rounded-lg border border-line bg-panel px-4 py-3"
        >
          <Megaphone size={16} className="mt-0.5 shrink-0 text-info" aria-hidden="true" />
          <div className="min-w-0">
            <p className="text-[13.5px] font-medium">{item.title}</p>
            <p className="mt-0.5 text-[13px] text-inkMuted">{item.body}</p>
          </div>
        </div>
      ))}
      <section className="surface mt-5 divide-y divide-line" aria-label="Сервисы">
        {page.sites.map((site) => {
          const tone =
            site.status === "down"
              ? "text-down"
              : site.maintenance
                ? "text-info"
                : site.degraded
                  ? "text-warn"
                  : site.status === "up"
                    ? "text-accent"
                    : "text-inkMuted";
          const label =
            site.status === "down"
              ? "Недоступен"
              : site.maintenance
                ? "Обслуживание"
                : site.degraded
                  ? "Медленно"
                  : site.status === "up"
                    ? "Работает"
                    : "Нет данных";
          return (
            <div key={site.id} className="px-4 py-4">
              <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-[14px] font-medium">{site.name}</p>
                  {site.url ? <p className="truncate text-xs text-inkMuted">{site.url}</p> : null}
                </div>
                <p className="text-right text-[13px]">
                  <span className={`font-medium ${tone}`}>{label}</span>
                  <span className={`ml-3 ${uptimeText(site.uptime_90d)}`}>
                    {formatPercent(site.uptime_90d, 3)}
                  </span>
                  <span className="ml-1 text-xs text-inkMuted">за 90 дней</span>
                </p>
              </div>
              <UptimeBar days={site.days} label={site.name} height={32} showLegend={false} />
            </div>
          );
        })}
        {!page.sites.length ? (
          <p className="px-4 py-6 text-center text-[13px] text-inkMuted">Мониторы не выбраны</p>
        ) : null}
      </section>
      <section className="mt-8" aria-labelledby="history-title">
        <h2 id="history-title" className="section-title">
          История инцидентов
        </h2>
        {page.incidents.length ? (
          <ol className="mt-3 grid gap-3">
            {page.incidents.slice(0, 20).map((incident) => (
              <li
                key={incident.id}
                id={`incident-${incident.id}`}
                className="rounded-lg border border-line bg-panel px-4 py-3"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="text-[13.5px] font-medium">
                    {incident.site_name}:{" "}
                    {incident.kind === "degraded" ? "снижение производительности" : "недоступность"}
                  </p>
                  <p className="text-xs text-inkMuted">
                    {formatFullDateTime(incident.started_at)} ·{" "}
                    {incident.ended_at ? formatDuration(incident.duration_seconds) : "продолжается"}
                  </p>
                </div>
                {incident.notes.length ? (
                  <ul className="mt-2 grid gap-1.5 border-l border-line pl-3">
                    {incident.notes.map((note) => (
                      <li key={`${note.created_at}-${note.status}`} className="text-[13px]">
                        <span className="font-medium">{NOTE[note.status] ?? note.status}</span>
                        <span className="text-inkMuted">: {note.text}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ol>
        ) : (
          <p className="mt-2 text-[13px] text-inkMuted">За 90 дней инцидентов не было.</p>
        )}
      </section>
      {footer}
    </div>
  );
}
