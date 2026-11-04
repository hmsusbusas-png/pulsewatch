import { memo, useRef, useState } from "react";
import type { DayUptime } from "../api/types";
import { formatDate, formatDuration, formatPercent, countLabel } from "../lib/format";
import { uptimeColor } from "../lib/status";
import { useWidth } from "./useWidth";

type Props = {
  days: DayUptime[];
  label: string;
  height?: number;
  showLegend?: boolean;
  compactBelow?: number;
};

export const UptimeBar = memo(function UptimeBar({
  days,
  label,
  height = 28,
  showLegend = false,
  compactBelow = 520,
}: Props) {
  const [ref, width] = useWidth<HTMLDivElement>(600);
  const narrow = typeof window !== "undefined" && window.matchMedia("(max-width: 480px)").matches;
  const shown = narrow || width < compactBelow ? days.slice(-30) : days;
  const [focus, setFocus] = useState(shown.length - 1);
  const [hover, setHover] = useState<number | null>(null);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const active = hover ?? null;
  const measured = shown.filter((day) => day.uptime !== null && day.checks);
  const total = measured.reduce((sum, day) => sum + day.checks, 0);
  const average = total
    ? measured.reduce((sum, day) => sum + (day.uptime ?? 0) * day.checks, 0) / total
    : null;
  const move = (index: number) => {
    const next = Math.max(0, Math.min(shown.length - 1, index));
    setFocus(next);
    setHover(next);
    buttons.current[next]?.focus();
  };
  const day = active !== null ? shown[active] : null;
  const left = active !== null ? ((active + 0.5) / shown.length) * 100 : 0;

  return (
    <div ref={ref} className="relative min-w-0">
      <div
        role="group"
        aria-label={`${label}: аптайм по дням за ${shown.length} дн., в среднем ${formatPercent(average, 3)}`}
        className={`flex items-stretch ${shown.length > 45 ? "gap-px" : "gap-[2px]"}`}
        style={{ height }}
        onMouseLeave={() => setHover(null)}
      >
        {shown.map((item, index) => (
          <button
            key={item.day}
            ref={(node) => {
              buttons.current[index] = node;
            }}
            type="button"
            tabIndex={index === focus ? 0 : -1}
            aria-label={`${formatDate(item.day)}: ${item.uptime === null ? "нет данных" : formatPercent(item.uptime, 3)}, инцидентов ${item.incidents}`}
            onMouseEnter={() => setHover(index)}
            onFocus={() => setHover(index)}
            onBlur={() => setHover(null)}
            onKeyDown={(event) => {
              if (event.key === "ArrowRight") move(index + 1);
              else if (event.key === "ArrowLeft") move(index - 1);
              else if (event.key === "Home") move(0);
              else if (event.key === "End") move(shown.length - 1);
              else return;
              event.preventDefault();
            }}
            className={`bar-segment ${uptimeColor(item)} outline-none focus-visible:ring-1 focus-visible:ring-ink focus-visible:ring-offset-0`}
          />
        ))}
      </div>
      {day ? (
        <div
          role="tooltip"
          className="pointer-events-none absolute bottom-full z-30 mb-2 w-[200px] -translate-x-1/2 rounded border border-line bg-raised px-3 py-2 text-xs shadow-pop"
          style={{ left: `clamp(100px, ${left}%, calc(100% - 100px))` }}
        >
          <p className="font-medium">{formatDate(day.day)}</p>
          {day.checks ? (
            <dl className="mt-1 grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 text-inkMuted">
              <dt>Аптайм</dt>
              <dd className="text-right text-ink">{formatPercent(day.uptime, 3)}</dd>
              <dt>Инциденты</dt>
              <dd className="text-right text-ink">{day.incidents}</dd>
              <dt>Простой</dt>
              <dd className="text-right text-ink">
                {day.downtime_seconds ? formatDuration(day.downtime_seconds) : "нет"}
              </dd>
              <dt>Проверки</dt>
              <dd className="text-right text-ink">
                {countLabel(day.checks, "проверка", "проверки", "проверок")}
              </dd>
            </dl>
          ) : (
            <p className="mt-1 text-inkMuted">Проверок не было</p>
          )}
        </div>
      ) : null}
      {showLegend ? (
        <div className="mt-2 flex items-center justify-between text-2xs text-inkMuted">
          <span>{shown.length} дн. назад</span>
          <span className="hidden items-center gap-3 sm:flex" aria-hidden="true">
            {[
              ["bg-accent", "≥ 99,9"],
              ["bg-yellow", "≥ 99"],
              ["bg-orange", "≥ 95"],
              ["bg-down", "< 95"],
              ["bg-line", "нет данных"],
            ].map(([color, text]) => (
              <span key={text} className="inline-flex items-center gap-1">
                <span className={`h-2 w-2 rounded-[2px] ${color}`} />
                {text}
              </span>
            ))}
          </span>
          <span>сегодня</span>
        </div>
      ) : null}
    </div>
  );
});
