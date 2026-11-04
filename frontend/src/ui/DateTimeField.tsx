import { useId, useMemo, useRef, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { useDismiss } from "./useDismiss";

const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const MONTHS = new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric" });
const LABEL = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric" });

function pad(value: number) {
  return String(value).padStart(2, "0");
}

export function DateField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useDismiss(root, open, () => setOpen(false));
  const selected = value ? new Date(`${value}T00:00:00`) : null;
  const [month, setMonth] = useState(() => {
    const base = selected ?? new Date();
    return new Date(base.getFullYear(), base.getMonth(), 1);
  });
  const days = useMemo(() => {
    const first = (month.getDay() + 6) % 7;
    const total = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
    return [
      ...Array.from({ length: first }, () => null),
      ...Array.from({ length: total }, (_, index) => index + 1),
    ];
  }, [month]);
  const iso = (day: number) => `${month.getFullYear()}-${pad(month.getMonth() + 1)}-${pad(day)}`;

  return (
    <div className="relative min-w-0" ref={root}>
      <span className="label" id={`${id}-label`}>
        {label}
      </span>
      <button
        type="button"
        className="field flex items-center justify-between gap-2 text-left"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-labelledby={`${id}-label ${id}-value`}
        onClick={() => setOpen((current) => !current)}
      >
        <span id={`${id}-value`} className={selected ? "" : "text-inkFaint"}>
          {selected ? LABEL.format(selected) : "Выберите дату"}
        </span>
        <CalendarDays size={15} className="text-inkMuted" aria-hidden="true" />
      </button>
      {open ? (
        <div
          role="dialog"
          aria-label={`${label}: календарь`}
          className="popover absolute left-0 top-full mt-1 w-[272px] p-3"
        >
          <div className="mb-2 flex items-center justify-between">
            <button
              type="button"
              className="rounded p-1.5 text-inkMuted hover:bg-panelHover hover:text-ink"
              aria-label="Предыдущий месяц"
              onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}
            >
              <ChevronLeft size={16} aria-hidden="true" />
            </button>
            <span className="text-[13px] font-medium capitalize">{MONTHS.format(month)}</span>
            <button
              type="button"
              className="rounded p-1.5 text-inkMuted hover:bg-panelHover hover:text-ink"
              aria-label="Следующий месяц"
              onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}
            >
              <ChevronRight size={16} aria-hidden="true" />
            </button>
          </div>
          <div className="grid grid-cols-7 gap-0.5 text-center" role="grid">
            {WEEKDAYS.map((day) => (
              <span key={day} className="py-1 text-2xs text-inkMuted" role="columnheader">
                {day}
              </span>
            ))}
            {days.map((day, index) =>
              day === null ? (
                <span key={`gap-${index}`} />
              ) : (
                <button
                  key={day}
                  type="button"
                  role="gridcell"
                  aria-selected={value === iso(day)}
                  onClick={() => {
                    onChange(iso(day));
                    setOpen(false);
                  }}
                  className={`h-8 rounded text-[13px] ${value === iso(day) ? "bg-accent font-semibold text-onAccent" : "hover:bg-panelHover"}`}
                >
                  {day}
                </button>
              ),
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function TimeField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const id = useId();
  const [hours, minutes] = (value || "00:00").split(":");
  const update = (part: "h" | "m", raw: string) => {
    const digits = raw.replace(/\D/g, "").slice(0, 2);
    const limit = part === "h" ? 23 : 59;
    const number = Math.min(limit, Number(digits || 0));
    onChange(part === "h" ? `${pad(number)}:${minutes}` : `${hours}:${pad(number)}`);
  };
  const step = (part: "h" | "m", delta: number) => {
    const h = Number(hours);
    const m = Number(minutes);
    if (part === "h") onChange(`${pad((h + delta + 24) % 24)}:${minutes}`);
    else onChange(`${hours}:${pad((m + delta + 60) % 60)}`);
  };
  return (
    <div className="min-w-0">
      <span className="label" id={`${id}-label`}>
        {label}
      </span>
      <div
        className="field flex items-center gap-1 px-2"
        role="group"
        aria-labelledby={`${id}-label`}
      >
        {(["h", "m"] as const).map((part, index) => (
          <span key={part} className="flex items-center">
            {index ? <span className="px-0.5 text-inkMuted">:</span> : null}
            <input
              inputMode="numeric"
              aria-label={part === "h" ? "Часы" : "Минуты"}
              className="w-7 bg-transparent text-center outline-none"
              value={part === "h" ? hours : minutes}
              onChange={(event) => update(part, event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "ArrowUp" || event.key === "ArrowDown") {
                  event.preventDefault();
                  step(part, event.key === "ArrowUp" ? 1 : -1);
                }
              }}
            />
          </span>
        ))}
      </div>
    </div>
  );
}
