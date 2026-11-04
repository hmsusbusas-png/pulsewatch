import { useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Globe2, KeyRound, RefreshCw, ShieldCheck } from "lucide-react";
import { apiRequest } from "../../api/client";
import { patchSite, useRegions } from "../../api/queries";
import type { Site, Slo } from "../../api/types";
import {
  daysUntil,
  formatDate,
  formatDuration,
  formatLatency,
  formatNumber,
  formatPercent,
} from "../../lib/format";
import { Button, ButtonLink } from "../../ui/Button";
import { CopyField, Skeleton, StatusDot, Time } from "../../ui/Feedback";
import { Segmented } from "../../ui/Controls";
import { notifyError, toast } from "../../ui/toast-store";

export function SloCard({
  slo,
  site,
  loading,
}: {
  slo: Slo | undefined;
  site: Site;
  loading: boolean;
}) {
  if (loading || !slo) return <Skeleton className="h-[220px] rounded-lg" />;
  if (slo.target === null) {
    return (
      <section className="panel" aria-labelledby="slo-title">
        <h2 id="slo-title" className="section-title">
          SLO
        </h2>
        <p className="mt-2 text-[13px] text-inkMuted">
          Задайте цель аптайма, чтобы увидеть бюджет ошибок на 30 дней и скорость его расходования.
        </p>
        <ButtonLink className="mt-3" size="sm" to={`/sites/${site.id}/edit#slo`}>
          Задать цель
        </ButtonLink>
      </section>
    );
  }
  const spentShare = slo.budget_seconds ? Math.min(1, slo.spent_seconds / slo.budget_seconds) : 1;
  const burning = (slo.burn_rate_1h ?? 0) > 1 || (slo.burn_rate_24h ?? 0) > 1;
  return (
    <section className="panel" aria-labelledby="slo-title">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="slo-title" className="section-title">
            SLO {formatPercent(slo.target, 2)}
          </h2>
          <p className="mt-0.5 text-xs text-inkMuted">
            окно {slo.window_days} дней, без плановых работ
          </p>
        </div>
        <span
          className={`text-2xl font-semibold tracking-[-0.03em] ${slo.uptime !== null && slo.uptime < slo.target ? "text-down" : "text-accent"}`}
        >
          {formatPercent(slo.uptime, 3)}
        </span>
      </div>
      <div className="mt-4">
        <div className="mb-1.5 flex justify-between text-xs text-inkMuted">
          <span>Бюджет ошибок: {formatDuration(slo.budget_seconds)}</span>
          <span className={slo.exhausted ? "text-down" : ""}>
            {slo.exhausted ? "исчерпан" : `осталось ${formatPercent(slo.remaining_percent, 1)}`}
          </span>
        </div>
        <div
          className="h-2 overflow-hidden rounded-full bg-line"
          role="meter"
          aria-label="Израсходовано бюджета ошибок"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(spentShare * 100)}
        >
          <div
            className={`h-full origin-left ${slo.exhausted ? "bg-down" : spentShare > 0.75 ? "bg-warn" : "bg-accent"}`}
            style={{ transform: `scaleX(${spentShare})` }}
          />
        </div>
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-[13px]">
        <div>
          <dt className="text-xs text-inkMuted">Израсходовано</dt>
          <dd className="mt-0.5 font-medium">{formatDuration(slo.spent_seconds)}</dd>
        </div>
        <div>
          <dt className="text-xs text-inkMuted">Осталось</dt>
          <dd className={`mt-0.5 font-medium ${slo.remaining_seconds < 0 ? "text-down" : ""}`}>
            {slo.remaining_seconds < 0
              ? `−${formatDuration(-slo.remaining_seconds)}`
              : formatDuration(slo.remaining_seconds)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-inkMuted">Сжигание, 1 ч / 24 ч</dt>
          <dd className={`mt-0.5 font-medium ${burning ? "text-warn" : ""}`}>
            {formatNumber(slo.burn_rate_1h, 1)}× / {formatNumber(slo.burn_rate_24h, 1)}×
          </dd>
        </div>
        <div>
          <dt className="text-xs text-inkMuted">Прогноз</dt>
          <dd className="mt-0.5 font-medium">
            {slo.exhausted
              ? "бюджет исчерпан"
              : slo.exhausted_at
                ? `исчерпание ${formatDate(slo.exhausted_at)}`
                : "расход в норме"}
          </dd>
        </div>
      </dl>
      <p className="mt-3 text-xs text-inkMuted">
        Допустимый простой в месяц: {formatDuration(slo.allowed_downtime_month_seconds)}
      </p>
    </section>
  );
}

export function RegionsCard({ site }: { site: Site }) {
  const regions = useRegions();
  const states = site.region_states ?? [];
  if (!states.length) return null;
  const online = new Map((regions.data ?? []).map((region) => [region.code, region.online]));
  return (
    <section className="panel" aria-labelledby="regions-title">
      <h2 id="regions-title" className="section-title flex items-center gap-2">
        <Globe2 size={15} aria-hidden="true" />
        Регионы
      </h2>
      <p className="mt-0.5 text-xs text-inkMuted">Сбой подтверждается большинством регионов</p>
      <ul className="mt-3 grid gap-2">
        {states.map((state) => {
          const tone = state.status === "down" ? "down" : state.status === "up" ? "up" : "unknown";
          return (
            <li
              key={state.region}
              className="flex items-center gap-3 rounded border border-line px-3 py-2 text-[13px]"
            >
              <StatusDot tone={tone} />
              <span className="min-w-0 flex-1">
                <span className="font-medium">{state.name}</span>
                {online.get(state.region) === false ? (
                  <span className="ml-2 text-xs text-warn">агент не на связи</span>
                ) : null}
                {state.status === "down" && state.last_error ? (
                  <span className="block truncate text-xs text-down">{state.last_error}</span>
                ) : null}
              </span>
              <span className="text-right text-xs text-inkMuted">
                {formatLatency(state.last_response_ms)}
                <br />
                <Time value={state.last_checked_at} />
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function CertificateCard({ site }: { site: Site }) {
  const details = site.ssl_details as Record<string, unknown> | null | undefined;
  const sslLeft = daysUntil(site.ssl_expires_at);
  const domainLeft = daysUntil(site.domain_expires_at);
  if (!site.ssl_expires_at && !site.domain_expires_at) return null;
  const color = (left: number | null) =>
    left === null ? "" : left < 7 ? "text-down" : left < 30 ? "text-warn" : "text-accent";
  return (
    <section className="panel" aria-labelledby="cert-title">
      <h2 id="cert-title" className="section-title flex items-center gap-2">
        <ShieldCheck size={15} aria-hidden="true" />
        Сертификат и домен
      </h2>
      <dl className="mt-3 grid gap-2.5 text-[13px]">
        {site.ssl_expires_at ? (
          <div className="flex justify-between gap-3">
            <dt className="text-inkMuted">Сертификат до</dt>
            <dd className={`text-right font-medium ${color(sslLeft)}`}>
              {formatDate(site.ssl_expires_at)}
              <span className="block text-xs font-normal text-inkMuted">через {sslLeft} дн.</span>
            </dd>
          </div>
        ) : null}
        {details ? (
          <>
            <div className="flex justify-between gap-3">
              <dt className="text-inkMuted">Издатель</dt>
              <dd className="truncate text-right">{String(details.issuer ?? "н/д")}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="shrink-0 text-inkMuted">Протокол</dt>
              <dd className="mono text-right">{String(details.tls_version ?? "н/д")}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="shrink-0 text-inkMuted">Шифр</dt>
              <dd className="mono min-w-0 break-all text-right text-xs leading-5">
                {String(details.cipher ?? "н/д")}
              </dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-inkMuted">Цепочка</dt>
              <dd className={details.chain_complete ? "text-accent" : "text-down"}>
                {details.chain_complete ? "полная" : "неполная"}
                {details.trusted === false ? " · не доверена" : ""}
              </dd>
            </div>
          </>
        ) : null}
        {site.domain_expires_at ? (
          <div className="flex justify-between gap-3 border-t border-line pt-2.5">
            <dt className="text-inkMuted">Домен до</dt>
            <dd className={`text-right font-medium ${color(domainLeft)}`}>
              {formatDate(site.domain_expires_at)}
              <span className="block text-xs font-normal text-inkMuted">
                через {domainLeft} дн.
              </span>
            </dd>
          </div>
        ) : null}
      </dl>
      <p className="mt-3 text-xs text-inkMuted">
        Оповещения за {(site.expiry_thresholds ?? []).join(", ")} дн.
      </p>
    </section>
  );
}

type Example = "cron" | "curl" | "actions";

export function HeartbeatCard({ site, token }: { site: Site; token: string | null }) {
  const client = useQueryClient();
  const [fresh, setFresh] = useState<string | null>(token);
  const [busy, setBusy] = useState(false);
  const [example, setExample] = useState<Example>("cron");
  const base = `${window.location.origin}/api/heartbeat/`;
  const url = fresh ? `${base}${fresh}` : `${base}${site.heartbeat_prefix ?? "hb_"}…`;
  const period = site.heartbeat_period_seconds ?? 3600;
  const schedule =
    period >= 86400
      ? "0 3 * * *"
      : period >= 3600
        ? `0 */${Math.round(period / 3600)} * * *`
        : `*/${Math.max(1, Math.round(period / 60))} * * * *`;
  const snippets: Record<Example, string> = {
    cron: `${schedule} /opt/backup.sh && curl -fsS -m 10 --retry 3 "${url}" > /dev/null\n# при ошибке: curl -fsS "${url}?status=fail"`,
    curl: `start=$(date +%s%3N)\n./job.sh\ncode=$?\ncurl -fsS -X POST "${url}?duration=$(( $(date +%s%3N) - start ))$([ $code -ne 0 ] && echo '&status=fail')"`,
    actions: `- name: Сигнал Pulsewatch\n  if: always()\n  run: |\n    status=\${{ job.status == 'success' && 'ok' || 'fail' }}\n    curl -fsS -X POST "\${{ secrets.PULSEWATCH_HEARTBEAT_URL }}?status=$status"`,
  };
  const rotate = async () => {
    setBusy(true);
    try {
      const updated = await apiRequest<Site>(`/sites/${site.id}/heartbeat/rotate`, {
        method: "POST",
      });
      setFresh(updated.heartbeat_token ?? null);
      patchSite(client, site.id, () => ({ ...updated, heartbeat_token: null }));
      toast("Адрес перевыпущен, старый больше не принимает сигналы");
    } catch (error) {
      notifyError(error);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel" aria-labelledby="hb-title">
      <div className="panel-head">
        <h2 id="hb-title" className="section-title flex items-center gap-2">
          <KeyRound size={15} aria-hidden="true" />
          Адрес пинга
        </h2>
        <Button
          size="sm"
          variant="ghost"
          loading={busy}
          onClick={() => void rotate()}
          icon={<RefreshCw size={14} aria-hidden="true" />}
        >
          Перевыпустить
        </Button>
      </div>
      {fresh ? (
        <>
          <CopyField label="Сохраните адрес. Он показывается один раз" value={url} />
          <div className="mt-4">
            <Segmented<Example>
              label="Пример"
              size="sm"
              value={example}
              onChange={setExample}
              options={[
                { value: "cron", label: "cron" },
                { value: "curl", label: "curl" },
                { value: "actions", label: "GitHub Actions" },
              ]}
            />
            <pre className="mono mt-2 overflow-x-auto rounded border border-line bg-sidebar p-3 leading-relaxed">
              {snippets[example]}
            </pre>
          </div>
        </>
      ) : (
        <p className="text-[13px] text-inkMuted">
          Адрес <code className="mono text-ink">{url}</code> скрыт: полный токен показывается только
          при создании. Если адрес потерян, перевыпустите его.
        </p>
      )}
      <dl className="mt-4 grid grid-cols-2 gap-3 text-[13px]">
        <div>
          <dt className="text-xs text-inkMuted">Ожидаемый период</dt>
          <dd className="mt-0.5">{formatDuration(period)}</dd>
        </div>
        <div>
          <dt className="text-xs text-inkMuted">Последний сигнал</dt>
          <dd className="mt-0.5">
            <Time value={site.last_ping_at ?? null} />
          </dd>
        </div>
      </dl>
      <p className="mt-3 text-xs text-inkMuted">
        Параметры: <code className="mono">status=fail</code>: сбой сразу,{" "}
        <code className="mono">duration</code>: длительность в мс.{" "}
        <Link to="/api-docs#heartbeat" className="underline">
          Справка
        </Link>
      </p>
    </section>
  );
}

export function DnsCard({ site }: { site: Site }) {
  if (site.monitor_type !== "dns") return null;
  return (
    <section className="panel" aria-labelledby="dns-title">
      <h2 id="dns-title" className="section-title">
        Записи {site.dns_record_type}
      </h2>
      {site.dns_values?.length ? (
        <ul className="mt-3 grid gap-1">
          {site.dns_values.map((value) => (
            <li
              key={value}
              className="mono truncate rounded bg-sidebar px-2.5 py-1.5"
              title={value}
            >
              {value}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-[13px] text-inkMuted">Записи ещё не получены.</p>
      )}
      {site.dns_expected ? (
        <p className="mt-3 text-xs text-inkMuted">
          Ожидается: <span className="mono text-ink">{site.dns_expected}</span>
        </p>
      ) : null}
      <p className="mt-1 text-xs text-inkMuted">
        Изменение набора записей приходит отдельным уведомлением.
      </p>
    </section>
  );
}
