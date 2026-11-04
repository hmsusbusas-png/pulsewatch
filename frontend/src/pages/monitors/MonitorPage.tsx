import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, ExternalLink, Pause, Pencil, Play, RefreshCw, Trash2 } from "lucide-react";
import {
  useChecks,
  useSite,
  useSiteDaily,
  useSiteIncidents,
  useSlo,
  useStats,
  useUpdateSite,
} from "../../api/queries";
import { isRange } from "../../api/types";
import type { Check, Site, StatsRange } from "../../api/types";
import { ResponseChart } from "../../charts/ResponseChart";
import { UptimeBar } from "../../charts/UptimeBar";
import { Waterfall } from "../../charts/Waterfall";
import { rememberMonitor } from "../../features/recent";
import {
  formatDateTime,
  formatDuration,
  formatInterval,
  formatLatency,
  formatNumber,
  formatPercent,
} from "../../lib/format";
import { MONITOR_LABEL, siteTone, uptimeText } from "../../lib/status";
import { Button, ButtonLink } from "../../ui/Button";
import { Checkbox, Segmented } from "../../ui/Controls";
import { EmptyState, ErrorPanel, Metric, Skeleton, StatusPill, Time } from "../../ui/Feedback";
import { notifyError, toast } from "../../ui/toast-store";
import { requestCheck, useDeferredSiteDelete } from "./actions";
import { CertificateCard, DnsCard, HeartbeatCard, RegionsCard, SloCard } from "./MonitorCards";

const RANGE_LABEL: Record<StatsRange, string> = {
  "24h": "24 ч",
  "7d": "7 дней",
  "30d": "30 дней",
  "90d": "90 дней",
};

function useCountdown(target: string | null | undefined) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return target ? Math.max(0, Math.round((Date.parse(target) - now) / 1000)) : null;
}

function FirstCheck({ site }: { site: Site }) {
  const left = useCountdown(site.next_check_at);
  if (site.last_checked_at) return null;
  return (
    <div
      className="mb-5 flex flex-wrap items-center gap-3 rounded-lg border border-info/30 bg-info/[0.07] px-4 py-3 text-[13px]"
      role="status"
      aria-live="polite"
    >
      <RefreshCw size={16} className="animate-spin text-info" aria-hidden="true" />
      <span className="flex-1">
        {left && left > 0
          ? `Первая проверка через ${formatDuration(left)}. Результат появится здесь сам.`
          : "Идёт первая проверка. Результат появится через несколько секунд."}
      </span>
    </div>
  );
}

function CheckNowButton({ site }: { site: Site }) {
  const [pending, setPending] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  useEffect(() => {
    if (pending && site.last_checked_at && Date.parse(site.last_checked_at) > Date.parse(pending)) {
      setPending(null);
      setResult(
        site.status === "up"
          ? `ответ за ${formatLatency(site.latest_response_ms)}`
          : "проверка не пройдена",
      );
      const timer = window.setTimeout(() => setResult(null), 6000);
      return () => window.clearTimeout(timer);
    }
    return undefined;
  }, [pending, site.last_checked_at, site.status, site.latest_response_ms]);
  useEffect(() => {
    if (!pending) return;
    const timer = window.setTimeout(() => setPending(null), 45_000);
    return () => window.clearTimeout(timer);
  }, [pending]);
  if (site.monitor_type === "heartbeat") return null;
  return (
    <span className="inline-flex items-center gap-2">
      <Button
        size="sm"
        loading={pending !== null}
        disabled={site.paused}
        onClick={async () => {
          const queued = await requestCheck(site);
          if (queued) setPending(queued.queued_at);
        }}
        icon={<RefreshCw size={14} aria-hidden="true" />}
      >
        {pending ? "Проверяем…" : "Проверить сейчас"}
      </Button>
      {result ? (
        <span
          className={`text-xs ${site.status === "up" ? "text-accent" : "text-down"}`}
          role="status"
        >
          {result}
        </span>
      ) : null}
    </span>
  );
}

function ChecksTable({
  id,
  range,
  onSelect,
  selected,
}: {
  id: number;
  range: StatsRange;
  onSelect: (check: Check) => void;
  selected: number | null;
}) {
  const [failed, setFailed] = useState(false);
  const checks = useChecks(id, range, failed);
  const rows = checks.data?.pages.flat() ?? [];
  return (
    <section className="surface" aria-labelledby="checks-title">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <h2 id="checks-title" className="section-title">
          Проверки
        </h2>
        <Checkbox
          checked={failed}
          onChange={setFailed}
          label="Только сбои"
          className="min-h-0 py-0"
        />
      </div>
      {checks.isError ? (
        <div className="px-4 pb-4">
          <ErrorPanel error={checks.error} onRetry={() => void checks.refetch()} />
        </div>
      ) : (
        <div
          tabIndex={0}
          role="region"
          aria-label="Таблица"
          className="table-scroll max-h-[420px] overflow-y-auto"
        >
          <table className="table min-w-[640px]">
            <thead>
              <tr>
                <th scope="col">Время</th>
                <th scope="col">Результат</th>
                <th scope="col" className="text-right">
                  Отклик
                </th>
                <th scope="col" className="text-right">
                  TTFB
                </th>
                <th scope="col">Регион</th>
                <th scope="col">Ошибка</th>
              </tr>
            </thead>
            <tbody>
              {checks.isPending
                ? Array.from({ length: 5 }, (_, index) => (
                    <tr key={index}>
                      <td colSpan={6}>
                        <Skeleton className="h-4" />
                      </td>
                    </tr>
                  ))
                : rows.map((check) => (
                    <tr
                      key={check.id}
                      className={`row-interactive ${selected === check.id ? "bg-accent/[0.07]" : ""}`}
                      onClick={() => onSelect(check)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          onSelect(check);
                        }
                      }}
                      tabIndex={0}
                      aria-selected={selected === check.id}
                    >
                      <td className="whitespace-nowrap">{formatDateTime(check.checked_at)}</td>
                      <td>
                        <span className={check.ok ? "text-accent" : "text-down"}>
                          {check.ok ? "успех" : "сбой"}
                        </span>
                        {check.status_code ? (
                          <span className="ml-2 text-inkMuted">{check.status_code}</span>
                        ) : null}
                      </td>
                      <td className="text-right">{formatLatency(check.response_ms)}</td>
                      <td className="text-right">{formatLatency(check.ttfb_ms)}</td>
                      <td className="text-inkMuted">{check.region ?? "н/д"}</td>
                      <td
                        className="max-w-[260px] truncate text-inkMuted"
                        title={check.error ?? ""}
                      >
                        {check.error ?? ""}
                      </td>
                    </tr>
                  ))}
            </tbody>
          </table>
          {!checks.isPending && rows.length === 0 ? (
            <EmptyState title={failed ? "Сбоев нет" : "Проверок пока нет"}>
              {failed
                ? "За выбранный период все проверки прошли успешно."
                : "Данные появятся после первой проверки."}
            </EmptyState>
          ) : null}
        </div>
      )}
      {checks.hasNextPage ? (
        <div className="border-t border-line p-2 text-center">
          <Button
            size="sm"
            variant="ghost"
            loading={checks.isFetchingNextPage}
            onClick={() => void checks.fetchNextPage()}
          >
            Показать ещё
          </Button>
        </div>
      ) : null}
    </section>
  );
}

export function MonitorPage() {
  const id = Number(useParams().id);
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const range: StatsRange = isRange(params.get("range"))
    ? (params.get("range") as StatsRange)
    : "24h";
  const site = useSite(id);
  const stats = useStats(id, range);
  const slo = useSlo(id, site.data?.monitor_type !== "heartbeat");
  const daily = useSiteDaily(id);
  const incidents = useSiteIncidents(id);
  const update = useUpdateSite(id);
  const remove = useDeferredSiteDelete();
  const [picked, setPicked] = useState<Check | null>(null);
  const token = (location.state as { heartbeatToken?: string } | null)?.heartbeatToken ?? null;

  useEffect(() => {
    if (site.data) {
      rememberMonitor({ id: site.data.id, name: site.data.name });
      document.title = `${site.data.name}. pulsewatch`;
    }
    return () => {
      document.title = "pulsewatch";
    };
  }, [site.data]);

  const lastPhases = useMemo(() => {
    const timings = stats.data?.timings;
    return picked ?? (timings ? { ...timings } : null);
  }, [picked, stats.data]);

  if (site.isPending) {
    return (
      <div className="grid gap-4" role="status" aria-label="Загрузка монитора">
        <Skeleton className="h-16" />
        <Skeleton className="h-24 rounded-lg" />
        <Skeleton className="h-[320px] rounded-lg" />
      </div>
    );
  }
  if (site.isError)
    return (
      <ErrorPanel
        error={site.error}
        onRetry={() => void site.refetch()}
        title="Монитор не загрузился"
      />
    );
  const data = site.data;
  const tone = siteTone(data);
  const isPassive = data.monitor_type === "heartbeat";
  const togglePause = () =>
    update.mutate(
      { paused: !data.paused },
      {
        onSuccess: () => toast(data.paused ? "Проверки возобновлены" : "Монитор на паузе"),
        onError: (error) => notifyError(error),
      },
    );

  return (
    <>
      <Link
        to="/"
        className="mb-3 inline-flex items-center gap-1.5 text-[13px] text-inkMuted hover:text-ink"
      >
        <ArrowLeft size={14} aria-hidden="true" />
        Журнал
      </Link>
      <header className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="page-title">{data.name}</h1>
            <StatusPill tone={tone} />
            {data.open_incident_id ? (
              <Link
                to={`/incidents/${data.open_incident_id}`}
                className="text-[13px] font-medium text-down underline"
              >
                Открыть инцидент
              </Link>
            ) : null}
          </div>
          <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-inkMuted">
            <span>{MONITOR_LABEL[data.monitor_type ?? "http"]}</span>
            {data.url && !isPassive ? (
              data.url.startsWith("http") ? (
                <a
                  href={data.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="inline-flex max-w-[420px] items-center gap-1 truncate hover:text-ink"
                >
                  <span className="truncate">{data.url}</span>
                  <ExternalLink size={12} aria-hidden="true" />
                </a>
              ) : (
                <span className="mono">{data.url}</span>
              )
            ) : null}
            <span>
              {isPassive
                ? `сигнал ${formatInterval(data.heartbeat_period_seconds ?? 3600)}`
                : formatInterval(data.interval_seconds)}
            </span>
            {data.last_checked_at ? (
              <span>
                проверено <Time value={data.last_checked_at} />
              </span>
            ) : null}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <CheckNowButton site={data} />
          <Button
            size="sm"
            loading={update.isPending}
            onClick={togglePause}
            icon={
              data.paused ? (
                <Play size={14} aria-hidden="true" />
              ) : (
                <Pause size={14} aria-hidden="true" />
              )
            }
          >
            {data.paused ? "Возобновить" : "Пауза"}
          </Button>
          <ButtonLink
            to={`/sites/${data.id}/edit`}
            size="sm"
            icon={<Pencil size={14} aria-hidden="true" />}
          >
            Изменить
          </ButtonLink>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => remove(data, "/")}
            aria-label="Удалить монитор"
            icon={<Trash2 size={14} aria-hidden="true" />}
          />
        </div>
      </header>
      {params.get("new") ? <FirstCheck site={data} /> : null}
      {data.pause_message ? (
        <div className="mb-5 rounded-lg border border-line bg-panelHover px-4 py-2.5 text-[13px] text-inkMuted">
          {data.pause_message}
        </div>
      ) : null}

      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <Segmented<StatsRange>
          label="Период"
          value={range}
          onChange={(value) =>
            setParams(
              (current) => {
                const next = new URLSearchParams(current);
                next.set("range", value);
                next.delete("new");
                return next;
              },
              { replace: true },
            )
          }
          options={(["24h", "7d", "30d", "90d"] as const).map((value) => ({
            value,
            label: RANGE_LABEL[value],
          }))}
        />
        {stats.data ? (
          <p className="text-xs text-inkMuted">
            {stats.data.source === "daily_rollups"
              ? "По суточным свёрткам"
              : `Шаг ${formatDuration(stats.data.resolution_seconds)}`}{" "}
            · {formatNumber(stats.data.checks)} проверок
          </p>
        ) : null}
      </div>

      {stats.isError ? (
        <ErrorPanel error={stats.error} onRetry={() => void stats.refetch()} />
      ) : null}
      <section className="panel mb-5" aria-label="Показатели за период">
        {stats.data ? (
          <>
            <dl className="mb-5 grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4 xl:grid-cols-7">
              <Metric
                label="Аптайм"
                value={formatPercent(stats.data.uptime, 3)}
                tone={uptimeText(stats.data.uptime)}
              />
              <Metric label="Средний" value={formatLatency(stats.data.avg_ms)} />
              <Metric label="p50" value={formatLatency(stats.data.p50_ms)} />
              <Metric label="p95" value={formatLatency(stats.data.p95_ms)} />
              <Metric label="p99" value={formatLatency(stats.data.p99_ms)} />
              <Metric
                label="Инциденты"
                value={formatNumber(stats.data.incidents)}
                tone={stats.data.incidents ? "text-down" : ""}
              />
              <Metric
                label="Простой"
                value={
                  stats.data.downtime_seconds ? formatDuration(stats.data.downtime_seconds) : "нет"
                }
              />
            </dl>
            {stats.data.points.length ? (
              <ResponseChart
                label={`Отклик «${data.name}» за ${RANGE_LABEL[range]}`}
                points={stats.data.points}
                incidents={stats.data.incident_spans}
                maintenance={stats.data.maintenance_spans}
                start={stats.data.start}
                end={stats.data.end}
                resolution={stats.data.resolution_seconds}
                baseline={stats.data.baseline_ms}
              />
            ) : (
              <EmptyState title="Нет данных за период">
                {isPassive
                  ? "График строится по длительности задач из сигналов."
                  : "Точки появятся после первых проверок."}
              </EmptyState>
            )}
            <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-2xs text-inkMuted">
              <span className="inline-flex items-center gap-1.5">
                <span className="h-0.5 w-4 bg-accent" aria-hidden="true" />
                средний
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="h-0.5 w-4 bg-accent/40" aria-hidden="true" />
                p95
              </span>
              {stats.data.baseline_ms ? (
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-0 w-4 border-t border-dashed border-info" aria-hidden="true" />
                  норма {formatLatency(stats.data.baseline_ms)}
                </span>
              ) : null}
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2.5 w-3 bg-down/30" aria-hidden="true" />
                инцидент
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2.5 w-3 bg-unknown/30" aria-hidden="true" />
                обслуживание
              </span>
              <span>Выделите участок мышью, чтобы увеличить</span>
            </p>
          </>
        ) : (
          <Skeleton className="h-[330px]" />
        )}
      </section>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="grid min-w-0 content-start gap-5">
          {!isPassive && data.monitor_type !== "dns" && data.monitor_type !== "ssl-only" ? (
            <section className="panel" aria-labelledby="waterfall-title">
              <div className="panel-head">
                <h2 id="waterfall-title" className="section-title">
                  Этапы запроса
                </h2>
                <span className="text-xs text-inkMuted">
                  {picked
                    ? `проверка ${formatDateTime(picked.checked_at)}`
                    : `среднее за ${RANGE_LABEL[range === "24h" ? "24h" : "7d"]}`}
                  {picked ? (
                    <button
                      type="button"
                      className="ml-2 underline"
                      onClick={() => setPicked(null)}
                    >
                      сбросить
                    </button>
                  ) : null}
                </span>
              </div>
              {lastPhases ? (
                <Waterfall phases={lastPhases} title="Этапы запроса" />
              ) : (
                <Skeleton className="h-16" />
              )}
              <p className="mt-3 text-xs text-inkMuted">
                Выберите проверку в таблице, чтобы увидеть её этапы.
              </p>
            </section>
          ) : null}
          <section className="panel" aria-labelledby="days-title">
            <div className="panel-head">
              <h2 id="days-title" className="section-title">
                Аптайм по дням
              </h2>
              <span className="text-xs text-inkMuted">90 дней · UTC</span>
            </div>
            {daily.data ? (
              <UptimeBar days={daily.data.days} label={data.name} height={36} showLegend />
            ) : (
              <Skeleton className="h-9" />
            )}
          </section>
          <ChecksTable id={id} range={range} onSelect={setPicked} selected={picked?.id ?? null} />
        </div>
        <div className="grid min-w-0 content-start gap-5">
          {isPassive ? (
            <HeartbeatCard site={data} token={token} />
          ) : (
            <SloCard slo={slo.data} site={data} loading={slo.isPending} />
          )}
          <RegionsCard site={data} />
          <CertificateCard site={data} />
          <DnsCard site={data} />
          <section className="panel" aria-labelledby="incidents-title">
            <div className="panel-head">
              <h2 id="incidents-title" className="section-title">
                Инциденты
              </h2>
              <Link to="/incidents" className="text-xs text-inkMuted hover:text-ink">
                все
              </Link>
            </div>
            {incidents.data?.length ? (
              <ul className="grid gap-1">
                {incidents.data.slice(0, 8).map((incident) => (
                  <li key={incident.id}>
                    <Link
                      to={`/incidents/${incident.id}`}
                      className="flex items-center gap-3 rounded px-2 py-2 text-[13px] hover:bg-panelHover"
                    >
                      <span
                        className={`h-2 w-2 shrink-0 rounded-full ${incident.is_open ? "bg-down" : incident.kind === "degraded" ? "bg-warn" : "bg-unknown"}`}
                        aria-hidden="true"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate">
                          {incident.kind === "degraded" ? "Медленный отклик" : incident.reason}
                        </span>
                        <span className="text-xs text-inkMuted">
                          {formatDateTime(incident.started_at)}
                        </span>
                      </span>
                      <span
                        className={`text-xs ${incident.is_open ? "text-down" : "text-inkMuted"}`}
                      >
                        {incident.is_open ? "идёт" : formatDuration(incident.duration_seconds)}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : incidents.isPending ? (
              <Skeleton className="h-20" />
            ) : (
              <p className="text-[13px] text-inkMuted">Инцидентов не было.</p>
            )}
          </section>
        </div>
      </div>
    </>
  );
}
