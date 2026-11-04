import { useId, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { ZoomOut } from "lucide-react";
import type { Span, StatsPoint } from "../api/types";
import { formatAxisTime, formatFullDateTime, formatLatency, formatNumber } from "../lib/format";
import { Button } from "../ui/Button";
import { niceCeiling, useWidth } from "./useWidth";

type Props = {
  points: StatsPoint[];
  incidents: Span[];
  maintenance: Span[];
  start: string;
  end: string;
  resolution: number;
  baseline?: number | null;
  height?: number;
  label: string;
};

const PAD = { top: 12, right: 12, bottom: 26, left: 52 };

export function ResponseChart({
  points,
  incidents,
  maintenance,
  start,
  end,
  resolution,
  baseline,
  height = 260,
  label,
}: Props) {
  const gradient = useId();
  const [container, width] = useWidth<HTMLDivElement>();
  const [zoom, setZoom] = useState<[number, number] | null>(null);
  const [active, setActive] = useState<number | null>(null);
  const [drag, setDrag] = useState<[number, number] | null>(null);
  const svg = useRef<SVGSVGElement>(null);

  const domain = useMemo<[number, number]>(
    () => zoom ?? [Date.parse(start), Date.parse(end)],
    [zoom, start, end],
  );
  const visible = useMemo(
    () =>
      points
        .map((point) => ({ ...point, time: Date.parse(point.t) }))
        .filter((point) => point.time >= domain[0] - resolution * 1000 && point.time <= domain[1]),
    [points, domain, resolution],
  );
  const innerWidth = Math.max(10, width - PAD.left - PAD.right);
  const innerHeight = height - PAD.top - PAD.bottom;
  const peak = Math.max(
    1,
    ...visible.map((point) => Math.max(point.avg_ms ?? 0, point.p95_ms ?? 0)),
    baseline ? baseline * 1.2 : 0,
  );
  const yMax = niceCeiling(peak * 1.08);
  const x = (time: number) =>
    PAD.left + ((time - domain[0]) / Math.max(1, domain[1] - domain[0])) * innerWidth;
  const y = (value: number) => PAD.top + innerHeight - (value / yMax) * innerHeight;

  const segments = useMemo(() => {
    const groups: (typeof visible)[] = [];
    let current: typeof visible = [];
    for (const point of visible) {
      const previous = current[current.length - 1];
      if (point.avg_ms === null) {
        if (current.length) groups.push(current);
        current = [];
        continue;
      }
      if (previous && point.time - previous.time > resolution * 1000 * 2.5) {
        groups.push(current);
        current = [];
      }
      current.push(point);
    }
    if (current.length) groups.push(current);
    return groups;
  }, [visible, resolution]);

  const line = (group: typeof visible, key: "avg_ms" | "p95_ms") =>
    group
      .filter((point) => point[key] !== null)
      .map(
        (point, index) =>
          `${index ? "L" : "M"}${x(point.time).toFixed(1)},${y(point[key] ?? 0).toFixed(1)}`,
      )
      .join("");
  const area = (group: typeof visible) =>
    group.length
      ? `${line(group, "avg_ms")}L${x(group[group.length - 1].time).toFixed(1)},${y(0)}L${x(group[0].time).toFixed(1)},${y(0)}Z`
      : "";

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((ratio) => yMax * ratio);
  const xTicks = Array.from(
    { length: 6 },
    (_, index) => domain[0] + ((domain[1] - domain[0]) * index) / 5,
  );
  const toTime = (clientX: number) => {
    const rect = svg.current?.getBoundingClientRect();
    if (!rect) return domain[0];
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left - PAD.left) / innerWidth));
    return domain[0] + ratio * (domain[1] - domain[0]);
  };
  const nearest = (time: number) => {
    let best = -1;
    let distance = Infinity;
    visible.forEach((point, index) => {
      const delta = Math.abs(point.time - time);
      if (delta < distance) {
        distance = delta;
        best = index;
      }
    });
    return best >= 0 ? best : null;
  };
  const onMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    const time = toTime(event.clientX);
    setActive(nearest(time));
    if (drag) setDrag([drag[0], time]);
  };
  const onUp = () => {
    if (drag) {
      const [a, b] = drag[0] < drag[1] ? drag : [drag[1], drag[0]];
      const pixels = ((b - a) / Math.max(1, domain[1] - domain[0])) * innerWidth;
      if (pixels > 10 && b - a > resolution * 2000) setZoom([a, b]);
      setDrag(null);
    }
  };

  const point = active !== null ? visible[active] : null;
  const summary = visible.length
    ? `${label}: ${visible.length} точек, средний отклик до ${formatLatency(Math.max(...visible.map((item) => item.avg_ms ?? 0)))}`
    : `${label}: нет данных за период`;
  const tooltipLeft = point ? Math.min(Math.max(x(point.time) - 90, 0), width - 190) : 0;

  return (
    <div ref={container} className="relative select-none">
      {zoom ? (
        <Button
          size="xs"
          variant="secondary"
          className="absolute right-2 top-0 z-10"
          onClick={() => setZoom(null)}
          icon={<ZoomOut size={13} aria-hidden="true" />}
        >
          Сбросить масштаб
        </Button>
      ) : null}
      <svg
        ref={svg}
        width={width}
        height={height}
        role="img"
        aria-label={summary}
        tabIndex={0}
        className="block touch-pan-y outline-none focus-visible:ring-2 focus-visible:ring-accent"
        onPointerMove={onMove}
        onPointerLeave={() => {
          setActive(null);
          setDrag(null);
        }}
        onPointerDown={(event) => {
          if (event.pointerType === "mouse")
            setDrag([toTime(event.clientX), toTime(event.clientX)]);
        }}
        onPointerUp={onUp}
        onKeyDown={(event) => {
          if (!visible.length) return;
          if (event.key === "ArrowRight")
            setActive((index) => Math.min(visible.length - 1, (index ?? -1) + 1));
          else if (event.key === "ArrowLeft")
            setActive((index) => Math.max(0, (index ?? visible.length) - 1));
          else if (event.key === "Escape") {
            setZoom(null);
            setActive(null);
          } else return;
          event.preventDefault();
        }}
        onBlur={() => setActive(null)}
      >
        <defs>
          <linearGradient id={gradient} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="rgb(var(--accent))" stopOpacity="0.32" />
            <stop offset="100%" stopColor="rgb(var(--accent))" stopOpacity="0" />
          </linearGradient>
        </defs>
        {maintenance.map((span) => {
          const left = x(Math.max(domain[0], Date.parse(span.start)));
          const right = x(Math.min(domain[1], Date.parse(span.end ?? end)));
          return right > left ? (
            <rect
              key={`m-${span.start}`}
              x={left}
              y={PAD.top}
              width={right - left}
              height={innerHeight}
              fill="rgb(var(--unknown))"
              opacity="0.14"
            />
          ) : null;
        })}
        {incidents.map((span) => {
          const left = x(Math.max(domain[0], Date.parse(span.start)));
          const right = x(Math.min(domain[1], Date.parse(span.end ?? end)));
          const color = span.kind === "degraded" ? "--warn" : "--down";
          return right > left - 1 ? (
            <rect
              key={`i-${span.incident_id ?? span.start}`}
              x={left}
              y={PAD.top}
              width={Math.max(2, right - left)}
              height={innerHeight}
              fill={`rgb(var(${color}))`}
              opacity={span.kind === "degraded" ? 0.1 : 0.16}
            />
          ) : null;
        })}
        {ticks.map((tick) => (
          <g key={tick}>
            <line
              x1={PAD.left}
              x2={width - PAD.right}
              y1={y(tick)}
              y2={y(tick)}
              stroke="rgb(var(--line))"
              strokeDasharray={tick ? "2 4" : undefined}
            />
            <text
              x={PAD.left - 8}
              y={y(tick) + 4}
              textAnchor="end"
              className="fill-inkMuted text-[10.5px]"
            >
              {formatLatency(tick)}
            </text>
          </g>
        ))}
        {xTicks.map((tick, index) => (
          <text
            key={tick}
            x={x(tick)}
            y={height - 6}
            textAnchor={index === 0 ? "start" : index === xTicks.length - 1 ? "end" : "middle"}
            className="fill-inkMuted text-[10.5px]"
          >
            {formatAxisTime(tick, domain[1] - domain[0])}
          </text>
        ))}
        {baseline ? (
          <line
            x1={PAD.left}
            x2={width - PAD.right}
            y1={y(baseline)}
            y2={y(baseline)}
            stroke="rgb(var(--info))"
            strokeOpacity="0.6"
            strokeDasharray="6 4"
          />
        ) : null}
        {segments.map((group) => (
          <g key={`${group[0].time}`}>
            <path d={area(group)} fill={`url(#${gradient})`} />
            <path
              d={line(group, "p95_ms")}
              fill="none"
              stroke="rgb(var(--accent))"
              strokeOpacity="0.35"
              strokeWidth="1"
            />
            <path
              d={line(group, "avg_ms")}
              fill="none"
              stroke="rgb(var(--accent))"
              strokeWidth="1.6"
              strokeLinejoin="round"
            />
          </g>
        ))}
        {visible
          .filter((item) => item.failures > 0)
          .map((item) => (
            <rect
              key={`f-${item.time}`}
              x={x(item.time) - 1.5}
              y={PAD.top + innerHeight - 4}
              width={3}
              height={4}
              fill="rgb(var(--down))"
            />
          ))}
        {drag ? (
          <rect
            x={x(Math.min(...drag))}
            y={PAD.top}
            width={Math.abs(x(drag[1]) - x(drag[0]))}
            height={innerHeight}
            fill="rgb(var(--info))"
            opacity="0.15"
          />
        ) : null}
        {point && point.avg_ms !== null ? (
          <g pointerEvents="none">
            <line
              x1={x(point.time)}
              x2={x(point.time)}
              y1={PAD.top}
              y2={PAD.top + innerHeight}
              stroke="rgb(var(--ink-muted))"
              strokeDasharray="3 3"
            />
            <line
              x1={PAD.left}
              x2={width - PAD.right}
              y1={y(point.avg_ms)}
              y2={y(point.avg_ms)}
              stroke="rgb(var(--ink-muted))"
              strokeOpacity="0.4"
              strokeDasharray="3 3"
            />
            <circle
              cx={x(point.time)}
              cy={y(point.avg_ms)}
              r={4}
              fill="rgb(var(--canvas))"
              stroke="rgb(var(--accent))"
              strokeWidth="2"
            />
          </g>
        ) : null}
      </svg>
      {point ? (
        <div
          className="pointer-events-none absolute top-1 z-10 w-[180px] rounded border border-line bg-raised px-3 py-2 text-xs shadow-pop"
          style={{ left: tooltipLeft }}
          aria-live="polite"
        >
          <p className="font-medium">{formatFullDateTime(point.time)}</p>
          <dl className="mt-1.5 grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 text-inkMuted">
            <dt>Средний</dt>
            <dd className="text-right text-ink">{formatLatency(point.avg_ms)}</dd>
            <dt>p95</dt>
            <dd className="text-right text-ink">{formatLatency(point.p95_ms)}</dd>
            <dt>Проверок</dt>
            <dd className="text-right text-ink">{formatNumber(point.checks)}</dd>
            <dt>Сбоев</dt>
            <dd className={`text-right ${point.failures ? "text-down" : "text-ink"}`}>
              {formatNumber(point.failures)}
            </dd>
          </dl>
        </div>
      ) : null}
    </div>
  );
}
