import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Rss } from "lucide-react";
import { apiRequest } from "../../api/client";
import { errorMessage } from "../../api/errors";
import { usePublicPage } from "../../api/queries";
import type { components } from "../../api/schema";
import { Logo } from "../../app/Logo";
import { formatDuration, formatFullDateTime, formatPercent } from "../../lib/format";
import { Button } from "../../ui/Button";
import { ErrorPanel, Skeleton, Time } from "../../ui/Feedback";
import { StatusView } from "./StatusView";

function Subscribe({ slug }: { slug: string }) {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "busy" | "sent">("idle");
  const [error, setError] = useState<string | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setState("busy");
    setError(null);
    try {
      await apiRequest(`/public/pages/${encodeURIComponent(slug)}/subscribe`, {
        method: "POST",
        body: { email: email.trim() },
        authenticated: false,
      });
      setState("sent");
    } catch (problem) {
      setError(errorMessage(problem));
      setState("idle");
    }
  };
  if (state === "sent")
    return (
      <p className="text-[13px] text-inkMuted" role="status">
        Проверьте почту и подтвердите подписку.
      </p>
    );
  return (
    <form onSubmit={(event) => void submit(event)} className="flex flex-col gap-2 sm:flex-row">
      <label htmlFor="subscribe-email" className="sr-only">
        Электронная почта
      </label>
      <input
        id="subscribe-email"
        type="email"
        required
        className="field sm:max-w-xs"
        placeholder="you@example.com"
        value={email}
        onChange={(event) => setEmail(event.target.value)}
      />
      <Button type="submit" variant="primary" loading={state === "busy"}>
        Подписаться на обновления
      </Button>
      {error ? (
        <p className="hint-error sm:self-center" role="alert">
          {error}
        </p>
      ) : null}
    </form>
  );
}

export function PublicPage() {
  const slug = useParams().slug ?? "";
  const page = usePublicPage(slug);
  useEffect(() => {
    if (page.data) document.title = `${page.data.title}. Статус`;
  }, [page.data]);
  return (
    <div className="min-h-screen">
      <main className="mx-auto max-w-3xl px-4 py-10 sm:py-14">
        {page.isPending ? (
          <div className="grid gap-4" role="status" aria-label="Загрузка">
            <Skeleton className="h-8 w-60" />
            <Skeleton className="h-14 rounded-lg" />
            <Skeleton className="h-64 rounded-lg" />
          </div>
        ) : page.isError ? (
          <ErrorPanel
            error={page.error}
            onRetry={() => void page.refetch()}
            title="Статус-страница не найдена"
          />
        ) : (
          <StatusView
            page={page.data}
            footer={
              <footer className="mt-10 grid gap-5 border-t border-line pt-6">
                <Subscribe slug={slug} />
                <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-inkMuted">
                  <span>
                    Обновлено <Time value={page.data.updated_at} />
                  </span>
                  <span className="flex items-center gap-4">
                    <a
                      href={`/api/public/pages/${encodeURIComponent(slug)}/feed.xml`}
                      className="inline-flex items-center gap-1 hover:text-ink"
                    >
                      <Rss size={13} aria-hidden="true" /> RSS
                    </a>
                    <Link to="/" className="inline-flex items-center gap-1.5 hover:text-ink">
                      <Logo compact /> pulsewatch
                    </Link>
                  </span>
                </div>
              </footer>
            }
          />
        )}
      </main>
    </div>
  );
}

type SiteStatus = components["schemas"]["PublicStatusResponse"];

export function SitePublicPage() {
  const slug = useParams().slug ?? "";
  const status = useQuery({
    queryKey: ["public-site", slug],
    queryFn: () =>
      apiRequest<SiteStatus>(`/status/${encodeURIComponent(slug)}`, { authenticated: false }),
    refetchInterval: 60_000,
  });
  const data = status.data;
  return (
    <main className="mx-auto min-h-screen max-w-2xl px-4 py-12">
      {status.isPending ? (
        <Skeleton className="h-48 rounded-lg" />
      ) : status.isError ? (
        <ErrorPanel error={status.error} title="Страница статуса не найдена" />
      ) : data ? (
        <>
          <h1 className="page-title">{data.name}</h1>
          {data.url ? <p className="mt-1 text-[13px] text-inkMuted">{data.url}</p> : null}
          <div
            className={`mt-5 rounded-lg border px-4 py-3.5 text-[15px] font-semibold ${data.status === "up" ? "border-accent/30 bg-accent/10" : data.status === "down" ? "border-down/30 bg-down/10" : "border-line bg-panel"}`}
          >
            {data.status === "up"
              ? "Работает"
              : data.status === "down"
                ? "Недоступен"
                : "Нет данных"}
          </div>
          <p className="mt-4 text-[13px]">
            Аптайм за 30 дней: <b>{formatPercent(data.uptime_30d, 3)}</b>
          </p>
          <h2 className="section-title mt-8">Последние инциденты</h2>
          <ul className="mt-3 grid gap-2">
            {data.incidents.map((incident) => (
              <li
                key={incident.started_at}
                className="rounded border border-line px-3 py-2 text-[13px]"
              >
                {formatFullDateTime(incident.started_at)} ·{" "}
                {incident.ended_at ? formatDuration(incident.duration_seconds) : "продолжается"}
              </li>
            ))}
            {!data.incidents.length ? (
              <li className="text-[13px] text-inkMuted">Инцидентов не было.</li>
            ) : null}
          </ul>
        </>
      ) : null}
    </main>
  );
}
