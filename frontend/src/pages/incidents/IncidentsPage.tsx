import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Siren } from "lucide-react";
import { useIncidents } from "../../api/queries";
import { formatDateTime, formatDuration } from "../../lib/format";
import { Button } from "../../ui/Button";
import { Segmented } from "../../ui/Controls";
import { EmptyState, ErrorPanel, Skeleton } from "../../ui/Feedback";

type State = "all" | "open" | "resolved";

export function IncidentsPage() {
  const [params, setParams] = useSearchParams();
  const state = (
    ["open", "resolved"].includes(params.get("state") ?? "") ? params.get("state") : "all"
  ) as State;
  const incidents = useIncidents(state);
  const rows = incidents.data?.pages.flat() ?? [];
  useEffect(() => {
    document.title = "Инциденты. pulsewatch";
  }, []);
  return (
    <>
      <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">Инциденты</h1>
          <p className="mt-1 text-[13px] text-inkMuted">
            Сбои и деградации по всем мониторам, новые сверху.
          </p>
        </div>
        <Segmented<State>
          label="Фильтр"
          value={state}
          onChange={(value) =>
            setParams(value === "all" ? {} : { state: value }, { replace: true })
          }
          options={[
            { value: "all", label: "Все" },
            { value: "open", label: "Открытые" },
            { value: "resolved", label: "Закрытые" },
          ]}
        />
      </header>
      {incidents.isError ? (
        <ErrorPanel error={incidents.error} onRetry={() => void incidents.refetch()} />
      ) : null}
      <section className="surface overflow-hidden" aria-label="Список инцидентов">
        {incidents.isPending ? (
          <div className="grid gap-3 p-4" role="status" aria-label="Загрузка">
            {Array.from({ length: 6 }, (_, index) => (
              <Skeleton key={index} className="h-10" />
            ))}
          </div>
        ) : rows.length ? (
          <div tabIndex={0} role="region" aria-label="Таблица" className="table-scroll">
            <table className="table min-w-[720px]">
              <thead>
                <tr>
                  <th scope="col">Монитор</th>
                  <th scope="col">Что случилось</th>
                  <th scope="col">Начало</th>
                  <th scope="col" className="text-right">
                    Длительность
                  </th>
                  <th scope="col">Состояние</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((incident) => (
                  <tr key={incident.id} className="row-interactive">
                    <td>
                      <Link
                        to={`/incidents/${incident.id}`}
                        className="font-medium hover:text-accent"
                      >
                        {incident.site_name}
                      </Link>
                    </td>
                    <td className="max-w-[320px] truncate text-inkMuted" title={incident.reason}>
                      {incident.kind === "degraded"
                        ? `Деградация: ${incident.reason}`
                        : incident.reason}
                    </td>
                    <td className="whitespace-nowrap">{formatDateTime(incident.started_at)}</td>
                    <td className="text-right">{formatDuration(incident.duration_seconds)}</td>
                    <td>
                      <span
                        className={`inline-flex items-center gap-1.5 text-xs ${incident.is_open ? (incident.kind === "degraded" ? "text-warn" : "text-down") : "text-inkMuted"}`}
                      >
                        <span
                          className={`h-1.5 w-1.5 rounded-full ${incident.is_open ? (incident.kind === "degraded" ? "bg-warn" : "bg-down") : "bg-unknown"}`}
                          aria-hidden="true"
                        />
                        {incident.is_open
                          ? incident.acknowledged_at
                            ? "в работе"
                            : "открыт"
                          : "закрыт"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<Siren size={18} aria-hidden="true" />}
            title={state === "open" ? "Открытых инцидентов нет" : "Инцидентов ещё не было"}
          >
            Здесь появятся сбои, подтверждённые проверками, и периоды медленного отклика с лентой
            событий и диагностикой на момент сбоя.
          </EmptyState>
        )}
      </section>
      {incidents.hasNextPage ? (
        <div className="mt-3 text-center">
          <Button
            size="sm"
            loading={incidents.isFetchingNextPage}
            onClick={() => void incidents.fetchNextPage()}
          >
            Показать ещё
          </Button>
        </div>
      ) : null}
    </>
  );
}
