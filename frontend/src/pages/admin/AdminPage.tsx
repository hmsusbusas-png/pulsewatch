import { useDeferredValue, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { apiRequest } from "../../api/client";
import { invalidate, useAdminStats, useAdminUsers, useMe } from "../../api/queries";
import { Bars } from "../../charts/Small";
import { formatDate, formatDuration, formatMoney, formatNumber } from "../../lib/format";
import { ErrorPanel, Metric, Skeleton } from "../../ui/Feedback";
import { Select } from "../../ui/Select";
import { notifyError, toast } from "../../ui/toast-store";

export function AdminPage() {
  const me = useMe();
  const admin = me.data?.is_admin === true;
  const stats = useAdminStats(admin);
  const [search, setSearch] = useState("");
  const deferred = useDeferredValue(search);
  const users = useAdminUsers(deferred, admin);
  const client = useQueryClient();
  useEffect(() => {
    document.title = "Админка. pulsewatch";
  }, []);
  if (me.isPending) return <Skeleton className="h-64 rounded-lg" />;
  if (!admin)
    return (
      <ErrorPanel error={new Error("Раздел доступен только администраторам")} title="Нет доступа" />
    );
  const data = stats.data;
  const plans = data ? Object.entries(data.subscriptions) : [];
  const totalSubs = plans.reduce((sum, [, count]) => sum + count, 0);
  const lagTone =
    data && data.queue_lag_seconds > 120
      ? "text-down"
      : data && data.queue_lag_seconds > 30
        ? "text-warn"
        : "text-accent";
  return (
    <>
      <h1 className="page-title mb-5">Админка</h1>
      {stats.isError ? (
        <ErrorPanel error={stats.error} onRetry={() => void stats.refetch()} />
      ) : null}
      {data ? (
        <>
          <section className="panel mb-5" aria-label="Показатели">
            <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 xl:grid-cols-6">
              <Metric label="Пользователи" value={formatNumber(data.users)} />
              <Metric
                label={`Выручка с ${formatDate(data.revenue_month_start)}`}
                value={formatMoney(data.revenue_kop)}
              />
              <Metric label="Мониторов" value={formatNumber(data.sites)} />
              <Metric
                label="Проверок за 24 ч"
                value={formatNumber(data.checks_24h)}
                hint={`всего ${formatNumber(data.checks)}`}
              />
              <Metric
                label="Отставание очереди"
                value={formatDuration(data.queue_lag_seconds)}
                tone={lagTone}
                hint={`в очереди ${data.due_checks}`}
              />
              <Metric
                label="Воркер"
                value={
                  data.worker_seen_seconds === null
                    ? "нет сигнала"
                    : `${data.worker_seen_seconds} с назад`
                }
                tone={
                  data.worker_seen_seconds === null || data.worker_seen_seconds > 90
                    ? "text-down"
                    : "text-accent"
                }
                hint={`уведомлений в очереди ${data.pending_notifications}`}
              />
            </dl>
          </section>
          <div className="mb-5 grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
            <section className="panel" aria-labelledby="signups-title">
              <h2 id="signups-title" className="section-title mb-4">
                Регистрации за 90 дней
              </h2>
              <Bars points={data.signups} label="Регистрации по дням" />
            </section>
            <section className="panel" aria-labelledby="plans-title">
              <h2 id="plans-title" className="section-title mb-4">
                Подписки по тарифам
              </h2>
              <ul className="grid gap-3">
                {plans.map(([code, count]) => (
                  <li key={code}>
                    <div className="mb-1 flex justify-between text-[13px]">
                      <span className="capitalize">{code}</span>
                      <span>{count}</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-line">
                      <div
                        className="h-full origin-left bg-accent"
                        style={{ transform: `scaleX(${totalSubs ? count / totalSubs : 0})` }}
                      />
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        </>
      ) : (
        <Skeleton className="mb-5 h-48 rounded-lg" />
      )}
      <section className="surface overflow-hidden" aria-labelledby="users-title">
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
          <h2 id="users-title" className="section-title">
            Пользователи
          </h2>
          <div className="relative w-full sm:w-72">
            <Search
              size={15}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-inkMuted"
              aria-hidden="true"
            />
            <input
              type="search"
              aria-label="Поиск по почте"
              placeholder="Поиск по почте"
              className="field min-h-9 py-1.5 pl-9"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
        </div>
        <div tabIndex={0} role="region" aria-label="Таблица" className="table-scroll">
          <table className="table min-w-[720px]">
            <thead>
              <tr>
                <th scope="col">Почта</th>
                <th scope="col">Тариф</th>
                <th scope="col">Статус</th>
                <th scope="col">До</th>
                <th scope="col" className="text-right">
                  Мониторы
                </th>
                <th scope="col">Регистрация</th>
              </tr>
            </thead>
            <tbody>
              {(users.data ?? []).map((user) => (
                <tr key={user.id}>
                  <td className="max-w-[260px] truncate">
                    {user.email}
                    {user.is_admin ? <span className="ml-2 text-2xs text-info">админ</span> : null}
                  </td>
                  <td className="w-[150px]">
                    <Select
                      ariaLabel={`Тариф ${user.email}`}
                      size="sm"
                      value={user.plan ?? "free"}
                      options={[
                        { value: "free", label: "Free" },
                        { value: "pro", label: "Pro" },
                        { value: "business", label: "Business" },
                      ]}
                      onChange={async (plan) => {
                        try {
                          await apiRequest(`/admin/users/${user.id}/plan`, {
                            method: "POST",
                            body: { plan_code: plan, days: 30 },
                          });
                          invalidate(client, ["admin"]);
                          toast(`${user.email}: тариф ${plan}`);
                        } catch (error) {
                          notifyError(error);
                        }
                      }}
                    />
                  </td>
                  <td className="text-inkMuted">{user.status ?? "н/д"}</td>
                  <td>{user.period_end ? formatDate(user.period_end) : "н/д"}</td>
                  <td className="text-right">{user.sites}</td>
                  <td>{formatDate(user.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {users.isPending ? (
            <div className="p-4">
              <Skeleton className="h-24" />
            </div>
          ) : null}
        </div>
      </section>
    </>
  );
}
