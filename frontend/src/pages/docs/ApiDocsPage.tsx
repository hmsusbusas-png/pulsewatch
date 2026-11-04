import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { ErrorPanel, Skeleton } from "../../ui/Feedback";
import { CopyButton } from "../../ui/Feedback";

type Parameter = {
  name: string;
  in: string;
  required?: boolean;
  description?: string;
  schema?: { type?: string; enum?: string[]; default?: unknown };
};
type Operation = {
  summary?: string;
  description?: string;
  tags?: string[];
  parameters?: Parameter[];
  requestBody?: { content?: Record<string, { schema?: { $ref?: string } }> };
  responses?: Record<string, { description?: string }>;
};
type Spec = {
  info: { title: string; version: string; description?: string };
  tags?: { name: string; description?: string }[];
  paths: Record<string, Record<string, Operation>>;
};

const METHOD_COLOR: Record<string, string> = {
  get: "text-info",
  post: "text-accent",
  patch: "text-warn",
  put: "text-warn",
  delete: "text-down",
};

function curl(method: string, path: string, operation: Operation) {
  const origin = window.location.origin;
  const query = (operation.parameters ?? [])
    .filter((item) => item.in === "query" && item.required)
    .map((item) => `${item.name}=…`)
    .join("&");
  const url = `${origin}${path}${query ? `?${query}` : ""}`;
  const parts = [
    `curl -X ${method.toUpperCase()} "${url}"`,
    '  -H "Authorization: Bearer $PULSEWATCH_KEY"',
  ];
  if (operation.requestBody) parts.push('  -H "Content-Type: application/json"', "  -d '{…}'");
  return parts.join(" \\\n");
}

export function ApiDocsPage() {
  const spec = useQuery({
    queryKey: ["openapi"],
    queryFn: async () => {
      const response = await fetch("/openapi.json");
      if (!response.ok) throw new Error("Контракт API недоступен");
      return (await response.json()) as Spec;
    },
    staleTime: Infinity,
  });
  const [query, setQuery] = useState("");
  useEffect(() => {
    document.title = "Документация API. pulsewatch";
  }, []);
  const groups = useMemo(() => {
    if (!spec.data) return [];
    const needle = query.trim().toLowerCase();
    return (spec.data.tags ?? [])
      .filter((tag) => tag.name !== "dev")
      .map((tag) => ({
        ...tag,
        operations: Object.entries(spec.data.paths).flatMap(([path, methods]) =>
          Object.entries(methods)
            .filter(([, operation]) => operation.tags?.includes(tag.name))
            .filter(
              ([method, operation]) =>
                !needle ||
                `${method} ${path} ${operation.summary ?? ""}`.toLowerCase().includes(needle),
            )
            .map(([method, operation]) => ({ method, path, operation })),
        ),
      }))
      .filter((group) => group.operations.length);
  }, [spec.data, query]);

  if (spec.isPending) return <Skeleton className="h-96 rounded-lg" />;
  if (spec.isError) return <ErrorPanel error={spec.error} onRetry={() => void spec.refetch()} />;
  return (
    <div className="grid gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
      <nav aria-label="Разделы API" className="hidden lg:block">
        <div className="sticky top-20 grid gap-0.5 text-[13px]">
          {groups.map((group) => (
            <a
              key={group.name}
              href={`#${group.name.replace(/\s+/g, "-")}`}
              className="rounded px-2 py-1 capitalize text-inkMuted hover:bg-panelHover hover:text-ink"
            >
              {group.name}
            </a>
          ))}
        </div>
      </nav>
      <div className="min-w-0">
        <h1 className="page-title">{spec.data.info.title}</h1>
        <p className="mt-1 text-xs text-inkMuted">
          Версия контракта {spec.data.info.version} ·{" "}
          <a href="/openapi.json" className="underline" download>
            openapi.json
          </a>
        </p>
        <details className="panel mt-4">
          <summary className="cursor-pointer text-[13px] font-medium">
            Аутентификация, версии, пагинация, ошибки
          </summary>
          <div className="mt-3 whitespace-pre-wrap text-[13px] leading-relaxed text-inkMuted">
            {spec.data.info.description}
          </div>
        </details>
        <div className="relative mt-5">
          <Search
            size={15}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-inkMuted"
            aria-hidden="true"
          />
          <input
            type="search"
            aria-label="Найти эндпоинт"
            placeholder="Найти эндпоинт: stats, heartbeat, POST…"
            className="field pl-9"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        {groups.map((group) => (
          <section
            key={group.name}
            id={group.name.replace(/\s+/g, "-")}
            className="mt-8 scroll-mt-20"
            aria-labelledby={`tag-${group.name}`}
          >
            <h2 id={`tag-${group.name}`} className="section-title capitalize">
              {group.name}
            </h2>
            {group.description ? (
              <p className="mt-0.5 text-[13px] text-inkMuted">{group.description}</p>
            ) : null}
            <div className="mt-3 grid gap-2">
              {group.operations.map(({ method, path, operation }) => (
                <details key={`${method}-${path}`} className="surface">
                  <summary className="flex cursor-pointer items-center gap-3 px-4 py-2.5 text-[13px]">
                    <span
                      className={`mono w-14 shrink-0 font-semibold uppercase ${METHOD_COLOR[method] ?? ""}`}
                    >
                      {method}
                    </span>
                    <span className="mono min-w-0 flex-1 truncate">{path}</span>
                    <span className="hidden truncate text-inkMuted sm:block">
                      {operation.summary}
                    </span>
                  </summary>
                  <div className="grid gap-3 border-t border-line px-4 py-3 text-[13px]">
                    {operation.description ? (
                      <p className="whitespace-pre-wrap text-inkMuted">{operation.description}</p>
                    ) : null}
                    {operation.parameters?.length ? (
                      <div tabIndex={0} role="region" aria-label="Таблица" className="table-scroll">
                        <table className="table">
                          <thead>
                            <tr>
                              <th scope="col">Параметр</th>
                              <th scope="col">Где</th>
                              <th scope="col">Тип</th>
                              <th scope="col">Описание</th>
                            </tr>
                          </thead>
                          <tbody>
                            {operation.parameters.map((parameter) => (
                              <tr key={`${parameter.in}-${parameter.name}`}>
                                <td className="mono">
                                  {parameter.name}
                                  {parameter.required ? <span className="text-down">*</span> : null}
                                </td>
                                <td className="text-inkMuted">{parameter.in}</td>
                                <td className="mono text-inkMuted">
                                  {parameter.schema?.enum
                                    ? parameter.schema.enum.join(" | ")
                                    : (parameter.schema?.type ?? "н/д")}
                                </td>
                                <td className="text-inkMuted">{parameter.description ?? ""}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : null}
                    {operation.requestBody?.content?.["application/json"]?.schema?.$ref ? (
                      <p className="text-inkMuted">
                        Тело:{" "}
                        <span className="mono text-ink">
                          {operation.requestBody.content["application/json"].schema.$ref
                            .split("/")
                            .pop()}
                        </span>
                      </p>
                    ) : null}
                    <p className="flex flex-wrap gap-2">
                      {Object.entries(operation.responses ?? {}).map(([code, response]) => (
                        <span key={code} className="chip" title={response.description}>
                          {code}
                        </span>
                      ))}
                    </p>
                    <div className="relative">
                      <pre className="mono overflow-x-auto rounded border border-line bg-sidebar p-3 pr-28">
                        {curl(method, path, operation)}
                      </pre>
                      <span className="absolute right-2 top-2">
                        <CopyButton value={curl(method, path, operation)} label="Копировать curl" />
                      </span>
                    </div>
                  </div>
                </details>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
