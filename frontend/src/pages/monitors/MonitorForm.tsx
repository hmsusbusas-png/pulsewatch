import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Clock3,
  Cloud,
  FileSearch,
  Globe,
  HeartPulse,
  Network,
  ShieldCheck,
} from "lucide-react";
import { apiRequest } from "../../api/client";
import { errorMessage } from "../../api/errors";
import {
  invalidate,
  keys,
  useChannels,
  useRegions,
  useSite,
  useSubscription,
} from "../../api/queries";
import type { MonitorType, Site } from "../../api/types";
import { formatDuration, formatInterval, hostOf } from "../../lib/format";
import { idempotencyKey } from "../../lib/ids";
import { Button, ButtonLink } from "../../ui/Button";
import { Checkbox, RadioCards, Segmented, Switch } from "../../ui/Controls";
import { ErrorPanel, PlanLock, Skeleton } from "../../ui/Feedback";
import { FormError, TextArea, TextField } from "../../ui/Field";
import { Select } from "../../ui/Select";
import { toast } from "../../ui/toast-store";
import { suggestName } from "./actions";
import { AssertionsEditor, HeadersEditor, MaintenanceEditor, ThresholdsEditor } from "./FormParts";
import { draftFrom, EMPTY, parseCodes, payloadFrom } from "./form-model";
import type { Draft } from "./form-model";

const INTERVALS = [30, 60, 120, 300, 600, 900, 1800, 3600];
const PERIODS = [300, 900, 1800, 3600, 21600, 43200, 86400, 604800];
const SLO_TARGETS = ["99", "99.5", "99.9", "99.95", "99.99"];

function Section({
  id,
  title,
  description,
  children,
}: {
  id: string;
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="panel scroll-mt-20">
      <h2 id={`${id}-title`} className="section-title">
        {title}
      </h2>
      {description ? <p className="mt-0.5 text-[13px] text-inkMuted">{description}</p> : null}
      <div className="mt-4 grid gap-4">{children}</div>
    </section>
  );
}

function Preview({ draft, channels }: { draft: Draft; channels: { id: number; name: string }[] }) {
  const type = draft.monitor_type;
  const codes = parseCodes(draft.codes);
  const target =
    type === "heartbeat" ? "адрес пинга появится после сохранения" : draft.url || "адрес не указан";
  const lines: string[] = [];
  if (type === "heartbeat") {
    lines.push(
      `Ждём сигнал раз в ${formatDuration(draft.heartbeat_period_seconds)}, допуск ${formatDuration(draft.heartbeat_grace_seconds)}`,
    );
    lines.push("Нет сигнала или status=fail: сразу инцидент");
  } else {
    lines.push(
      `${type === "dns" ? `DNS ${draft.dns_record_type}` : type === "tcp" ? "TCP-соединение" : type === "ssl-only" ? "Проверка сертификата" : draft.method} ${formatInterval(draft.interval_seconds)}, таймаут ${draft.timeout_seconds} с`,
    );
    if (type === "http" || type === "keyword")
      lines.push(`Успех: код ${codes ? draft.codes : "н/д"}`);
    if (type === "keyword" && draft.keyword)
      lines.push(`${draft.keyword_present ? "Есть" : "Нет"} «${draft.keyword}» в ответе`);
    for (const item of draft.assertions)
      lines.push(`${item.path || item.source} ${item.op} ${item.value ?? ""}`.trim());
    if (type === "tcp" && draft.tcp_expect) lines.push(`Ответ содержит «${draft.tcp_expect}»`);
    if (type === "dns" && draft.dns_expected)
      lines.push(`Среди записей есть «${draft.dns_expected}»`);
    lines.push(
      `Сбой подтверждается ${draft.failure_threshold} ${draft.failure_threshold === 1 ? "проверкой" : "проверками"}${draft.regions.length > 1 ? `, большинством из ${draft.regions.length} регионов` : ""}`,
    );
  }
  const targetChannels = channels
    .filter((channel) => draft.channel_ids.includes(channel.id))
    .map((channel) => channel.name);
  lines.push(
    targetChannels.length ? `Уведомить: ${targetChannels.join(", ")}` : "Уведомления не настроены",
  );
  if (draft.escalation_minutes && draft.escalation_channel) {
    const name = channels.find((channel) => String(channel.id) === draft.escalation_channel)?.name;
    lines.push(`Через ${draft.escalation_minutes} мин без реакции: ${name}`);
  }
  if (draft.slo_target)
    lines.push(
      `SLO ${draft.slo_target.replace(".", ",")} %: до ${formatDuration(Math.round(30 * 86400 * (1 - Number(draft.slo_target) / 100)))} простоя в месяц`,
    );
  if (draft.maintenance.length) lines.push(`Окон обслуживания: ${draft.maintenance.length}`);
  return (
    <aside aria-label="Предпросмотр" className="panel sticky top-20">
      <p className="eyebrow">Предпросмотр</p>
      <p className="mt-2 truncate text-[15px] font-semibold">
        {draft.name || (draft.url ? suggestName(draft.url) : "Новый монитор")}
      </p>
      <p className="mt-0.5 truncate text-xs text-inkMuted">
        {type === "heartbeat" ? target : hostOf(target)}
      </p>
      <ul className="mt-4 grid gap-2 text-[13px]">
        {lines.map((line) => (
          <li key={line} className="flex gap-2">
            <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-accent" aria-hidden="true" />
            <span className="min-w-0">{line}</span>
          </li>
        ))}
      </ul>
    </aside>
  );
}

export function MonitorForm({ mode }: { mode: "create" | "edit" }) {
  const id = Number(useParams().id ?? 0);
  const navigate = useNavigate();
  const client = useQueryClient();
  const site = useSite(mode === "edit" ? id : 0);
  const subscription = useSubscription();
  const channels = useChannels();
  const regions = useRegions();
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [loaded, setLoaded] = useState(mode === "create");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [key] = useState(idempotencyKey);
  const plan = subscription.data?.plan;
  const has = (feature: string) => Boolean(plan?.features[feature]);
  const set = <K extends keyof Draft>(field: K, value: Draft[K]) =>
    setDraft((current) => ({ ...current, [field]: value }));

  useEffect(() => {
    if (mode === "edit" && site.data && !loaded) {
      setDraft(draftFrom(site.data));
      setLoaded(true);
    }
  }, [mode, site.data, loaded]);
  useEffect(() => {
    if (mode === "create" && plan && draft.interval_seconds < plan.min_interval_sec)
      set("interval_seconds", plan.min_interval_sec);
  }, [mode, plan, draft.interval_seconds]);
  useEffect(() => {
    if (mode === "create" && channels.data && !draft.channel_ids.length)
      set(
        "channel_ids",
        channels.data
          .filter((channel) => channel.verified_at && channel.enabled)
          .map((channel) => channel.id),
      );
  }, [mode, channels.data, draft.channel_ids.length]);
  useEffect(() => {
    document.title = `${mode === "create" ? "Новый монитор" : "Изменить монитор"}. pulsewatch`;
  }, [mode]);

  const typeOptions = [
    {
      value: "http" as MonitorType,
      label: "HTTP(S)",
      description: "Код ответа, условия по JSON и заголовкам",
      icon: <Globe size={15} aria-hidden="true" />,
    },
    {
      value: "keyword" as MonitorType,
      label: "Ключевое слово",
      description: "Строка есть или отсутствует в ответе",
      icon: <FileSearch size={15} aria-hidden="true" />,
      disabled: !has("keyword"),
    },
    {
      value: "tcp" as MonitorType,
      label: "TCP-порт",
      description: "Соединение и обмен строками",
      icon: <Network size={15} aria-hidden="true" />,
    },
    {
      value: "ssl-only" as MonitorType,
      label: "Сертификат",
      description: "Срок, цепочка, версия TLS",
      icon: <ShieldCheck size={15} aria-hidden="true" />,
    },
    {
      value: "dns" as MonitorType,
      label: "DNS",
      description: "A, AAAA, CNAME, MX, TXT, NS",
      icon: <Cloud size={15} aria-hidden="true" />,
      disabled: !has("dns"),
    },
    {
      value: "heartbeat" as MonitorType,
      label: "Heartbeat",
      description: "Сигналы от cron и фоновых задач",
      icon: <HeartPulse size={15} aria-hidden="true" />,
      disabled: !has("heartbeat"),
    },
  ];

  if (mode === "edit" && site.isError)
    return <ErrorPanel error={site.error} onRetry={() => void site.refetch()} />;
  if (!loaded || !subscription.data) {
    return (
      <div className="grid gap-4" role="status" aria-label="Загрузка формы">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-64 rounded-lg" />
      </div>
    );
  }

  const type = draft.monitor_type;
  const http = type === "http" || type === "keyword";
  const codesValid = !http || parseCodes(draft.codes) !== null;
  const minInterval = plan?.min_interval_sec ?? 300;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (type !== "heartbeat" && !draft.url.trim()) {
      setError("Укажите адрес");
      return;
    }
    if (!codesValid) {
      setError("Коды ответа: например 200-299, 301, 302");
      return;
    }
    setBusy(true);
    setError(null);
    const payload = payloadFrom({
      ...draft,
      name: draft.name.trim() || suggestName(draft.url) || "Heartbeat",
    });
    try {
      let saved: Site;
      if (mode === "create") {
        saved = await apiRequest<Site>("/sites", {
          method: "POST",
          body: payload,
          idempotencyKey: key,
        });
      } else {
        const patch: Partial<typeof payload> = { ...payload };
        delete patch.paused;
        delete patch.show_url_publicly;
        if (type === "heartbeat") {
          delete patch.monitor_type;
          delete patch.url;
        }
        saved = await apiRequest<Site>(`/sites/${id}`, { method: "PATCH", body: patch });
      }
      if (draft.escalation_minutes && draft.escalation_channel) {
        await apiRequest("/escalations", {
          method: "POST",
          body: {
            after_minutes: Number(draft.escalation_minutes),
            channel_id: Number(draft.escalation_channel),
            site_id: saved.id,
          },
        });
        invalidate(client, keys.escalations);
      }
      invalidate(client, keys.sites, keys.dashboard, keys.subscription, keys.site(saved.id));
      toast(mode === "create" ? "Монитор создан" : "Изменения сохранены");
      navigate(`/sites/${saved.id}${mode === "create" ? "?new=1" : ""}`, {
        state: saved.heartbeat_token ? { heartbeatToken: saved.heartbeat_token } : undefined,
      });
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };

  const channelList = channels.data ?? [];
  return (
    <form onSubmit={(event) => void submit(event)} noValidate>
      <Link
        to={mode === "edit" ? `/sites/${id}` : "/"}
        className="mb-3 inline-flex items-center gap-1.5 text-[13px] text-inkMuted hover:text-ink"
      >
        <ArrowLeft size={14} aria-hidden="true" />
        {mode === "edit" ? "К монитору" : "Журнал"}
      </Link>
      <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <h1 className="page-title">
          {mode === "create" ? "Новый монитор" : `Изменить «${site.data?.name}»`}
        </h1>
        <nav aria-label="Разделы формы" className="hidden gap-1 text-[13px] lg:flex">
          {[
            ["type", "Тип"],
            ["target", "Адрес"],
            ["schedule", "Расписание"],
            ["rules", "Условия"],
            ["alerts", "Уведомления"],
            ["extra", "Дополнительно"],
          ].map(([anchor, label]) => (
            <a
              key={anchor}
              href={`#${anchor}`}
              className="rounded px-2 py-1 text-inkMuted hover:bg-panelHover hover:text-ink"
            >
              {label}
            </a>
          ))}
        </nav>
      </header>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="grid min-w-0 gap-5">
          <Section id="type" title="Тип проверки">
            {mode === "edit" && type === "heartbeat" ? (
              <p className="text-[13px] text-inkMuted">
                Heartbeat-монитор нельзя перевести в другой тип.
              </p>
            ) : (
              <RadioCards<MonitorType>
                label="Тип проверки"
                columns="sm:grid-cols-2 xl:grid-cols-3"
                value={type}
                onChange={(value) => set("monitor_type", value)}
                options={typeOptions.filter(
                  (option) => mode === "create" || option.value !== "heartbeat",
                )}
              />
            )}
            {typeOptions.some((option) => option.disabled) ? (
              <p className="text-xs text-inkMuted">
                Часть типов доступна на старших тарифах.{" "}
                <Link to="/pricing" className="underline">
                  сравнить
                </Link>
                .
              </p>
            ) : null}
          </Section>

          <Section id="target" title="Адрес">
            <div className="grid gap-4 sm:grid-cols-2">
              {type !== "heartbeat" ? (
                <TextField
                  label={type === "tcp" ? "Хост и порт" : type === "dns" ? "Доменное имя" : "URL"}
                  placeholder={
                    type === "tcp"
                      ? "db.example.com:5433"
                      : type === "dns"
                        ? "example.com"
                        : "https://api.example.com/health"
                  }
                  value={draft.url}
                  onChange={(event) => set("url", event.target.value)}
                  inputMode="url"
                  required
                  hint={
                    type === "tcp"
                      ? "Служебные порты (22, 25, 5432, 6379…) запрещены"
                      : type === "dns"
                        ? "Без схемы и пути"
                        : "Внутренние и служебные адреса блокируются"
                  }
                />
              ) : null}
              <TextField
                label="Название"
                placeholder={draft.url ? suggestName(draft.url) : "Например, Ночной бэкап"}
                value={draft.name}
                maxLength={100}
                onChange={(event) => set("name", event.target.value)}
              />
            </div>
          </Section>

          <Section id="schedule" title="Расписание">
            {type === "heartbeat" ? (
              <div className="grid gap-4 sm:grid-cols-2">
                <Select
                  label="Задача сообщает о себе"
                  value={String(draft.heartbeat_period_seconds)}
                  onChange={(value) => set("heartbeat_period_seconds", Number(value))}
                  options={PERIODS.map((value) => ({
                    value: String(value),
                    label: `раз в ${formatDuration(value)}`,
                  }))}
                />
                <Select
                  label="Допуск опоздания"
                  value={String(draft.heartbeat_grace_seconds)}
                  onChange={(value) => set("heartbeat_grace_seconds", Number(value))}
                  options={[0, 60, 300, 900, 1800, 3600, 7200].map((value) => ({
                    value: String(value),
                    label: value ? formatDuration(value) : "без допуска",
                  }))}
                />
              </div>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2">
                <Select
                  label="Как часто проверять"
                  value={String(draft.interval_seconds)}
                  onChange={(value) => set("interval_seconds", Number(value))}
                  hint={`Минимум на тарифе ${plan?.name}: ${formatInterval(minInterval).replace("каждые ", "").replace("каждую ", "")}`}
                  options={INTERVALS.map((value) => ({
                    value: String(value),
                    label: formatInterval(value),
                    disabled: value < minInterval,
                  }))}
                />
                <Select
                  label="Таймаут"
                  value={String(draft.timeout_seconds)}
                  onChange={(value) => set("timeout_seconds", Number(value))}
                  options={[3, 5, 10, 15, 20, 30].map((value) => ({
                    value: String(value),
                    label: `${value} с`,
                  }))}
                />
              </div>
            )}
          </Section>

          {type !== "heartbeat" && type !== "ssl-only" ? (
            <Section id="rules" title="Условия успеха">
              {http ? (
                <>
                  <div className="grid gap-4 sm:grid-cols-3">
                    <Segmented<"GET" | "HEAD" | "POST">
                      label="Метод"
                      value={draft.method}
                      onChange={(value) => set("method", value)}
                      options={[
                        { value: "GET", label: "GET" },
                        { value: "HEAD", label: "HEAD" },
                        { value: "POST", label: "POST", disabled: !has("post") },
                      ]}
                    />
                    <TextField
                      label="Коды ответа"
                      value={draft.codes}
                      onChange={(event) => set("codes", event.target.value)}
                      error={codesValid ? null : "Например: 200-299, 301"}
                    />
                    <Switch
                      label="Следовать редиректам"
                      checked={draft.follow_redirects}
                      onChange={(value) => set("follow_redirects", value)}
                    />
                  </div>
                  {type === "keyword" ? (
                    <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_200px]">
                      <TextField
                        label="Ключевое слово"
                        value={draft.keyword}
                        onChange={(event) => set("keyword", event.target.value)}
                      />
                      <Select
                        label="Условие"
                        value={draft.keyword_present ? "yes" : "no"}
                        onChange={(value) => set("keyword_present", value === "yes")}
                        options={[
                          { value: "yes", label: "должно быть" },
                          { value: "no", label: "не должно быть" },
                        ]}
                      />
                    </div>
                  ) : null}
                  <div>
                    <p className="label">Условия по ответу</p>
                    {has("assertions") ? (
                      <AssertionsEditor
                        value={draft.assertions}
                        onChange={(value) => set("assertions", value)}
                        siteId={mode === "edit" ? id : null}
                      />
                    ) : (
                      <PlanLock feature="Условия по JSON и заголовкам" plan="Pro" />
                    )}
                  </div>
                  <div>
                    <p className="label">Заголовки запроса</p>
                    {has("headers") ? (
                      <HeadersEditor
                        value={draft.headers}
                        onChange={(value) => set("headers", value)}
                      />
                    ) : (
                      <PlanLock feature="Свои заголовки" plan="Pro" />
                    )}
                  </div>
                  {draft.method === "POST" ? (
                    <TextArea
                      label="Тело запроса"
                      value={draft.body}
                      onChange={(event) => set("body", event.target.value)}
                      hint="Секреты передавайте в заголовках. Тело хранится как данные проверки."
                    />
                  ) : null}
                </>
              ) : null}
              {type === "tcp" ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  <TextField
                    label="Отправить строку"
                    placeholder="PING\r\n"
                    value={draft.tcp_send}
                    onChange={(event) => set("tcp_send", event.target.value)}
                    hint="\r\n заменяются на перевод строки"
                  />
                  <TextField
                    label="Ожидать в ответе"
                    placeholder="+PONG"
                    value={draft.tcp_expect}
                    onChange={(event) => set("tcp_expect", event.target.value)}
                  />
                </div>
              ) : null}
              {type === "dns" ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  <Select
                    label="Тип записи"
                    value={draft.dns_record_type}
                    onChange={(value) => set("dns_record_type", value)}
                    options={(["A", "AAAA", "CNAME", "MX", "TXT", "NS"] as const).map((value) => ({
                      value,
                      label: value,
                    }))}
                  />
                  <TextField
                    label="Ожидаемое значение"
                    placeholder="93.184.216.34"
                    value={draft.dns_expected}
                    onChange={(event) => set("dns_expected", event.target.value)}
                    hint="Необязательно. Изменение набора записей придёт уведомлением."
                  />
                </div>
              ) : null}
            </Section>
          ) : null}

          <Section
            id="alerts"
            title="Уведомления"
            description="Каналы получают сбой, восстановление и деградацию отклика."
          >
            {channelList.length ? (
              <div className="grid gap-0.5 sm:grid-cols-2">
                {channelList.map((channel) => (
                  <Checkbox
                    key={channel.id}
                    checked={draft.channel_ids.includes(channel.id)}
                    onChange={(next) =>
                      set(
                        "channel_ids",
                        next
                          ? [...draft.channel_ids, channel.id]
                          : draft.channel_ids.filter((item) => item !== channel.id),
                      )
                    }
                    label={channel.name}
                    description={`${channel.type}${channel.verified_at ? "" : " · не подтверждён"}`}
                  />
                ))}
              </div>
            ) : (
              <p className="text-[13px] text-inkMuted">
                Каналов пока нет.{" "}
                <Link to="/channels" className="text-ink underline">
                  Подключите Telegram, почту или Slack
                </Link>
                .
              </p>
            )}
            {type !== "heartbeat" ? (
              <div>
                <p className="label">Сбой подтверждается после</p>
                <Segmented<string>
                  label="Сколько неудачных проверок подряд"
                  value={String(draft.failure_threshold)}
                  onChange={(value) => set("failure_threshold", Number(value))}
                  options={["1", "2", "3", "4", "5"].map((value) => ({ value, label: `${value}` }))}
                />
                <p className="hint">неудачных проверок подряд для подтверждения сбоя</p>
              </div>
            ) : null}
            <div className="grid gap-4 sm:grid-cols-2">
              <Select
                label="Повторять, пока не работает"
                value={String(draft.cooldown_seconds)}
                onChange={(value) => set("cooldown_seconds", Number(value))}
                options={[600, 1800, 3600, 10800, 86400].map((value) => ({
                  value: String(value),
                  label: `раз в ${formatDuration(value)}`,
                }))}
              />
            </div>
            <div className="rounded border border-line p-3">
              <p className="text-[13px] font-medium">Эскалация</p>
              {has("escalations") ? (
                <div className="mt-2 grid items-end gap-3 sm:grid-cols-[160px_minmax(0,1fr)]">
                  <Select
                    label="Если не закрыт"
                    value={draft.escalation_minutes}
                    onChange={(value) => set("escalation_minutes", value)}
                    options={[
                      { value: "", label: "не эскалировать" },
                      ...[5, 15, 30, 60, 120].map((value) => ({
                        value: String(value),
                        label: `${value} мин`,
                      })),
                    ]}
                  />
                  <Select
                    label="уведомить канал"
                    value={draft.escalation_channel}
                    disabled={!draft.escalation_minutes}
                    onChange={(value) => set("escalation_channel", value)}
                    options={[
                      { value: "", label: "Выберите канал" },
                      ...channelList.map((channel) => ({
                        value: String(channel.id),
                        label: channel.name,
                      })),
                    ]}
                  />
                </div>
              ) : (
                <div className="mt-2">
                  <PlanLock feature="Эскалация на дополнительный канал" plan="Pro" />
                </div>
              )}
              <p className="hint">
                Правило срабатывает, пока инцидент не взят в работу. Все правила находятся в разделе{" "}
                <Link to="/channels#escalations" className="underline">
                  Каналы
                </Link>
                .
              </p>
            </div>
          </Section>

          <Section id="extra" title="Дополнительно">
            {type !== "heartbeat" ? (
              <div>
                <p className="label">Регионы проверки</p>
                {has("regions") ? (
                  <div className="grid gap-0.5 sm:grid-cols-3">
                    {(regions.data ?? []).map((region) => (
                      <Checkbox
                        key={region.code}
                        checked={region.is_primary || draft.regions.includes(region.code)}
                        disabled={region.is_primary}
                        onChange={(next) => {
                          const others = draft.regions.filter((code) => code !== region.code);
                          const primary = (regions.data ?? []).find(
                            (item) => item.is_primary,
                          )?.code;
                          const list = next ? [...others, region.code] : others;
                          set(
                            "regions",
                            list.length && primary && !list.includes(primary)
                              ? [primary, ...list]
                              : list,
                          );
                        }}
                        label={region.name}
                        description={`${region.code}${region.online ? "" : " · агент не на связи"}`}
                      />
                    ))}
                  </div>
                ) : (
                  <PlanLock feature="Проверка из нескольких регионов" />
                )}
                <p className="hint">
                  При двух и более регионах инцидент открывается, только если сбой видит
                  большинство.
                </p>
              </div>
            ) : null}
            <div id="slo" className="grid gap-4 sm:grid-cols-2">
              {has("slo") ? (
                <Select
                  label="Цель SLO"
                  value={draft.slo_target}
                  onChange={(value) => set("slo_target", value)}
                  hint={
                    draft.slo_target
                      ? `Допустимый простой: ${formatDuration(Math.round(30 * 86400 * (1 - Number(draft.slo_target) / 100)))} в месяц`
                      : "Без цели бюджет ошибок не считается"
                  }
                  options={[
                    { value: "", label: "Не задана" },
                    ...SLO_TARGETS.map((value) => ({
                      value,
                      label: `${value.replace(".", ",")} %`,
                      hint: `${formatDuration(Math.round(30 * 86400 * (1 - Number(value) / 100)))} в месяц`,
                    })),
                  ]}
                />
              ) : (
                <PlanLock feature="SLO и бюджет ошибок" plan="Pro" />
              )}
              <TextField
                label="Теги"
                placeholder="prod, api"
                value={draft.tags}
                onChange={(event) => set("tags", event.target.value)}
              />
              <TextField
                label="Группа"
                placeholder="Ядро"
                value={draft.group}
                maxLength={100}
                onChange={(event) => set("group", event.target.value)}
              />
            </div>
            {type === "http" || type === "keyword" || type === "ssl-only" ? (
              <div>
                <p className="label">Предупреждать об истечении сертификата и домена за</p>
                <ThresholdsEditor
                  value={draft.expiry_thresholds}
                  onChange={(value) => set("expiry_thresholds", value)}
                />
                <div className="mt-2">
                  {has("domain_expiry") ? (
                    <Switch
                      label="Следить за сроком регистрации домена (RDAP)"
                      checked={draft.check_domain}
                      onChange={(value) => set("check_domain", value)}
                    />
                  ) : null}
                </div>
              </div>
            ) : null}
            <div>
              <p className="label flex items-center gap-1.5">
                <Clock3 size={14} aria-hidden="true" />
                Окна обслуживания
              </p>
              {has("maintenance") ? (
                <MaintenanceEditor
                  value={draft.maintenance}
                  onChange={(value) => set("maintenance", value)}
                />
              ) : (
                <PlanLock feature="Окна обслуживания" />
              )}
              <p className="hint">В окне проверки не выполняются, время исключается из SLA.</p>
            </div>
            <Switch
              label="Публичная страница этого монитора"
              description="Отдельная ссылка со статусом и аптаймом за 30 дней"
              checked={draft.is_public}
              onChange={(value) => set("is_public", value)}
            />
          </Section>
          <FormError message={error} />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="primary" loading={busy}>
              {mode === "create" ? "Создать монитор" : "Сохранить"}
            </Button>
            <ButtonLink to={mode === "edit" ? `/sites/${id}` : "/"}>Отмена</ButtonLink>
          </div>
        </div>
        <div className="hidden lg:block">
          <Preview draft={draft} channels={channelList} />
        </div>
      </div>
    </form>
  );
}
