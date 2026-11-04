import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Pin, Trash2 } from "lucide-react";
import { apiRequest } from "../../api/client";
import { errorMessage } from "../../api/errors";
import {
  invalidate,
  keys,
  useAnnouncements,
  useDaily,
  usePages,
  useSites,
  useSubscription,
} from "../../api/queries";
import type { PageCreate, PublicPage } from "../../api/types";
import { idempotencyKey } from "../../lib/ids";
import { siteTone } from "../../lib/status";
import { Button, ButtonLink } from "../../ui/Button";
import { Checkbox, Switch } from "../../ui/Controls";
import { CopyField, ErrorPanel, PlanLock, Skeleton } from "../../ui/Feedback";
import { FormError, TextArea, TextField } from "../../ui/Field";
import { notifyError, toast } from "../../ui/toast-store";
import { ACCENTS } from "./accents";
import type { AccentName } from "./accents";
import { StatusView } from "./StatusView";

function Announcements({ pageId }: { pageId: number }) {
  const list = useAnnouncements(pageId);
  const client = useQueryClient();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <section className="panel" aria-labelledby="ann-title">
      <h2 id="ann-title" className="section-title">
        Объявления
      </h2>
      <p className="mt-0.5 text-[13px] text-inkMuted">
        Закреплённые объявления видны над списком сервисов.
      </p>
      <ul className="mt-3 grid gap-2">
        {(list.data ?? []).map((item) => (
          <li
            key={item.id}
            className="flex items-start gap-3 rounded border border-line px-3 py-2 text-[13px]"
          >
            <Pin size={14} className="mt-0.5 text-info" aria-hidden="true" />
            <span className="min-w-0 flex-1">
              <span className="font-medium">{item.title}</span>
              <span className="block text-inkMuted">{item.body}</span>
            </span>
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label="Удалить объявление"
              onClick={async () => {
                try {
                  await apiRequest(`/status-pages/${pageId}/announcements/${item.id}`, {
                    method: "DELETE",
                  });
                  invalidate(client, keys.announcements(pageId));
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
      <form
        className="mt-3 grid gap-3"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!title.trim() || !body.trim()) return;
          setBusy(true);
          try {
            await apiRequest(`/status-pages/${pageId}/announcements`, {
              method: "POST",
              body: { title: title.trim(), body: body.trim(), pinned: true },
              idempotencyKey: idempotencyKey(),
            });
            setTitle("");
            setBody("");
            invalidate(client, keys.announcements(pageId));
            toast("Объявление опубликовано");
          } catch (error) {
            notifyError(error);
          } finally {
            setBusy(false);
          }
        }}
      >
        <TextField
          label="Заголовок"
          value={title}
          maxLength={150}
          onChange={(event) => setTitle(event.target.value)}
        />
        <TextArea
          label="Текст"
          value={body}
          maxLength={2000}
          onChange={(event) => setBody(event.target.value)}
        />
        <div>
          <Button type="submit" size="sm" loading={busy} disabled={!title.trim() || !body.trim()}>
            Опубликовать
          </Button>
        </div>
      </form>
    </section>
  );
}

export function StatusPageEditor() {
  const params = useParams();
  const editing = params.id !== undefined;
  const id = Number(params.id ?? 0);
  const navigate = useNavigate();
  const client = useQueryClient();
  const pages = usePages();
  const sites = useSites();
  const daily = useDaily(Boolean(sites.data?.length));
  const subscription = useSubscription();
  const existing = pages.data?.find((page) => page.id === id);
  const [form, setForm] = useState<PageCreate>({
    title: "",
    description: "",
    slug: null,
    site_ids: [],
    show_urls: false,
    logo_url: null,
    accent: "emerald",
    custom_domain: null,
  });
  const [ready, setReady] = useState(!editing);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [key] = useState(idempotencyKey);
  const customSlug = Boolean(subscription.data?.plan.features.custom_slug);

  useEffect(() => {
    if (editing && existing && !ready) {
      setForm({
        title: existing.title,
        description: existing.description,
        slug: existing.slug,
        site_ids: existing.site_ids,
        show_urls: existing.show_urls,
        logo_url: existing.logo_url,
        accent: existing.accent as AccentName,
        custom_domain: existing.custom_domain,
      });
      setReady(true);
    }
  }, [editing, existing, ready]);
  useEffect(() => {
    document.title = `${editing ? "Статус-страница" : "Новая статус-страница"}. pulsewatch`;
  }, [editing]);

  const preview = useMemo<PublicPage>(() => {
    const days = new Map((daily.data ?? []).map((entry) => [entry.site_id, entry.days]));
    const chosen = (sites.data ?? []).filter((site) => form.site_ids.includes(site.id));
    const down = chosen.filter((site) => siteTone(site) === "down").length;
    const status =
      chosen.length && down === chosen.length
        ? "major_outage"
        : down
          ? "partial_outage"
          : chosen.some((site) => site.degraded)
            ? "degraded"
            : "operational";
    const labels = {
      major_outage: "Серьёзный сбой",
      partial_outage: "Частичный сбой",
      degraded: "Снижена производительность",
      operational: "Все системы работают",
    } as const;
    return {
      slug: form.slug ?? "preview",
      title: form.title || "Название страницы",
      description: form.description ?? "",
      logo_url: form.logo_url ?? null,
      accent: form.accent ?? "emerald",
      overall: { status, label: labels[status] },
      announcements: [],
      sites: chosen.map((site) => {
        const own = days.get(site.id) ?? [];
        const total = own.reduce((sum, day) => sum + day.checks, 0);
        return {
          id: site.id,
          name: site.name,
          status: site.status,
          degraded: Boolean(site.degraded),
          maintenance: Boolean(site.maintenance_active),
          url: form.show_urls && site.monitor_type !== "heartbeat" ? site.url : null,
          uptime_30d: site.uptime_30d,
          uptime_90d: total
            ? own.reduce((sum, day) => sum + (day.uptime ?? 0) * day.checks, 0) / total
            : null,
          days: own,
        };
      }),
      incidents: [],
      updated_at: new Date().toISOString(),
    };
  }, [form, sites.data, daily.data]);

  if (editing && pages.isSuccess && !existing)
    return <ErrorPanel error={new Error("Статус-страница не найдена")} />;
  if (!ready || sites.isPending)
    return (
      <div className="grid gap-4" role="status" aria-label="Загрузка">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-96 rounded-lg" />
      </div>
    );

  const set = <K extends keyof PageCreate>(field: K, value: PageCreate[K]) =>
    setForm((current) => ({ ...current, [field]: value }));
  const save = async () => {
    if (!form.title.trim() || !form.site_ids.length) {
      setError("Укажите название и выберите хотя бы один монитор");
      return;
    }
    setBusy(true);
    setError(null);
    const body = {
      ...form,
      title: form.title.trim(),
      slug: customSlug ? form.slug || null : editing ? (existing?.slug ?? null) : null,
      custom_domain: customSlug ? form.custom_domain || null : null,
      logo_url: form.logo_url || null,
    };
    try {
      if (editing) {
        await apiRequest(`/status-pages/${id}`, { method: "PUT", body });
        toast("Страница сохранена");
      } else {
        const created = await apiRequest<{ id: number }>("/status-pages", {
          method: "POST",
          body,
          idempotencyKey: key,
        });
        toast("Страница создана");
        navigate(`/status-pages/${created.id}`, { replace: true });
      }
      invalidate(client, keys.pages);
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };
  const origin = window.location.origin;
  const slug = existing?.slug;

  return (
    <>
      <Link
        to="/status-pages"
        className="mb-3 inline-flex items-center gap-1.5 text-[13px] text-inkMuted hover:text-ink"
      >
        <ArrowLeft size={14} aria-hidden="true" />
        Статус-страницы
      </Link>
      <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <h1 className="page-title">{editing ? existing?.title : "Новая статус-страница"}</h1>
        <div className="flex gap-2">
          {slug ? (
            <ButtonLink to={`/public/${slug}`} target="_blank" size="sm">
              Открыть
            </ButtonLink>
          ) : null}
          <Button variant="primary" size="sm" loading={busy} onClick={() => void save()}>
            {editing ? "Сохранить" : "Создать"}
          </Button>
        </div>
      </header>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,460px)_minmax(0,1fr)]">
        <div className="grid min-w-0 content-start gap-5">
          <section className="panel grid gap-4" aria-label="Основное">
            <TextField
              label="Название"
              value={form.title}
              maxLength={150}
              onChange={(event) => set("title", event.target.value)}
            />
            <TextArea
              label="Описание"
              value={form.description ?? ""}
              maxLength={2000}
              onChange={(event) => set("description", event.target.value)}
            />
            {customSlug ? (
              <TextField
                label="Адрес"
                value={form.slug ?? ""}
                onChange={(event) => set("slug", event.target.value.toLowerCase())}
                hint={`${origin}/public/${form.slug || "…"}`}
                placeholder="status"
              />
            ) : (
              <PlanLock feature="Свой адрес и домен страницы" plan="Pro" />
            )}
            <TextField
              label="Логотип"
              type="url"
              placeholder="https://cdn.example.com/logo.svg"
              value={form.logo_url ?? ""}
              onChange={(event) => set("logo_url", event.target.value)}
              hint="Только https. Картинка загружается браузером посетителя."
            />
            <div>
              <p className="label" id="accent-label">
                Акцентный цвет
              </p>
              <div
                role="radiogroup"
                aria-labelledby="accent-label"
                className="flex flex-wrap gap-2"
              >
                {(Object.keys(ACCENTS) as AccentName[]).map((name) => (
                  <button
                    key={name}
                    type="button"
                    role="radio"
                    aria-checked={form.accent === name}
                    aria-label={ACCENTS[name].label}
                    onClick={() => set("accent", name)}
                    className={`h-8 w-8 rounded-full border-2 ${form.accent === name ? "border-ink" : "border-transparent"}`}
                    style={{ background: `rgb(${ACCENTS[name].rgb})` }}
                  />
                ))}
              </div>
            </div>
            <Switch
              label="Показывать адреса мониторов"
              description="По умолчанию адреса скрыты от посетителей"
              checked={Boolean(form.show_urls)}
              onChange={(value) => set("show_urls", value)}
            />
          </section>
          <section className="panel" aria-labelledby="sites-title">
            <h2 id="sites-title" className="section-title">
              Мониторы на странице
            </h2>
            <div className="mt-2 grid gap-0.5">
              {(sites.data ?? []).map((site) => (
                <Checkbox
                  key={site.id}
                  checked={form.site_ids.includes(site.id)}
                  onChange={(next) =>
                    set(
                      "site_ids",
                      next
                        ? [...form.site_ids, site.id]
                        : form.site_ids.filter((item) => item !== site.id),
                    )
                  }
                  label={site.name}
                />
              ))}
            </div>
          </section>
          {customSlug ? (
            <section className="panel grid gap-3" aria-labelledby="domain-title">
              <h2 id="domain-title" className="section-title">
                Свой домен
              </h2>
              <TextField
                label="Домен"
                placeholder="status.example.com"
                value={form.custom_domain ?? ""}
                onChange={(event) => set("custom_domain", event.target.value)}
              />
              <div className="rounded border border-line bg-sidebar p-3 text-[13px]">
                <p className="font-medium">Запись у регистратора</p>
                <p className="mono mt-1.5 break-all">
                  {form.custom_domain || "status.example.com"}. CNAME{" "}
                  {existing?.cname_target ?? "status.pulsewatch.dev"}.
                </p>
                <p className="mt-1.5 text-xs text-inkMuted">
                  Сертификат для домена выпускает ваш прокси или CDN: сервис только принимает имя.
                </p>
              </div>
            </section>
          ) : null}
          {slug ? (
            <section className="panel grid gap-3" aria-labelledby="share-title">
              <h2 id="share-title" className="section-title">
                Поделиться
              </h2>
              <CopyField label="Ссылка" value={`${origin}/public/${slug}`} />
              <div>
                <p className="label">Бейдж аптайма</p>
                <img
                  src={`/api/badge/${slug}.svg`}
                  alt="Бейдж аптайма"
                  height={20}
                  className="mb-2"
                />
                <CopyField
                  label="Markdown"
                  value={`[![uptime](${origin}/api/badge/${slug}.svg)](${origin}/public/${slug})`}
                />
              </div>
              <CopyField
                label="RSS инцидентов"
                value={`${origin}/api/public/pages/${slug}/feed.xml`}
              />
            </section>
          ) : null}
          {editing ? <Announcements pageId={id} /> : null}
          <FormError message={error} />
        </div>
        <section aria-label="Предпросмотр" className="min-w-0">
          <p className="eyebrow mb-2">Предпросмотр</p>
          <div className="rounded-lg border border-line bg-canvas p-5 sm:p-8">
            <StatusView page={preview} />
          </div>
        </section>
      </div>
    </>
  );
}
