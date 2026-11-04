import { formatLatency, formatPercent } from "../lib/format";

type Phases = {
  dns_ms?: number | null;
  connect_ms?: number | null;
  tls_ms?: number | null;
  ttfb_ms?: number | null;
  transfer_ms?: number | null;
};

const STAGES = [
  { key: "dns_ms", label: "DNS", color: "bg-violet", dot: "bg-violet" },
  { key: "connect_ms", label: "TCP", color: "bg-info", dot: "bg-info" },
  { key: "tls_ms", label: "TLS", color: "bg-yellow", dot: "bg-yellow" },
  { key: "ttfb_ms", label: "TTFB", color: "bg-accent", dot: "bg-accent" },
  { key: "transfer_ms", label: "Загрузка", color: "bg-orange", dot: "bg-orange" },
] as const;

export function Waterfall({ phases, title }: { phases: Phases; title: string }) {
  const values = STAGES.map((stage) => ({ ...stage, value: phases[stage.key] ?? null }));
  const present = values.filter((stage) => stage.value !== null);
  const total = present.reduce((sum, stage) => sum + (stage.value ?? 0), 0);
  if (!present.length) {
    return <p className="text-[13px] text-inkMuted">Для этой проверки этапы не измерялись.</p>;
  }
  return (
    <figure className="min-w-0">
      <figcaption className="sr-only">
        {title}: {present.map((stage) => `${stage.label} ${formatLatency(stage.value)}`).join(", ")}
      </figcaption>
      <div className="flex h-6 overflow-hidden rounded bg-panelHover" aria-hidden="true">
        {present.map((stage) => (
          <span
            key={stage.key}
            className={`${stage.color} h-full border-r border-panel last:border-r-0`}
            style={{ width: `${Math.max(1.5, ((stage.value ?? 0) / Math.max(1, total)) * 100)}%` }}
            title={`${stage.label}: ${formatLatency(stage.value)}`}
          />
        ))}
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-5">
        {values.map((stage) => (
          <div key={stage.key} className="min-w-0">
            <dt className="flex items-center gap-1.5 text-xs text-inkMuted">
              <span className={`h-2 w-2 rounded-[2px] ${stage.dot}`} aria-hidden="true" />
              {stage.label}
            </dt>
            <dd className="mt-0.5 text-[13px] font-medium">
              {stage.value === null ? (
                <span className="text-inkMuted">н/д</span>
              ) : (
                <>
                  {formatLatency(stage.value)}
                  <span className="ml-1.5 text-xs font-normal text-inkMuted">
                    {formatPercent(((stage.value ?? 0) / Math.max(1, total)) * 100, 0)}
                  </span>
                </>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </figure>
  );
}
