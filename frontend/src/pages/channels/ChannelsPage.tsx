import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Bell, Mail, MessageCircle, Plus, Send, Trash2, Webhook } from "lucide-react";
import { apiRequest } from "../../api/client";
import { errorMessage } from "../../api/errors";
import {
  invalidate,
  keys,
  useChannels,
  useEscalations,
  useSites,
  useSubscription,
  useTelegram,
  useTemplates,
} from "../../api/queries";
import type { Channel, Template, TelegramLink } from "../../api/types";
import { formatDateTime } from "../../lib/format";
import { Button } from "../../ui/Button";
import { Switch } from "../../ui/Controls";
import { EmptyState, ErrorPanel, PlanLock, Skeleton } from "../../ui/Feedback";
import { TextArea } from "../../ui/Field";
import { Menu } from "../../ui/Menu";
import { Select } from "../../ui/Select";
import { deferRemoval, notifyError, toast } from "../../ui/toast-store";
import { ChannelDialog, DeliveriesDialog } from "./ChannelDialogs";
import { KIND_INFO } from "./kinds";
import type { Kind } from "./kinds";

const ICONS: Record<string, typeof Bell> = {
  email: Mail,
  telegram: Send,
  webhook: Webhook,
  slack: MessageCircle,
  discord: MessageCircle,
  teams: MessageCircle,
  ntfy: Bell,
};

function TelegramPanel() {
  const status = useTelegram();
  const client = useQueryClient();
  const [link, setLink] = useState<TelegramLink | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!link?.url) return;
    let alive = true;
    void import("qrcode").then((module) =>
      module
        .toDataURL(link.url ?? "", {
          margin: 1,
          width: 168,
          color: { dark: "#f4f4f5", light: "#00000000" },
        })
        .then((value) => {
          if (alive) setQr(value);
        }),
    );
    return () => {
      alive = false;
    };
  }, [link]);
  if (status.isPending) return <Skeleton className="h-28 rounded-lg" />;
  if (status.isError)
    return <ErrorPanel error={status.error} onRetry={() => void status.refetch()} />;
  const data = status.data;
  return (
    <section className="panel" aria-labelledby="tg-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 id="tg-title" className="section-title flex items-center gap-2">
            <Send size={15} aria-hidden="true" />
            Telegram
          </h2>
          <p className="mt-1 text-[13px] text-inkMuted">
            {data.linked
              ? `Подключён${data.name ? ` к ${data.name}` : ""}. Бот присылает сбои и понимает /status, /pause, /mute.`
              : data.enabled
                ? "Привяжите аккаунт: откройте ссылку или отсканируйте код и нажмите Start в боте."
                : "Бот не настроен на сервере: задайте BOT_TOKEN, чтобы включить Telegram."}
          </p>
        </div>
        {data.linked ? (
          <Button
            size="sm"
            variant="ghost"
            loading={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await apiRequest("/telegram/link", { method: "DELETE" });
                invalidate(client, keys.telegram, keys.channels);
                toast("Telegram отвязан");
              } catch (error) {
                notifyError(error);
              } finally {
                setBusy(false);
              }
            }}
          >
            Отвязать
          </Button>
        ) : data.enabled ? (
          <Button
            size="sm"
            variant="primary"
            loading={busy}
            onClick={async () => {
              setBusy(true);
              try {
                setLink(await apiRequest<TelegramLink>("/telegram/link", { method: "POST" }));
              } catch (error) {
                notifyError(error);
              } finally {
                setBusy(false);
              }
            }}
          >
            Получить ссылку
          </Button>
        ) : null}
      </div>
      {link && !data.linked ? (
        <div className="mt-4 flex flex-wrap items-center gap-4 rounded border border-line bg-sidebar p-3">
          {qr ? (
            <img
              src={qr}
              width={120}
              height={120}
              alt="QR-код для привязки бота"
              className="rounded"
            />
          ) : (
            <Skeleton className="h-[120px] w-[120px]" />
          )}
          <div className="min-w-0 flex-1 text-[13px]">
            {link.url ? (
              <a
                href={link.url}
                target="_blank"
                rel="noreferrer noopener"
                className="font-medium text-accent underline"
              >
                Открыть @{link.bot_username}
              </a>
            ) : null}
            <p className="mt-1 text-inkMuted">{link.instructions}</p>
            <Button className="mt-2" size="sm" onClick={() => void status.refetch()}>
              Я нажал Start
            </Button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function ChannelRow({
  channel,
  onEdit,
  onLogs,
}: {
  channel: Channel;
  onEdit: () => void;
  onLogs: () => void;
}) {
  const client = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const Icon = ICONS[channel.type] ?? Bell;
  const toggle = async (enabled: boolean) => {
    const before = client.getQueryData<Channel[]>(keys.channels);
    client.setQueryData<Channel[]>(keys.channels, (list) =>
      list?.map((item) => (item.id === channel.id ? { ...item, enabled } : item)),
    );
    try {
      await apiRequest(`/channels/${channel.id}`, { method: "PATCH", body: { enabled } });
    } catch (error) {
      client.setQueryData(keys.channels, before);
      toast(`Не удалось ${enabled ? "включить" : "выключить"} канал: ${errorMessage(error)}`, {
        tone: "error",
      });
    }
  };
  const run = async (name: string, path: string, done: string) => {
    setBusy(name);
    try {
      await apiRequest(path, { method: "POST" });
      toast(done);
    } catch (error) {
      notifyError(error);
    } finally {
      setBusy(null);
    }
  };
  const remove = () => {
    const before = client.getQueryData<Channel[]>(keys.channels);
    deferRemoval({
      message: `Канал «${channel.name}» удалён`,
      hide: () =>
        client.setQueryData<Channel[]>(keys.channels, (list) =>
          list?.filter((item) => item.id !== channel.id),
        ),
      restore: () => client.setQueryData(keys.channels, before),
      commit: async () => {
        await apiRequest(`/channels/${channel.id}`, { method: "DELETE", keepalive: true });
        invalidate(client, keys.subscription, keys.escalations);
      },
    });
  };
  return (
    <li className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3 last:border-b-0">
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded border border-line bg-panelHover text-inkMuted">
        <Icon size={16} aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-2 text-[13.5px] font-medium">
          {channel.name}
          <span className="rounded bg-panelHover px-1.5 py-px text-2xs font-normal text-inkMuted">
            {KIND_INFO[channel.type as Kind]?.label ?? "Telegram"}
          </span>
          {channel.pending_verification ? (
            <span className="text-2xs font-normal text-warn">ожидает подтверждения</span>
          ) : null}
        </p>
        <p className="mt-0.5 truncate text-xs text-inkMuted">
          {channel.target ?? "н/д"}
          {channel.verified_at ? ` · подтверждён ${formatDateTime(channel.verified_at)}` : ""}
        </p>
      </div>
      <Switch
        compact
        label={`Канал «${channel.name}» включён`}
        checked={channel.enabled}
        onChange={(value) => void toggle(value)}
      />
      <Button
        size="sm"
        variant="ghost"
        loading={busy === "test"}
        disabled={!channel.enabled}
        onClick={() =>
          void run("test", `/channels/${channel.id}/test`, "Тестовое уведомление в очереди")
        }
      >
        Тест
      </Button>
      <Menu
        label={`Действия с каналом ${channel.name}`}
        items={[
          ...(channel.pending_verification && channel.type !== "telegram"
            ? [
                {
                  label: "Отправить подтверждение",
                  onSelect: () =>
                    void run(
                      "verify",
                      `/channels/${channel.id}/verify`,
                      "Подтверждение отправлено",
                    ),
                },
              ]
            : []),
          { label: "Журнал доставок", onSelect: onLogs },
          ...(channel.type !== "telegram" ? [{ label: "Изменить", onSelect: onEdit }] : []),
          { label: "Удалить", danger: true, icon: <Trash2 size={14} />, onSelect: remove },
        ]}
        trigger={(props) => (
          <Button size="sm" variant="ghost" {...props}>
            Ещё
          </Button>
        )}
      />
    </li>
  );
}

function Escalations({ channels }: { channels: Channel[] }) {
  const rules = useEscalations();
  const sites = useSites();
  const subscription = useSubscription();
  const client = useQueryClient();
  const [minutes, setMinutes] = useState("15");
  const [channel, setChannel] = useState("");
  const [site, setSite] = useState("");
  const [busy, setBusy] = useState(false);
  if (subscription.data && !subscription.data.plan.features.escalations)
    return (
      <section id="escalations" className="panel scroll-mt-20" aria-labelledby="esc-title">
        <h2 id="esc-title" className="section-title">
          Эскалации
        </h2>
        <div className="mt-3">
          <PlanLock feature="Эскалация затянувшихся инцидентов" plan="Pro" />
        </div>
      </section>
    );
  const names = new Map((channels ?? []).map((item) => [item.id, item.name]));
  const siteNames = new Map((sites.data ?? []).map((item) => [item.id, item.name]));
  return (
    <section id="escalations" className="panel scroll-mt-20" aria-labelledby="esc-title">
      <h2 id="esc-title" className="section-title">
        Эскалации
      </h2>
      <p className="mt-0.5 text-[13px] text-inkMuted">
        Если инцидент не закрыт и не взят в работу, уведомить ещё один канал.
      </p>
      {rules.data?.length ? (
        <ul className="mt-3 grid gap-1.5">
          {rules.data.map((rule) => (
            <li
              key={rule.id}
              className="flex items-center gap-3 rounded border border-line px-3 py-2 text-[13px]"
            >
              <span className="min-w-0 flex-1">
                Через <b>{rule.after_minutes} мин</b> →{" "}
                {names.get(rule.channel_id) ?? "канал удалён"}
                <span className="text-inkMuted">
                  {" "}
                  · {rule.site_id ? (siteNames.get(rule.site_id) ?? "монитор") : "все мониторы"}
                </span>
              </span>
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label="Удалить правило"
                onClick={async () => {
                  try {
                    await apiRequest(`/escalations/${rule.id}`, { method: "DELETE" });
                    invalidate(client, keys.escalations);
                  } catch (error) {
                    notifyError(error);
                  }
                }}
              >
                <Trash2 size={14} aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
      <form
        className="mt-3 grid items-end gap-3 sm:grid-cols-[130px_minmax(0,1fr)_minmax(0,1fr)_auto]"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!channel) return;
          setBusy(true);
          try {
            await apiRequest("/escalations", {
              method: "POST",
              body: {
                after_minutes: Number(minutes),
                channel_id: Number(channel),
                site_id: site ? Number(site) : null,
              },
            });
            invalidate(client, keys.escalations);
            toast("Правило добавлено");
          } catch (error) {
            notifyError(error);
          } finally {
            setBusy(false);
          }
        }}
      >
        <Select
          label="Через"
          value={minutes}
          onChange={setMinutes}
          options={["5", "15", "30", "60", "120"].map((value) => ({
            value,
            label: `${value} мин`,
          }))}
        />
        <Select
          label="Канал"
          value={channel}
          onChange={setChannel}
          options={[
            { value: "", label: "Выберите" },
            ...channels.map((item) => ({ value: String(item.id), label: item.name })),
          ]}
        />
        <Select
          label="Мониторы"
          value={site}
          onChange={setSite}
          options={[
            { value: "", label: "Все мониторы" },
            ...(sites.data ?? []).map((item) => ({ value: String(item.id), label: item.name })),
          ]}
        />
        <Button
          type="submit"
          variant="primary"
          loading={busy}
          disabled={!channel}
          icon={<Plus size={14} aria-hidden="true" />}
        >
          Добавить
        </Button>
      </form>
    </section>
  );
}

const EVENT_LABEL: Record<string, string> = {
  down: "Сбой",
  up: "Восстановление",
  degraded: "Деградация",
  expiring: "Истечение срока",
};

function TemplateEditor({ template }: { template: Template }) {
  const client = useQueryClient();
  const [body, setBody] = useState(template.body);
  const [preview, setPreview] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(async () => {
      try {
        const result = await apiRequest<{ text: string }>("/templates/preview", {
          method: "POST",
          body: { body },
        });
        setPreview(result.text);
        setError(null);
      } catch (problem) {
        setError(errorMessage(problem));
      }
    }, 300);
    return () => window.clearTimeout(timer);
  }, [body]);
  return (
    <div className="grid gap-3 rounded border border-line p-3 lg:grid-cols-2">
      <div>
        <TextArea
          label={`${EVENT_LABEL[template.event] ?? template.event}${template.custom ? " · свой шаблон" : ""}`}
          value={body}
          maxLength={2000}
          onChange={(event) => setBody(event.target.value)}
          className="font-mono text-[12.5px]"
          error={error}
        />
        <div className="mt-2 flex flex-wrap gap-1">
          {["{name}", "{url}", "{status}", "{code}", "{duration}", "{error}"].map((token) => (
            <button
              key={token}
              type="button"
              className="chip font-mono"
              onClick={() => setBody((value) => `${value}${token}`)}
            >
              {token}
            </button>
          ))}
        </div>
      </div>
      <div className="min-w-0">
        <p className="label">Как придёт</p>
        <pre className="min-h-24 whitespace-pre-wrap rounded border border-line bg-sidebar p-3 text-[13px] leading-relaxed">
          {preview ?? "…"}
        </pre>
        <div className="mt-2 flex gap-2">
          <Button
            size="sm"
            variant="primary"
            loading={busy}
            disabled={Boolean(error)}
            onClick={async () => {
              setBusy(true);
              try {
                await apiRequest(`/templates/${template.event}`, { method: "PUT", body: { body } });
                invalidate(client, keys.templates);
                toast("Шаблон сохранён");
              } catch (problem) {
                notifyError(problem);
              } finally {
                setBusy(false);
              }
            }}
          >
            Сохранить
          </Button>
          {template.custom ? (
            <Button
              size="sm"
              onClick={async () => {
                await apiRequest(`/templates/${template.event}`, { method: "DELETE" });
                invalidate(client, keys.templates);
                toast("Вернули стандартный текст");
              }}
            >
              Сбросить
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function Templates() {
  const templates = useTemplates();
  const subscription = useSubscription();
  const allowed = subscription.data?.plan.features.templates;
  return (
    <section className="panel" aria-labelledby="tpl-title">
      <h2 id="tpl-title" className="section-title">
        Шаблоны уведомлений
      </h2>
      <p className="mt-0.5 text-[13px] text-inkMuted">
        Свой текст для каждого события с подстановками.
      </p>
      <div className="mt-3 grid gap-3">
        {!allowed ? (
          <PlanLock feature="Свои шаблоны уведомлений" />
        ) : templates.data ? (
          templates.data.map((template) => (
            <TemplateEditor key={template.event} template={template} />
          ))
        ) : (
          <Skeleton className="h-32" />
        )}
      </div>
    </section>
  );
}

export function ChannelsPage() {
  const channels = useChannels();
  const subscription = useSubscription();
  const [editing, setEditing] = useState<Channel | null | "new">(null);
  const [logs, setLogs] = useState<Channel | null>(null);
  useEffect(() => {
    document.title = "Каналы. pulsewatch";
  }, []);
  const list = channels.data ?? [];
  const limit = subscription.data?.plan.max_channels;
  return (
    <>
      <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">Каналы уведомлений</h1>
          <p className="mt-1 text-[13px] text-inkMuted">
            Куда сообщать о сбоях.{" "}
            {limit ? `Лимит тарифа: ${limit}, подключено ${list.length}.` : ""}
          </p>
        </div>
        <Button
          variant="primary"
          onClick={() => setEditing("new")}
          icon={<Plus size={15} aria-hidden="true" />}
        >
          Добавить канал
        </Button>
      </header>
      <div className="grid gap-5">
        <TelegramPanel />
        <section className="surface overflow-hidden" aria-label="Подключённые каналы">
          {channels.isPending ? (
            <div className="grid gap-2 p-4" role="status" aria-label="Загрузка">
              {[1, 2, 3].map((item) => (
                <Skeleton key={item} className="h-12" />
              ))}
            </div>
          ) : channels.isError ? (
            <div className="p-4">
              <ErrorPanel error={channels.error} onRetry={() => void channels.refetch()} />
            </div>
          ) : list.length ? (
            <ul>
              {list.map((channel) => (
                <ChannelRow
                  key={channel.id}
                  channel={channel}
                  onEdit={() => setEditing(channel)}
                  onLogs={() => setLogs(channel)}
                />
              ))}
            </ul>
          ) : (
            <EmptyState
              icon={<Bell size={18} aria-hidden="true" />}
              title="Каналов пока нет"
              action={
                <Button variant="primary" onClick={() => setEditing("new")}>
                  Подключить канал
                </Button>
              }
            >
              Без канала о сбое вы узнаете, только открыв журнал. Подключите Telegram, почту, Slack,
              Discord, Teams, ntfy или свой вебхук.
            </EmptyState>
          )}
        </section>
        <Escalations channels={list} />
        <Templates />
      </div>
      <ChannelDialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        channel={editing === "new" ? null : editing}
      />
      <DeliveriesDialog channel={logs} onClose={() => setLogs(null)} />
    </>
  );
}
