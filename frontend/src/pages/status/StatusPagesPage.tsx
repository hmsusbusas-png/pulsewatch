import { useEffect } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Globe, Plus, Trash2 } from "lucide-react";
import { apiRequest } from "../../api/client";
import { invalidate, keys, usePages } from "../../api/queries";
import type { StatusPage } from "../../api/types";
import { countLabel } from "../../lib/format";
import { Button, ButtonLink } from "../../ui/Button";
import { EmptyState, ErrorPanel, Skeleton } from "../../ui/Feedback";
import { deferRemoval } from "../../ui/toast-store";

export function StatusPagesPage() {
  const pages = usePages();
  const client = useQueryClient();
  useEffect(() => {
    document.title = "Статус-страницы. pulsewatch";
  }, []);
  const remove = (page: StatusPage) => {
    const before = client.getQueryData<StatusPage[]>(keys.pages);
    deferRemoval({
      message: `Страница «${page.title}» удалена`,
      hide: () =>
        client.setQueryData<StatusPage[]>(keys.pages, (list) =>
          list?.filter((item) => item.id !== page.id),
        ),
      restore: () => client.setQueryData(keys.pages, before),
      commit: async () => {
        await apiRequest(`/status-pages/${page.id}`, { method: "DELETE", keepalive: true });
        invalidate(client, keys.pages);
      },
    });
  };
  return (
    <>
      <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">Статус-страницы</h1>
          <p className="mt-1 text-[13px] text-inkMuted">
            Публичная страница доступности для клиентов: 90 дней истории, инциденты и подписка.
          </p>
        </div>
        <ButtonLink
          to="/status-pages/new"
          variant="primary"
          icon={<Plus size={15} aria-hidden="true" />}
        >
          Новая страница
        </ButtonLink>
      </header>
      {pages.isError ? (
        <ErrorPanel error={pages.error} onRetry={() => void pages.refetch()} />
      ) : null}
      {pages.isPending ? (
        <div className="grid gap-3 sm:grid-cols-2" role="status" aria-label="Загрузка">
          {[1, 2].map((item) => (
            <Skeleton key={item} className="h-36 rounded-lg" />
          ))}
        </div>
      ) : pages.data?.length ? (
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {pages.data.map((page) => (
            <li key={page.id} className="panel flex flex-col">
              <div className="flex items-start gap-3">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded border border-line bg-panelHover text-inkMuted">
                  <Globe size={16} aria-hidden="true" />
                </span>
                <div className="min-w-0 flex-1">
                  <Link
                    to={`/status-pages/${page.id}`}
                    className="block truncate text-[14px] font-semibold hover:text-accent"
                  >
                    {page.title}
                  </Link>
                  <p className="truncate text-xs text-inkMuted">
                    /{page.slug}
                    {page.custom_domain ? ` · ${page.custom_domain}` : ""}
                  </p>
                </div>
              </div>
              <p className="mt-3 line-clamp-2 min-h-[2.5em] text-[13px] text-inkMuted">
                {page.description || "Без описания"}
              </p>
              <p className="mt-3 text-xs text-inkMuted">
                {countLabel(page.site_ids.length, "монитор", "монитора", "мониторов")} ·{" "}
                {countLabel(page.subscribers, "подписчик", "подписчика", "подписчиков")}
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                <ButtonLink to={`/status-pages/${page.id}`} size="sm">
                  Редактировать
                </ButtonLink>
                <a
                  href={`/public/${page.slug}`}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="inline-flex min-h-8 items-center gap-1.5 rounded px-2.5 text-[13px] text-inkMuted hover:bg-panelHover hover:text-ink"
                >
                  Открыть <ExternalLink size={13} aria-hidden="true" />
                </a>
                <Button
                  size="sm"
                  variant="ghost"
                  className="ml-auto"
                  aria-label={`Удалить «${page.title}»`}
                  onClick={() => remove(page)}
                  icon={<Trash2 size={14} aria-hidden="true" />}
                />
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <section className="surface">
          <EmptyState
            icon={<Globe size={18} aria-hidden="true" />}
            title="Страниц ещё нет"
            action={
              <ButtonLink to="/status-pages/new" variant="primary">
                Создать страницу
              </ButtonLink>
            }
          >
            Статус-страница снимает поток вопросов «у вас всё работает?»: клиенты видят состояние
            сервисов, историю за 90 дней и заметки по инцидентам, могут подписаться на обновления.
          </EmptyState>
        </section>
      )}
    </>
  );
}
