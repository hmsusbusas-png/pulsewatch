import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Plus, Search, Upload, X } from "lucide-react";
import { useDaily, useDashboard, useSites } from "../../api/queries";
import type { DayUptime, Site } from "../../api/types";
import { StatusRing } from "../../charts/Small";
import { countLabel, formatLatency, formatNumber, formatPercent } from "../../lib/format";
import { MONITOR_LABEL, siteTone } from "../../lib/status";
import type { Tone } from "../../lib/status";
import { ButtonLink, Button } from "../../ui/Button";
import { Checkbox, Segmented } from "../../ui/Controls";
import { EmptyState, ErrorPanel, Metric, Skeleton } from "../../ui/Feedback";
import { Select } from "../../ui/Select";
import { BulkBar } from "./BulkBar";
import { MonitorRow, ROW_GRID } from "./MonitorRow";
import { QuickStart } from "./QuickStart";

type StatusFilter = "all" | "problems" | "up" | "paused";
type Sort = "problems" | "name" | "latency" | "uptime";
const RANK: Record<Tone, number> = {
  down: 0,
  degraded: 1,
  unknown: 2,
  maintenance: 3,
  up: 4,
  paused: 5,
};

function Summary({ sites }: { sites: Site[] }) {
  const dashboard = useDashboard();
  const tones = sites.map(siteTone);
  const count = (tone: Tone) => tones.filter((item) => item === tone).length;
  const down = count("down");
  const degraded = count("degraded");
  const open = dashboard.data?.open_incidents ?? down;
  const measured = sites.filter((site) => site.uptime_30d !== null);
  const uptime = measured.length
    ? measured.reduce((sum, site) => sum + (site.uptime_30d ?? 0), 0) / measured.length
    : null;
  const headline =
    down > 0
      ? `${countLabel(open, "инцидент", "инцидента", "инцидентов")} · ${countLabel(down, "монитор недоступен", "монитора недоступны", "мониторов недоступны")}`
      : degraded > 0
        ? `Все системы доступны, ${countLabel(degraded, "монитор отвечает", "монитора отвечают", "мониторов отвечают")} медленно`
        : "Все системы работают";
  return (
    <section className="panel mb-5 flex flex-col gap-5 lg:flex-row lg:items-center">
      <div className="flex min-w-0 flex-1 items-center gap-5">
        <StatusRing
          label={`Состояние: работают ${count("up")}, недоступны ${down}, медленно ${degraded}, на паузе ${count("paused")}`}
          slices={[
            { value: count("up"), color: "--accent", label: "up" },
            { value: degraded, color: "--warn", label: "degraded" },
            { value: down, color: "--down", label: "down" },
            {
              value: count("paused") + count("unknown") + count("maintenance"),
              color: "--unknown",
              label: "other",
            },
          ]}
        />
        <div className="min-w-0">
          <p className="eyebrow">Сейчас</p>
          <h1
            className={`mt-1 text-[22px] font-semibold leading-tight tracking-[-0.03em] sm:text-[26px] ${down ? "text-down" : degraded ? "text-warn" : "text-ink"}`}
          >
            {headline}
          </h1>
          <p className="mt-1 text-[13px] text-inkMuted">
            {countLabel(sites.length, "монитор", "монитора", "мониторов")} под наблюдением
          </p>
        </div>
      </div>
      <dl className="grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-4 lg:w-[520px]">
        <Metric label="Работают" value={formatNumber(count("up"))} tone="text-accent" />
        <Metric
          label="Открытые инциденты"
          value={formatNumber(open)}
          tone={open ? "text-down" : ""}
        />
        <Metric label="Аптайм за 30 дней" value={formatPercent(uptime)} />
        <Metric label="Средний отклик" value={formatLatency(dashboard.data?.average_response_ms)} />
      </dl>
    </section>
  );
}

function RowsSkeleton() {
  return (
    <div className="surface" aria-label="Загрузка мониторов" role="status">
      {Array.from({ length: 6 }, (_, index) => (
        <div key={index} className={`border-b border-line px-4 py-3.5 last:border-b-0 ${ROW_GRID}`}>
          <Skeleton className="h-4 w-4" />
          <div className="grid gap-1.5">
            <Skeleton className="h-3.5 w-40" />
            <Skeleton className="h-3 w-28" />
          </div>
          <Skeleton className="col-span-2 col-start-2 row-start-2 h-6 md:col-span-1 md:col-start-auto md:row-start-auto" />
          <Skeleton className="h-3.5 w-12 justify-self-end" />
          <Skeleton className="hidden h-6 xl:block" />
          <Skeleton className="hidden h-3.5 w-14 justify-self-end md:block" />
          <Skeleton className="hidden h-3.5 w-16 justify-self-end md:block" />
        </div>
      ))}
    </div>
  );
}

export function MonitorsPage() {
  useEffect(() => {
    document.title = "Журнал мониторов. pulsewatch";
  }, []);
  const sites = useSites();
  const daily = useDaily(Boolean(sites.data?.length));
  const [params, setParams] = useSearchParams();
  const query = params.get("q") ?? "";
  const status = (params.get("status") as StatusFilter | null) ?? "all";
  const type = params.get("type") ?? "";
  const tag = params.get("tag") ?? "";
  const group = params.get("group") ?? "";
  const sort = (params.get("sort") as Sort | null) ?? "problems";
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const anchor = useRef<number | null>(null);

  const setParam = useCallback(
    (key: string, value: string) =>
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          if (
            value &&
            !(key === "status" && value === "all") &&
            !(key === "sort" && value === "problems")
          )
            next.set(key, value);
          else next.delete(key);
          return next;
        },
        { replace: true },
      ),
    [setParams],
  );

  const days = useMemo(() => {
    const map = new Map<number, DayUptime[]>();
    for (const entry of daily.data ?? []) map.set(entry.site_id, entry.days);
    return map;
  }, [daily.data]);

  const all = useMemo(() => sites.data ?? [], [sites.data]);
  const tags = useMemo(() => [...new Set(all.flatMap((site) => site.tags ?? []))].sort(), [all]);
  const groups = useMemo(
    () =>
      [
        ...new Set(
          all.map((site) => site.group).filter((value): value is string => Boolean(value)),
        ),
      ].sort(),
    [all],
  );
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const list = all.filter((site) => {
      const tone = siteTone(site);
      if (
        needle &&
        !`${site.name} ${site.url} ${(site.tags ?? []).join(" ")}`.toLowerCase().includes(needle)
      )
        return false;
      if (status === "problems" && !["down", "degraded"].includes(tone)) return false;
      if (status === "up" && tone !== "up") return false;
      if (status === "paused" && tone !== "paused") return false;
      if (type && site.monitor_type !== type) return false;
      if (tag && !(site.tags ?? []).includes(tag)) return false;
      if (group && site.group !== group) return false;
      return true;
    });
    return [...list].sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name, "ru");
      if (sort === "latency") return (b.latest_response_ms ?? -1) - (a.latest_response_ms ?? -1);
      if (sort === "uptime") return (a.uptime_30d ?? 101) - (b.uptime_30d ?? 101);
      return RANK[siteTone(a)] - RANK[siteTone(b)] || a.name.localeCompare(b.name, "ru");
    });
  }, [all, query, status, type, tag, group, sort]);

  const onSelect = useCallback(
    (index: number, event: MouseEvent | React.KeyboardEvent, next: boolean) => {
      const start = event.shiftKey ? anchor.current : null;
      setSelected((current) => {
        const result = new Set(current);
        if (start !== null) {
          const [from, to] = [Math.min(start, index), Math.max(start, index)];
          for (let position = from; position <= to; position += 1) {
            const site = visible[position];
            if (site) {
              if (next) result.add(site.id);
              else result.delete(site.id);
            }
          }
        } else if (next) result.add(visible[index].id);
        else result.delete(visible[index].id);
        return result;
      });
      anchor.current = index;
    },
    [visible],
  );

  if (sites.isPending) {
    return (
      <>
        <Skeleton className="mb-5 h-[120px] rounded-lg" />
        <RowsSkeleton />
      </>
    );
  }
  if (sites.isError)
    return (
      <ErrorPanel
        error={sites.error}
        onRetry={() => void sites.refetch()}
        title="Не удалось загрузить мониторы"
      />
    );
  if (all.length === 0) return <QuickStart />;

  const chosen = all.filter((site) => selected.has(site.id));
  const allVisibleSelected = visible.length > 0 && visible.every((site) => selected.has(site.id));
  const someSelected = visible.some((site) => selected.has(site.id));
  const chips = [
    query && { key: "q", label: `«${query}»` },
    status !== "all" && {
      key: "status",
      label: { problems: "Проблемы", up: "Работают", paused: "На паузе" }[
        status as Exclude<StatusFilter, "all">
      ],
    },
    type && { key: "type", label: MONITOR_LABEL[type] ?? type },
    tag && { key: "tag", label: `#${tag}` },
    group && { key: "group", label: group },
  ].filter((chip): chip is { key: string; label: string } => Boolean(chip));

  return (
    <>
      <Summary sites={all} />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1 sm:max-w-xs">
          <Search
            size={15}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-inkMuted"
            aria-hidden="true"
          />
          <input
            data-search
            type="search"
            aria-label="Фильтр мониторов по имени, адресу или тегу"
            placeholder="Фильтр…  /"
            className="field min-h-9 py-1.5 pl-9"
            value={query}
            onChange={(event) => setParam("q", event.target.value)}
          />
        </div>
        <Segmented<StatusFilter>
          label="Состояние"
          size="sm"
          value={status}
          onChange={(value) => setParam("status", value)}
          options={[
            { value: "all", label: "Все" },
            { value: "problems", label: "Проблемы" },
            { value: "up", label: "Работают" },
            { value: "paused", label: "Пауза" },
          ]}
        />
        <Select
          ariaLabel="Тип монитора"
          size="sm"
          className="w-[150px]"
          value={type}
          onChange={(value) => setParam("type", value)}
          options={[
            { value: "", label: "Все типы" },
            ...Object.entries(MONITOR_LABEL).map(([value, label]) => ({ value, label })),
          ]}
        />
        {tags.length ? (
          <Select
            ariaLabel="Тег"
            size="sm"
            className="w-[130px]"
            value={tag}
            onChange={(value) => setParam("tag", value)}
            options={[
              { value: "", label: "Все теги" },
              ...tags.map((value) => ({ value, label: `#${value}` })),
            ]}
          />
        ) : null}
        {groups.length ? (
          <Select
            ariaLabel="Группа"
            size="sm"
            className="w-[150px]"
            value={group}
            onChange={(value) => setParam("group", value)}
            options={[
              { value: "", label: "Все группы" },
              ...groups.map((value) => ({ value, label: value })),
            ]}
          />
        ) : null}
        <Select<Sort>
          ariaLabel="Сортировка"
          size="sm"
          className="w-[170px]"
          value={sort}
          onChange={(value) => setParam("sort", value)}
          options={[
            { value: "problems", label: "Сначала проблемы" },
            { value: "name", label: "По названию" },
            { value: "latency", label: "По отклику" },
            { value: "uptime", label: "По аптайму" },
          ]}
        />
        <div className="ml-auto flex gap-2">
          <ButtonLink
            to="/settings?tab=import"
            size="sm"
            variant="ghost"
            icon={<Upload size={14} aria-hidden="true" />}
          >
            Импорт
          </ButtonLink>
          <ButtonLink
            to="/sites/new"
            size="sm"
            variant="primary"
            className="sm:hidden"
            icon={<Plus size={14} aria-hidden="true" />}
          >
            Монитор
          </ButtonLink>
        </div>
      </div>
      {chips.length ? (
        <div className="mb-3 flex flex-wrap items-center gap-1.5" aria-label="Активные фильтры">
          {chips.map((chip) => (
            <span key={chip.key} className="chip">
              {chip.label}
              <button
                type="button"
                aria-label={`Убрать фильтр ${chip.label}`}
                className="-mr-1 rounded-full p-0.5 text-inkMuted hover:text-ink"
                onClick={() => setParam(chip.key, "")}
              >
                <X size={12} aria-hidden="true" />
              </button>
            </span>
          ))}
          <Button size="xs" variant="ghost" onClick={() => setParams({}, { replace: true })}>
            Сбросить
          </Button>
          <span className="ml-1 text-xs text-inkMuted">
            Показано {visible.length} из {all.length}
          </span>
        </div>
      ) : null}
      <section className="surface overflow-hidden" aria-label="Мониторы">
        <div
          className={`hidden border-b border-line px-4 py-2 text-xs text-inkMuted md:grid ${ROW_GRID}`}
        >
          <Checkbox
            checked={allVisibleSelected ? true : someSelected ? "mixed" : false}
            ariaLabel="Выбрать все показанные"
            onChange={(next) =>
              setSelected(next ? new Set(visible.map((site) => site.id)) : new Set())
            }
          />
          <span>Монитор</span>
          <span>Аптайм по дням, 90 дней</span>
          <span className="text-right">30 дней</span>
          <span className="hidden xl:block">Отклик, 24 точки</span>
          <span className="text-right">Последний</span>
          <span className="text-right">Проверка</span>
        </div>
        {visible.length ? (
          visible.map((site, index) => (
            <MonitorRow
              key={site.id}
              site={site}
              index={index}
              days={days.get(site.id)}
              selected={selected.has(site.id)}
              onSelect={onSelect}
            />
          ))
        ) : (
          <EmptyState
            title="Ничего не найдено"
            action={
              <Button size="sm" onClick={() => setParams({}, { replace: true })}>
                Сбросить фильтры
              </Button>
            }
          >
            Под условия фильтра не подошёл ни один монитор.
          </EmptyState>
        )}
      </section>
      <p className="mt-3 text-xs text-inkMuted">
        Shift + клик по флажку выделяет диапазон. Экспорт и импорт конфигурации доступны в{" "}
        <Link className="underline hover:text-ink" to="/settings?tab=import">
          настройках
        </Link>
        .
      </p>
      {chosen.length ? <BulkBar selected={chosen} onClear={() => setSelected(new Set())} /> : null}
    </>
  );
}
