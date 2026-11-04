import { useState } from "react";
import { Link } from "react-router-dom";
import { Check, X } from "lucide-react";
import { useChannels, usePages, useSites } from "../api/queries";

const KEY = "pw.onboarding.hidden";

export function Onboarding() {
  const [hidden, setHidden] = useState(() => localStorage.getItem(KEY) === "1");
  const sites = useSites();
  const channels = useChannels();
  const pages = usePages();
  if (hidden || !sites.data || !channels.data || !pages.data) return null;
  const steps = [
    { done: sites.data.length > 0, label: "Добавить монитор", to: "/sites/new" },
    {
      done: channels.data.some((channel) => channel.type === "telegram"),
      label: "Подключить Telegram",
      to: "/channels",
    },
    { done: pages.data.length > 0, label: "Создать статус-страницу", to: "/status-pages/new" },
  ];
  const done = steps.filter((step) => step.done).length;
  if (done === steps.length) return null;
  return (
    <section
      aria-label="Первые шаги"
      className="mx-3 mb-3 rounded-lg border border-line bg-panel p-3"
    >
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs font-semibold">Первые шаги</p>
        <button
          type="button"
          aria-label="Скрыть первые шаги"
          className="rounded p-1 text-inkMuted hover:bg-panelHover hover:text-ink"
          onClick={() => {
            localStorage.setItem(KEY, "1");
            setHidden(true);
          }}
        >
          <X size={13} aria-hidden="true" />
        </button>
      </div>
      <div
        className="mb-2.5 h-1 overflow-hidden rounded-full bg-line"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={3}
        aria-valuenow={done}
        aria-label="Пройдено шагов"
      >
        <div
          className="h-full origin-left bg-accent transition-transform"
          style={{ transform: `scaleX(${done / steps.length})` }}
        />
      </div>
      <ol className="grid gap-0.5">
        {steps.map((step) => (
          <li key={step.label}>
            <Link
              to={step.to}
              className={`flex min-h-8 items-center gap-2 rounded px-1.5 text-[13px] hover:bg-panelHover ${step.done ? "text-inkMuted line-through" : "text-ink"}`}
            >
              <span
                aria-hidden="true"
                className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${step.done ? "border-accent bg-accent text-onAccent" : "border-lineStrong"}`}
              >
                {step.done ? <Check size={10} strokeWidth={3} /> : null}
              </span>
              <span>{step.label}</span>
              <span className="sr-only">{step.done ? ", выполнено" : ""}</span>
            </Link>
          </li>
        ))}
      </ol>
    </section>
  );
}
