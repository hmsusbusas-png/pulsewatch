import { memo } from "react";
import type { MouseEvent } from "react";
import { Link } from "react-router-dom";
import type { DayUptime, Site } from "../../api/types";
import { UptimeBar } from "../../charts/UptimeBar";
import { Sparkline } from "../../charts/Small";
import { formatLatency, formatPercent, hostOf } from "../../lib/format";
import { MONITOR_LABEL, siteTone, TONE_LABEL, uptimeText } from "../../lib/status";
import { useFlash } from "../../live/live-store";
import { Checkbox } from "../../ui/Controls";
import { StatusDot, Time } from "../../ui/Feedback";
import { Skeleton } from "../../ui/Feedback";

export const ROW_GRID =
  "grid grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 md:grid-cols-[28px_minmax(180px,1.3fr)_minmax(200px,2fr)_78px_88px_92px] xl:grid-cols-[28px_minmax(200px,1.2fr)_minmax(260px,2fr)_80px_110px_90px_104px]";

type Props = {
  site: Site;
  days: DayUptime[] | undefined;
  index: number;
  selected: boolean;
  onSelect: (index: number, event: MouseEvent | React.KeyboardEvent, next: boolean) => void;
};

export const MonitorRow = memo(function MonitorRow({
  site,
  days,
  index,
  selected,
  onSelect,
}: Props) {
  const tone = siteTone(site);
  const flash = useFlash(site.id);
  const recent = flash && Date.now() - flash < 1500;
  const target = site.monitor_type === "heartbeat" ? "Ожидает сигналов cron" : hostOf(site.url);
  return (
    <div
      className={`relative border-b border-line px-3 py-3 last:border-b-0 hover:bg-panelHover/60 sm:px-4 ${ROW_GRID} ${selected ? "bg-accent/[0.05]" : ""}`}
      data-testid="monitor-row"
    >
      {recent ? <span key={flash} className="flash-overlay" aria-hidden="true" /> : null}
      <span className="col-start-1 row-start-1 md:col-start-auto md:row-start-auto">
        <Checkbox
          checked={selected}
          ariaLabel={`Выбрать «${site.name}»`}
          onChange={(next, event) => onSelect(index, event as MouseEvent, next)}
        />
      </span>
      <div className="col-start-2 row-start-1 min-w-0 md:col-start-auto md:row-start-auto">
        <Link
          to={`/sites/${site.id}`}
          className="group flex min-w-0 items-center gap-2.5 rounded outline-offset-2"
        >
          <StatusDot tone={tone} pulse />
          <span className="truncate text-[13.5px] font-medium group-hover:text-accent">
            {site.name}
          </span>
          <span className="sr-only">, {TONE_LABEL[tone]}</span>
        </Link>
        <p className="mt-0.5 flex min-w-0 items-center gap-1.5 pl-5 text-xs text-inkMuted">
          <span className="shrink-0 rounded bg-panelHover px-1.5 py-px text-2xs">
            {MONITOR_LABEL[site.monitor_type ?? "http"]}
          </span>
          <span className="truncate">{target}</span>
        </p>
      </div>
      <div className="col-span-2 col-start-2 row-start-2 min-w-0 md:col-span-1 md:col-start-auto md:row-start-auto">
        {days ? (
          <UptimeBar days={days} label={site.name} height={24} compactBelow={220} />
        ) : (
          <Skeleton className="h-6" />
        )}
      </div>
      <div
        className={`col-start-3 row-start-1 text-right md:col-start-auto text-[13px] font-medium md:row-start-auto ${uptimeText(site.uptime_30d)}`}
      >
        <span className="sr-only">Аптайм за 30 дней: </span>
        {formatPercent(site.uptime_30d)}
      </div>
      <div className="hidden xl:block">
        <Sparkline
          values={site.response_series.map((point) => point.ms)}
          label={`Отклик ${site.name}`}
          width={104}
        />
      </div>
      <div className="hidden text-right text-[13px] md:block">
        {site.monitor_type === "heartbeat" ||
        site.monitor_type === "ssl-only" ||
        tone === "down" ? (
          <span className="text-inkMuted">н/д</span>
        ) : (
          formatLatency(site.latest_response_ms)
        )}
      </div>
      <div className="hidden text-right text-xs text-inkMuted md:block">
        {site.paused ? "на паузе" : <Time value={site.last_checked_at} />}
      </div>
    </div>
  );
});
