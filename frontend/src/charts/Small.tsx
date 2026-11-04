import { memo } from "react";
import { formatNumber, formatShortDate } from "../lib/format";

export const Sparkline = memo(function Sparkline({
  values,
  width = 96,
  height = 28,
  label,
}: {
  values: (number | null)[];
  width?: number;
  height?: number;
  label: string;
}) {
  const clean = values.filter((value): value is number => value !== null);
  if (clean.length < 2) {
    return (
      <span className="text-xs text-inkMuted" aria-label={`${label}: недостаточно данных`}>
        н/д
      </span>
    );
  }
  const max = Math.max(...clean);
  const min = Math.min(...clean);
  const span = Math.max(1, max - min);
  const step = width / (clean.length - 1);
  const path = clean
    .map(
      (value, index) =>
        `${index ? "L" : "M"}${(index * step).toFixed(1)},${(height - 3 - ((value - min) / span) * (height - 6)).toFixed(1)}`,
    )
    .join("");
  return (
    <svg
      width={width}
      height={height}
      role="img"
      aria-label={label}
      className="block overflow-visible"
    >
      <path
        d={path}
        fill="none"
        stroke="rgb(var(--accent))"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
    </svg>
  );
});

type Slice = { value: number; color: string; label: string };

export function StatusRing({
  slices,
  size = 88,
  label,
}: {
  slices: Slice[];
  size?: number;
  label: string;
}) {
  const total = slices.reduce((sum, slice) => sum + slice.value, 0);
  const radius = size / 2 - 6;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={label}
      className="shrink-0 -rotate-90"
    >
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="rgb(var(--line))"
        strokeWidth="8"
      />
      {total
        ? slices
            .filter((slice) => slice.value > 0)
            .map((slice) => {
              const length = (slice.value / total) * circumference;
              const gap = slices.filter((item) => item.value > 0).length > 1 ? 2 : 0;
              const element = (
                <circle
                  key={slice.label}
                  cx={size / 2}
                  cy={size / 2}
                  r={radius}
                  fill="none"
                  stroke={`rgb(var(${slice.color}))`}
                  strokeWidth="8"
                  strokeDasharray={`${Math.max(0, length - gap)} ${circumference}`}
                  strokeDashoffset={-offset}
                />
              );
              offset += length;
              return element;
            })
        : null}
    </svg>
  );
}

export function Bars({
  points,
  height = 120,
  label,
}: {
  points: { day: string; count: number }[];
  height?: number;
  label: string;
}) {
  const max = Math.max(1, ...points.map((point) => point.count));
  const total = points.reduce((sum, point) => sum + point.count, 0);
  return (
    <div>
      <div
        className="flex items-end gap-[2px]"
        style={{ height }}
        role="img"
        aria-label={`${label}: всего ${formatNumber(total)}`}
      >
        {points.map((point) => (
          <span
            key={point.day}
            title={`${formatShortDate(point.day)}: ${point.count}`}
            className="min-w-0 flex-1 rounded-t-[2px] bg-accent/70 hover:bg-accent"
            style={{ height: `${Math.max(point.count ? 6 : 1, (point.count / max) * 100)}%` }}
          />
        ))}
      </div>
      <div className="mt-1.5 flex justify-between text-2xs text-inkMuted" aria-hidden="true">
        <span>{points.length ? formatShortDate(points[0].day) : ""}</span>
        <span>{points.length ? formatShortDate(points[points.length - 1].day) : ""}</span>
      </div>
    </div>
  );
}
