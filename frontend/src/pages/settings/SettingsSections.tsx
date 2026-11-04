import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiRequest, expireSession } from "../../api/client";
import { errorMessage } from "../../api/errors";
import {
  invalidate,
  keys,
  useAudit,
  useMe,
  usePayments,
  usePlans,
  useSessions,
  useSubscription,
} from "../../api/queries";
import type { AuditEntry, User } from "../../api/types";
import { applyTheme, currentTheme } from "../../app/theme";
import { formatDate, formatDateTime, formatMoney, setDisplayZone } from "../../lib/format";
import { ZONES } from "../../lib/zones";
import { Button, ButtonLink } from "../../ui/Button";
import { Segmented } from "../../ui/Controls";
import { Dialog } from "../../ui/Dialog";
import { ErrorPanel, Skeleton, Time } from "../../ui/Feedback";
import { FormError, TextField } from "../../ui/Field";
import { Select } from "../../ui/Select";
import { notifyError, toast } from "../../ui/toast-store";

const STATUS: Record<string, string> = {
  trialing: "пробный период",
  active: "активна",
  past_due: "просрочена",
  canceled: "отменена",
  expired: "истекла",
};

export function ProfileSection() {
  const me = useMe();
  const client = useQueryClient();
  const [theme, setTheme] = useState(currentTheme);
  const zones = me.data && !ZONES.includes(me.data.timezone) ? [me.data.timezone, ...ZONES] : ZONES;
  return (
    <section className="panel grid gap-4" aria-labelledby="profile-title">
      <h2 id="profile-title" className="section-title">
        Профиль
      </h2>
      <dl className="grid gap-1 text-[13px]">
        <dt className="text-xs text-inkMuted">Почта</dt>
        <dd>{me.data?.email ?? "н/д"}</dd>
      </dl>
      <div className="grid gap-4 sm:grid-cols-2">
        <Select
          label="Часовой пояс"
          hint="Во всех датах и графиках приложения"
          value={me.data?.timezone ?? "Europe/Moscow"}
          options={zones.map((zone) => ({ value: zone, label: zone.replace("_", " ") }))}
          onChange={async (timezone) => {
            try {
              const user = await apiRequest<User>("/me", { method: "PATCH", body: { timezone } });
              client.setQueryData(keys.me, user);
              setDisplayZone(user.timezone);
              toast(`Время показывается в ${user.timezone}`);
            } catch (error) {
              notifyError(error);
            }
          }}
        />
        <div>
          <p className="label">Тема</p>
          <Segmented<"dark" | "light">
            label="Тема оформления"
            value={theme}
            onChange={(value) => {
              applyTheme(value);
              setTheme(value);
            }}
            options={[
              { value: "dark", label: "Тёмная" },
              { value: "light", label: "Светлая" },
            ]}
          />
        </div>
      </div>
    </section>
  );
}

export function SecuritySection() {
  const sessions = useSessions();
  const client = useQueryClient();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [email, setEmail] = useState("");
  const [emailPassword, setEmailPassword] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const run = async (
    name: string,
    request: () => Promise<unknown>,
    done: string,
    after?: () => void,
  ) => {
    setBusy(name);
    setError(null);
    try {
      await request();
      toast(done);
      after?.();
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="grid gap-5">
      <section className="panel" aria-labelledby="sessions-title">
        <div className="panel-head">
          <h2 id="sessions-title" className="section-title">
            Активные сессии
          </h2>
          <Button
            size="sm"
            variant="danger"
            loading={busy === "all"}
            onClick={() =>
              void run(
                "all",
                () => apiRequest("/account/sessions", { method: "DELETE" }),
                "Все сессии завершены",
                expireSession,
              )
            }
          >
            Выйти везде
          </Button>
        </div>
        {sessions.isError ? (
          <ErrorPanel error={sessions.error} onRetry={() => void sessions.refetch()} />
        ) : null}
        {sessions.isPending ? (
          <Skeleton className="h-24" />
        ) : (
          <div tabIndex={0} role="region" aria-label="Таблица" className="table-scroll">
            <table className="table min-w-[560px]">
              <thead>
                <tr>
                  <th scope="col">Устройство</th>
                  <th scope="col">IP</th>
                  <th scope="col">Вход</th>
                  <th scope="col">Активность</th>
                  <th scope="col">
                    <span className="sr-only">Действия</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {(sessions.data ?? []).map((session) => (
                  <tr key={session.id}>
                    <td>
                      <span className="font-medium">
                        {session.browser ?? "Неизвестный браузер"}
                      </span>
                      <span className="text-inkMuted"> · {session.os ?? "ОС не определена"}</span>
                      {session.current ? (
                        <span className="ml-2 rounded-full bg-accent/15 px-1.5 text-2xs text-accent">
                          эта сессия
                        </span>
                      ) : null}
                    </td>
                    <td className="mono text-inkMuted">{session.ip ?? "н/д"}</td>
                    <td className="whitespace-nowrap">{formatDateTime(session.created_at)}</td>
                    <td>
                      <Time value={session.last_used_at} />
                    </td>
                    <td className="text-right">
                      {!session.current ? (
                        <Button
                          size="xs"
                          variant="ghost"
                          onClick={async () => {
                            try {
                              await apiRequest(`/account/sessions/${session.id}`, {
                                method: "DELETE",
                              });
                              invalidate(client, keys.sessions);
                              toast("Сессия завершена");
                            } catch (problem) {
                              notifyError(problem);
                            }
                          }}
                        >
                          Завершить
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <div className="grid gap-5 lg:grid-cols-2">
        <form
          className="panel grid content-start gap-3"
          aria-labelledby="pass-title"
          onSubmit={(event) => {
            event.preventDefault();
            void run(
              "pass",
              () =>
                apiRequest("/account/password", {
                  method: "POST",
                  body: { current_password: current, new_password: next },
                }),
              "Пароль изменён. Войдите снова",
              expireSession,
            );
          }}
        >
          <h2 id="pass-title" className="section-title">
            Пароль
          </h2>
          <TextField
            label="Текущий пароль"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
          />
          <TextField
            label="Новый пароль"
            type="password"
            autoComplete="new-password"
            minLength={8}
            maxLength={72}
            value={next}
            onChange={(event) => setNext(event.target.value)}
            hint="После смены все сессии завершатся"
          />
          <div>
            <Button
              type="submit"
              size="sm"
              loading={busy === "pass"}
              disabled={!current || next.length < 8}
            >
              Сменить пароль
            </Button>
          </div>
        </form>
        <form
          className="panel grid content-start gap-3"
          aria-labelledby="mail-title"
          onSubmit={(event) => {
            event.preventDefault();
            void run(
              "mail",
              () =>
                apiRequest("/account/email", {
                  method: "POST",
                  body: { email, password: emailPassword },
                }),
              "Ссылка для подтверждения отправлена на новый адрес",
            );
          }}
        >
          <h2 id="mail-title" className="section-title">
            Почта
          </h2>
          <TextField
            label="Новый адрес"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          <TextField
            label="Пароль"
            type="password"
            autoComplete="current-password"
            value={emailPassword}
            onChange={(event) => setEmailPassword(event.target.value)}
          />
          <div>
            <Button
              type="submit"
              size="sm"
              loading={busy === "mail"}
              disabled={!email || !emailPassword}
            >
              Отправить подтверждение
            </Button>
          </div>
        </form>
      </div>
      <FormError message={error} />
      <section
        className="panel flex flex-wrap items-center justify-between gap-3 border-down/25"
        aria-labelledby="danger-title"
      >
        <div>
          <h2 id="danger-title" className="section-title text-down">
            Удаление аккаунта
          </h2>
          <p className="mt-0.5 text-[13px] text-inkMuted">
            Мониторы, история и статус-страницы будут удалены без возможности восстановления.
          </p>
        </div>
        <Button variant="danger" size="sm" onClick={() => setDeleting(true)}>
          Удалить аккаунт
        </Button>
      </section>
      <DeleteAccount open={deleting} onClose={() => setDeleting(false)} />
    </div>
  );
}

function DeleteAccount({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="sm"
      title="Удалить аккаунт?"
      description="Подтвердите почтой и паролем. Действие необратимо."
      footer={
        <>
          <Button onClick={onClose}>Отмена</Button>
          <Button
            variant="danger"
            loading={busy}
            disabled={!email || !password}
            onClick={async () => {
              setBusy(true);
              try {
                await apiRequest("/account", { method: "DELETE", body: { email, password } });
                expireSession();
              } catch (problem) {
                setError(errorMessage(problem));
              } finally {
                setBusy(false);
              }
            }}
          >
            Удалить навсегда
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        <TextField
          label="Почта аккаунта"
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <TextField
          label="Пароль"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <FormError message={error} />
      </div>
    </Dialog>
  );
}

const ACTIONS: Record<string, string> = {
  "auth.login": "Вход",
  "site.create": "Создан монитор",
  "site.update": "Изменён монитор",
  "site.delete": "Удалён монитор",
  "site.import": "Импортирован монитор",
  "site.heartbeat_rotate": "Перевыпущен адрес пинга",
  "channel.create": "Добавлен канал",
  "channel.update": "Изменён канал",
  "channel.delete": "Удалён канал",
  "channel.verified": "Канал подтверждён",
  "page.create": "Создана статус-страница",
  "page.update": "Изменена статус-страница",
  "page.delete": "Удалена статус-страница",
  "page.announcement": "Опубликовано объявление",
  "incident.note": "Заметка к инциденту",
  "incident.acknowledge": "Инцидент взят в работу",
  "incident.postmortem": "Сохранён постмортем",
  "escalation.create": "Добавлено правило эскалации",
  "escalation.delete": "Удалено правило эскалации",
  "template.update": "Изменён шаблон уведомления",
  "subscription.paid": "Оплачена подписка",
  "subscription.cancel": "Отменено продление",
  "subscription.downgrade_scheduled": "Запланирован переход на тариф",
  "subscription.expired": "Подписка истекла",
  "account.password": "Сменён пароль",
  "account.email": "Сменена почта",
  "account.timezone": "Сменён часовой пояс",
  "session.revoke": "Завершена сессия",
  "key.create": "Выпущен API-ключ",
  "key.revoke": "Отозван API-ключ",
};

function value(item: unknown): string {
  if (item === null || item === undefined || item === "") return "н/д";
  if (typeof item === "string" || typeof item === "number" || typeof item === "boolean")
    return String(item);
  return JSON.stringify(item);
}

function AuditRow({ entry }: { entry: AuditEntry }) {
  return (
    <li className="grid gap-1 border-b border-line px-4 py-3 last:border-b-0 sm:grid-cols-[160px_minmax(0,1fr)]">
      <span className="text-xs text-inkMuted">{formatDateTime(entry.created_at)}</span>
      <div className="min-w-0">
        <p className="text-[13px] font-medium">
          {ACTIONS[entry.action] ?? entry.action}
          {entry.resource_id ? (
            <span className="font-normal text-inkMuted"> #{entry.resource_id}</span>
          ) : null}
        </p>
        {entry.changes.length ? (
          <ul className="mt-1 grid gap-0.5">
            {entry.changes.map((change) => (
              <li
                key={change.field}
                className="mono flex min-w-0 flex-wrap gap-x-1.5 text-inkMuted"
              >
                <span className="text-ink">{change.field}</span>
                <span
                  className="max-w-[260px] truncate line-through decoration-inkFaint"
                  title={value(change.old)}
                >
                  {value(change.old)}
                </span>
                <span aria-hidden="true">→</span>
                <span className="max-w-[320px] truncate text-ink" title={value(change.new)}>
                  {value(change.new)}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </li>
  );
}

export function AuditSection() {
  const [type, setType] = useState("");
  const audit = useAudit(type);
  const rows = audit.data?.pages.flat() ?? [];
  return (
    <section className="surface overflow-hidden" aria-labelledby="audit-title">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <h2 id="audit-title" className="section-title">
          Журнал действий
        </h2>
        <Select
          ariaLabel="Раздел"
          size="sm"
          className="w-[180px]"
          value={type}
          onChange={setType}
          options={[
            { value: "", label: "Все разделы" },
            { value: "site", label: "Мониторы" },
            { value: "channel", label: "Каналы" },
            { value: "status_page", label: "Статус-страницы" },
            { value: "incident", label: "Инциденты" },
            { value: "billing", label: "Оплата" },
            { value: "account", label: "Аккаунт" },
            { value: "session", label: "Сессии" },
          ]}
        />
      </div>
      {audit.isError ? (
        <div className="px-4 pb-4">
          <ErrorPanel error={audit.error} onRetry={() => void audit.refetch()} />
        </div>
      ) : null}
      {audit.isPending ? (
        <div className="grid gap-2 px-4 pb-4">
          {[1, 2, 3].map((item) => (
            <Skeleton key={item} className="h-10" />
          ))}
        </div>
      ) : (
        <ul className="border-t border-line">
          {rows.map((entry) => (
            <AuditRow key={entry.id} entry={entry} />
          ))}
          {!rows.length ? (
            <li className="px-4 py-8 text-center text-[13px] text-inkMuted">Записей нет.</li>
          ) : null}
        </ul>
      )}
      {audit.hasNextPage ? (
        <div className="border-t border-line p-2 text-center">
          <Button
            size="sm"
            variant="ghost"
            loading={audit.isFetchingNextPage}
            onClick={() => void audit.fetchNextPage()}
          >
            Показать ещё
          </Button>
        </div>
      ) : null}
    </section>
  );
}

export function BillingSection() {
  const subscription = useSubscription();
  const payments = usePayments();
  const plans = usePlans();
  const client = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const act = async (name: string, path: string, body: unknown, done: string) => {
    setBusy(name);
    try {
      await apiRequest(path, { method: "POST", body });
      invalidate(client, keys.subscription);
      toast(done);
    } catch (problem) {
      notifyError(problem);
    } finally {
      setBusy(null);
    }
  };
  if (subscription.isPending) return <Skeleton className="h-48 rounded-lg" />;
  if (subscription.isError)
    return <ErrorPanel error={subscription.error} onRetry={() => void subscription.refetch()} />;
  const data = subscription.data;
  const cheaper = (plans.data ?? []).filter((plan) => plan.price_kop < data.plan.price_kop);
  return (
    <div className="grid gap-5">
      <section className="panel" aria-labelledby="sub-title">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 id="sub-title" className="section-title">
              Тариф {data.plan.name}
            </h2>
            <p className="mt-1 text-[13px] text-inkMuted">
              Подписка {STATUS[data.status] ?? data.status} · период до{" "}
              {formatDate(data.current_period_end)}
              {data.scheduled_plan
                ? ` · затем ${data.scheduled_plan === "free" ? "Free" : "Pro"}`
                : data.cancel_at_period_end
                  ? " · продление отменено"
                  : ""}
            </p>
          </div>
          <ButtonLink to="/pricing" size="sm" variant="primary">
            Сменить тариф
          </ButtonLink>
        </div>
        <dl className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <div>
            <dt className="text-xs text-inkMuted">Мониторы</dt>
            <dd className="mt-0.5 text-[15px] font-medium">
              {data.usage.sites ?? 0} из {data.plan.max_sites}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-inkMuted">Каналы</dt>
            <dd className="mt-0.5 text-[15px] font-medium">
              {data.usage.channels ?? 0} из {data.plan.max_channels ?? "∞"}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-inkMuted">История</dt>
            <dd className="mt-0.5 text-[15px] font-medium">{data.plan.retention_days} дней</dd>
          </div>
          <div>
            <dt className="text-xs text-inkMuted">Регионы</dt>
            <dd className="mt-0.5 text-[15px] font-medium">{data.plan.max_regions}</dd>
          </div>
        </dl>
        {data.plan.code !== "free" ? (
          <div className="mt-5 flex flex-wrap gap-2 border-t border-line pt-4">
            {data.cancel_at_period_end || data.scheduled_plan ? (
              <Button
                size="sm"
                loading={busy === "resume"}
                onClick={() =>
                  void act("resume", "/billing/resume", undefined, "Продление восстановлено")
                }
              >
                Не переходить, продлевать
              </Button>
            ) : (
              <>
                {cheaper.map((plan) => (
                  <Button
                    key={plan.code}
                    size="sm"
                    loading={busy === plan.code}
                    onClick={() =>
                      void act(
                        plan.code,
                        "/billing/downgrade",
                        { plan_code: plan.code },
                        `С ${formatDate(data.current_period_end)}: ${plan.name}`,
                      )
                    }
                  >
                    Перейти на {plan.name} в конце периода
                  </Button>
                ))}
              </>
            )}
          </div>
        ) : null}
      </section>
      <section className="surface overflow-hidden" aria-labelledby="pay-title">
        <h2 id="pay-title" className="section-title px-4 py-3">
          Платежи
        </h2>
        {payments.data?.length ? (
          <div tabIndex={0} role="region" aria-label="Таблица" className="table-scroll">
            <table className="table min-w-[480px]">
              <thead>
                <tr>
                  <th scope="col">Дата</th>
                  <th scope="col">Сумма</th>
                  <th scope="col">Статус</th>
                  <th scope="col">Провайдер</th>
                </tr>
              </thead>
              <tbody>
                {payments.data.map((payment) => (
                  <tr key={payment.id}>
                    <td>{formatDateTime(payment.paid_at ?? payment.created_at)}</td>
                    <td className="font-medium">{formatMoney(payment.amount_kop)}</td>
                    <td
                      className={payment.status === "succeeded" ? "text-accent" : "text-inkMuted"}
                    >
                      {payment.status === "succeeded" ? "оплачен" : payment.status}
                    </td>
                    <td className="text-inkMuted">{payment.provider}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="px-4 pb-4 text-[13px] text-inkMuted">Платежей ещё не было.</p>
        )}
      </section>
    </div>
  );
}
