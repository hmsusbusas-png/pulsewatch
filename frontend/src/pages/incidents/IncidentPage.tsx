import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CheckCircle2,
  CircleDot,
  Hand,
  MessageSquare,
  Siren,
  TriangleAlert,
} from "lucide-react";
import { apiRequest } from "../../api/client";
import { invalidate, keys, useIncident, useRegions } from "../../api/queries";
import type { Diagnostic, IncidentDetail, TimelineEntry } from "../../api/types";
import { Waterfall } from "../../charts/Waterfall";
import { formatDuration, formatFullDateTime } from "../../lib/format";
import { Button } from "../../ui/Button";
import { Segmented } from "../../ui/Controls";
import { ErrorPanel, Skeleton, StatusPill } from "../../ui/Feedback";
import { TextArea } from "../../ui/Field";
import { Markdown } from "../../ui/Markdown";
import { notifyError, toast } from "../../ui/toast-store";

type NoteStatus = "investigating" | "identified" | "monitoring" | "resolved";
const NOTE_LABEL: Record<NoteStatus, string> = {
  investigating: "Расследуем",
  identified: "Причина найдена",
  monitoring: "Наблюдаем",
  resolved: "Решено",
};
const KIND_LABEL: Record<TimelineEntry["kind"], string> = {
  started: "Первая неудачная проверка",
  confirmed: "Сбой подтверждён",
  acknowledged: "Взят в работу",
  escalated: "Эскалация на дополнительный канал",
  note: "Заметка",
  resolved: "Восстановлен",
};

function TimelineIcon({ kind }: { kind: TimelineEntry["kind"] }) {
  const map = {
    started: [CircleDot, "text-down border-down/40 bg-down/10"],
    confirmed: [Siren, "text-down border-down/40 bg-down/10"],
    acknowledged: [Hand, "text-info border-info/40 bg-info/10"],
    escalated: [TriangleAlert, "text-warn border-warn/40 bg-warn/10"],
    note: [MessageSquare, "text-ink border-lineStrong bg-panelHover"],
    resolved: [CheckCircle2, "text-accent border-accent/40 bg-accent/10"],
  } as const;
  const [Icon, color] = map[kind];
  return (
    <span
      className={`relative z-[1] flex h-7 w-7 shrink-0 items-center justify-center rounded-full border ${color}`}
    >
      <Icon size={14} aria-hidden="true" />
    </span>
  );
}

function Timeline({ incident }: { incident: IncidentDetail }) {
  return (
    <ol className="relative grid gap-0" aria-label="Лента событий">
      {incident.timeline.map((entry, index) => (
        <li
          key={`${entry.kind}-${entry.at}-${index}`}
          className="relative flex gap-3 pb-5 last:pb-0"
        >
          {index < incident.timeline.length - 1 ? (
            <span
              className="absolute left-[13px] top-7 h-[calc(100%-28px)] w-px bg-line"
              aria-hidden="true"
            />
          ) : null}
          <TimelineIcon kind={entry.kind} />
          <div className="min-w-0 flex-1 pt-0.5">
            <p className="flex flex-wrap items-baseline gap-x-2 text-[13px]">
              <span className="font-medium">
                {entry.kind === "note" && entry.status
                  ? (NOTE_LABEL[entry.status as NoteStatus] ?? entry.status)
                  : KIND_LABEL[entry.kind]}
              </span>
              <time dateTime={entry.at} className="text-xs text-inkMuted">
                {formatFullDateTime(entry.at)}
              </time>
            </p>
            {entry.text ? (
              <p className="mt-1 whitespace-pre-wrap text-[13px] text-inkMuted">{entry.text}</p>
            ) : null}
            {entry.duration_seconds !== null && entry.duration_seconds !== undefined ? (
              <p className="mt-1 text-2xs text-inkFaint">
                {index === incident.timeline.length - 1 && incident.is_open
                  ? "длится"
                  : "до следующего события"}
                : {formatDuration(entry.duration_seconds)}
              </p>
            ) : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

function DiagnosticView({ item }: { item: Diagnostic }) {
  const regions = useRegions();
  const region = regions.data?.find((entry) => entry.code === item.region);
  return (
    <div className="grid gap-4">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-[13px] sm:grid-cols-4">
        <div>
          <dt className="text-xs text-inkMuted">Код ответа</dt>
          <dd className="mt-0.5 font-medium">{item.status_code ?? "нет ответа"}</dd>
        </div>
        <div>
          <dt className="text-xs text-inkMuted">IP</dt>
          <dd className="mono mt-0.5">{item.ip ?? "н/д"}</dd>
        </div>
        <div>
          <dt className="text-xs text-inkMuted">Регион</dt>
          <dd className="mt-0.5">{region?.name ?? item.region ?? "н/д"}</dd>
        </div>
        <div>
          <dt className="text-xs text-inkMuted">DNS</dt>
          <dd className="mono mt-0.5 truncate" title={item.resolved.join(", ")}>
            {item.resolved.join(", ") || "н/д"}
          </dd>
        </div>
      </dl>
      {item.error ? (
        <div>
          <p className="label">Ошибка</p>
          <p className="mono rounded border border-down/30 bg-down/[0.06] px-3 py-2 text-down">
            {item.error}
          </p>
        </div>
      ) : null}
      {Object.keys(item.timings).length ? (
        <div>
          <p className="label">Этапы запроса</p>
          <Waterfall phases={item.timings} title="Этапы запроса в момент сбоя" />
        </div>
      ) : null}
      {Object.keys(item.headers).length ? (
        <div>
          <p className="label">Заголовки ответа</p>
          <div
            tabIndex={0}
            role="region"
            aria-label="Таблица"
            className="table-scroll rounded border border-line"
          >
            <table className="table">
              <tbody>
                {Object.entries(item.headers).map(([name, value]) => (
                  <tr key={name}>
                    <th
                      scope="row"
                      className="mono w-1/3 !bg-transparent !py-1.5 text-left font-normal text-inkMuted"
                    >
                      {name}
                    </th>
                    <td className="mono !py-1.5 break-all">{value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
      {item.body ? (
        <div>
          <p className="label">Тело ответа, первые 2 КБ (секреты вырезаны)</p>
          <pre className="mono max-h-56 overflow-auto rounded border border-line bg-sidebar p-3 leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]">
            {item.body}
          </pre>
        </div>
      ) : null}
    </div>
  );
}

export function IncidentPage() {
  const id = Number(useParams().id);
  const incident = useIncident(id);
  const client = useQueryClient();
  const [status, setStatus] = useState<NoteStatus>("investigating");
  const [text, setText] = useState("");
  const [postmortem, setPostmortem] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [diagnostic, setDiagnostic] = useState(0);

  useEffect(() => {
    if (incident.data) document.title = `Инцидент: ${incident.data.site_name}. pulsewatch`;
  }, [incident.data]);

  if (incident.isPending)
    return (
      <div className="grid gap-4" role="status" aria-label="Загрузка">
        <Skeleton className="h-16" />
        <Skeleton className="h-80 rounded-lg" />
      </div>
    );
  if (incident.isError)
    return (
      <ErrorPanel
        error={incident.error}
        onRetry={() => void incident.refetch()}
        title="Инцидент не загрузился"
      />
    );
  const data = incident.data;
  const refresh = () =>
    invalidate(client, keys.incident(id), ["incidents"], keys.siteIncidents(data.site_id ?? 0));
  const act = async (name: string, request: () => Promise<unknown>, done: string) => {
    setBusy(name);
    try {
      await request();
      toast(done);
      refresh();
      return true;
    } catch (error) {
      notifyError(error);
      return false;
    } finally {
      setBusy(null);
    }
  };
  const tone = data.is_open ? (data.kind === "degraded" ? "degraded" : "down") : "up";

  return (
    <>
      <Link
        to="/incidents"
        className="mb-3 inline-flex items-center gap-1.5 text-[13px] text-inkMuted hover:text-ink"
      >
        <ArrowLeft size={14} aria-hidden="true" />
        Инциденты
      </Link>
      <header className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="page-title">
              {data.kind === "degraded" ? "Деградация отклика" : "Недоступность"}: {data.site_name}
            </h1>
            <StatusPill
              tone={tone}
              label={data.is_open ? (data.acknowledged_at ? "в работе" : "открыт") : "закрыт"}
            />
          </div>
          <p className="mt-1.5 text-[13px] text-inkMuted">
            {formatFullDateTime(data.started_at)} · {data.is_open ? "длится" : "длился"}{" "}
            {formatDuration(data.duration_seconds)} ·{" "}
            <Link to={`/sites/${data.site_id}`} className="underline hover:text-ink">
              к монитору
            </Link>
          </p>
        </div>
        {data.is_open && !data.acknowledged_at ? (
          <Button
            variant="primary"
            size="sm"
            loading={busy === "ack"}
            onClick={() =>
              void act(
                "ack",
                () => apiRequest(`/incidents/${id}/acknowledge`, { method: "POST" }),
                "Инцидент взят в работу. Эскалация остановлена",
              )
            }
            icon={<Hand size={14} aria-hidden="true" />}
          >
            Взять в работу
          </Button>
        ) : null}
      </header>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="grid min-w-0 content-start gap-5">
          <section className="panel" aria-labelledby="diag-title">
            <div className="panel-head">
              <h2 id="diag-title" className="section-title">
                Диагностика в момент сбоя
              </h2>
              {data.diagnostics.length > 1 ? (
                <Segmented<string>
                  label="Снимок"
                  size="sm"
                  value={String(diagnostic)}
                  onChange={(value) => setDiagnostic(Number(value))}
                  options={data.diagnostics.map((_, index) => ({
                    value: String(index),
                    label: index === 0 ? "первая ошибка" : "подтверждение",
                  }))}
                />
              ) : null}
            </div>
            {data.diagnostics[diagnostic] ? (
              <DiagnosticView item={data.diagnostics[diagnostic]} />
            ) : (
              <p className="text-[13px] text-inkMuted">
                {data.kind === "degraded"
                  ? "Для деградации снимок ответа не сохраняется: проверки проходили успешно, но медленно."
                  : "Снимок для этого инцидента не сохранён."}
              </p>
            )}
          </section>
          <section className="panel" aria-labelledby="pm-title">
            <div className="panel-head">
              <h2 id="pm-title" className="section-title">
                Постмортем
              </h2>
              {postmortem === null ? (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    setPostmortem(
                      data.postmortem ?? "## Что произошло\n\n## Хронология\n\n## Что меняем\n",
                    )
                  }
                >
                  {data.postmortem ? "Изменить" : "Написать"}
                </Button>
              ) : null}
            </div>
            {postmortem !== null ? (
              <div className="grid gap-3">
                <TextArea
                  label="Текст (Markdown)"
                  className="min-h-[220px] font-mono text-[13px]"
                  value={postmortem}
                  onChange={(event) => setPostmortem(event.target.value)}
                />
                <div className="flex gap-2">
                  <Button
                    variant="primary"
                    size="sm"
                    loading={busy === "pm"}
                    onClick={async () => {
                      if (
                        await act(
                          "pm",
                          () =>
                            apiRequest(`/incidents/${id}/postmortem`, {
                              method: "PUT",
                              body: { text: postmortem },
                            }),
                          "Постмортем сохранён",
                        )
                      )
                        setPostmortem(null);
                    }}
                  >
                    Сохранить
                  </Button>
                  <Button size="sm" onClick={() => setPostmortem(null)}>
                    Отмена
                  </Button>
                </div>
              </div>
            ) : data.postmortem ? (
              <Markdown source={data.postmortem} />
            ) : (
              <p className="text-[13px] text-inkMuted">
                Опишите причину и меры. Команда и заказчики увидят разбор рядом с хронологией.
              </p>
            )}
          </section>
        </div>
        <div className="grid min-w-0 content-start gap-5">
          <section className="panel" aria-labelledby="tl-title">
            <h2 id="tl-title" className="section-title mb-4">
              Лента событий
            </h2>
            <Timeline incident={data} />
          </section>
          <section className="panel" aria-labelledby="note-title">
            <h2 id="note-title" className="section-title">
              Заметка
            </h2>
            <p className="mt-0.5 text-xs text-inkMuted">
              Публикуется на статус-страницах с этим монитором.
            </p>
            <form
              className="mt-3 grid gap-3"
              onSubmit={async (event) => {
                event.preventDefault();
                if (!text.trim()) return;
                if (
                  await act(
                    "note",
                    () =>
                      apiRequest(`/incidents/${id}/notes`, {
                        method: "POST",
                        body: { status, text: text.trim() },
                      }),
                    "Заметка опубликована",
                  )
                )
                  setText("");
              }}
            >
              <Segmented<NoteStatus>
                label="Статус"
                size="sm"
                value={status}
                onChange={setStatus}
                options={(Object.keys(NOTE_LABEL) as NoteStatus[]).map((value) => ({
                  value,
                  label: NOTE_LABEL[value],
                }))}
              />
              <TextArea
                label="Текст"
                value={text}
                maxLength={2000}
                onChange={(event) => setText(event.target.value)}
                placeholder="Что известно сейчас и когда ждать обновления"
              />
              <div>
                <Button
                  type="submit"
                  size="sm"
                  variant="primary"
                  loading={busy === "note"}
                  disabled={!text.trim()}
                >
                  Опубликовать
                </Button>
              </div>
            </form>
          </section>
        </div>
      </div>
    </>
  );
}
