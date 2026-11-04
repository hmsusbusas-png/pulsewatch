import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Download, FileUp } from "lucide-react";
import { apiRequest } from "../../api/client";
import { errorMessage } from "../../api/errors";
import { invalidate, keys } from "../../api/queries";
import type { ImportReport } from "../../api/types";
import { Button } from "../../ui/Button";
import { Segmented } from "../../ui/Controls";
import { FormError } from "../../ui/Field";
import { notifyError, toast } from "../../ui/toast-store";

type Format = "json" | "csv";

function previewRows(content: string, format: Format): string[][] {
  if (format === "csv") {
    return content
      .replace(/^\ufeff/, "")
      .split(/\r?\n/)
      .filter(Boolean)
      .slice(0, 11)
      .map((line) => line.split(","));
  }
  try {
    const parsed: unknown = JSON.parse(content);
    const list = Array.isArray(parsed) ? parsed : (parsed as { monitors?: unknown }).monitors;
    if (!Array.isArray(list)) return [];
    return [
      ["name", "url", "monitor_type", "interval_seconds"],
      ...list.slice(0, 10).map((item) => {
        const row = item as Record<string, unknown>;
        return [
          String(row.name ?? ""),
          String(row.url ?? ""),
          String(row.monitor_type ?? "http"),
          String(row.interval_seconds ?? ""),
        ];
      }),
    ];
  } catch {
    return [];
  }
}

export function TransferSection() {
  const client = useQueryClient();
  const [content, setContent] = useState("");
  const [file, setFile] = useState("");
  const [format, setFormat] = useState<Format>("csv");
  const [report, setReport] = useState<ImportReport | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rows = content ? previewRows(content, format) : [];

  const send = async (dry: boolean) => {
    setBusy(dry ? "dry" : "import");
    setError(null);
    try {
      const result = await apiRequest<ImportReport>("/sites/import", {
        method: "POST",
        body: { format, content, dry_run: dry },
      });
      setReport(result);
      if (!dry && result.created) {
        invalidate(client, keys.sites, keys.dashboard, keys.subscription);
        toast(`Импортировано мониторов: ${result.created}`);
      }
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(null);
    }
  };
  const download = async (kind: Format) => {
    setBusy(`export-${kind}`);
    try {
      const blob = await apiRequest<Blob>(`/sites/export?format=${kind}`, { responseType: "blob" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `pulsewatch-monitors.${kind}`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (problem) {
      notifyError(problem);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="grid gap-5">
      <section className="panel" aria-labelledby="export-title">
        <h2 id="export-title" className="section-title">
          Экспорт
        </h2>
        <p className="mt-0.5 text-[13px] text-inkMuted">
          Конфигурация мониторов без секретов: заголовки и токены не выгружаются. JSON подходит для
          импорта.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            size="sm"
            loading={busy === "export-json"}
            onClick={() => void download("json")}
            icon={<Download size={14} aria-hidden="true" />}
          >
            JSON
          </Button>
          <Button
            size="sm"
            loading={busy === "export-csv"}
            onClick={() => void download("csv")}
            icon={<Download size={14} aria-hidden="true" />}
          >
            CSV
          </Button>
        </div>
      </section>
      <section className="panel" aria-labelledby="import-title">
        <h2 id="import-title" className="section-title">
          Импорт
        </h2>
        <p className="mt-0.5 text-[13px] text-inkMuted">
          CSV со столбцами name, url, monitor_type, interval_seconds, tags (через «;»), group,
          keyword или JSON из экспорта. До 200 строк.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <label className="inline-flex min-h-8 cursor-pointer items-center gap-2 rounded border border-line bg-panelHover px-3 text-[13px] hover:border-lineStrong focus-within:ring-2 focus-within:ring-accent">
            <FileUp size={14} aria-hidden="true" />
            Выбрать файл
            <input
              type="file"
              accept=".csv,.json,text/csv,application/json"
              className="sr-only"
              onChange={async (event) => {
                const picked = event.target.files?.[0];
                if (!picked) return;
                if (picked.size > 512 * 1024) {
                  setError("Файл больше 512 КБ");
                  return;
                }
                setFile(picked.name);
                setFormat(picked.name.toLowerCase().endsWith(".json") ? "json" : "csv");
                setContent(await picked.text());
                setReport(null);
                setError(null);
              }}
            />
          </label>
          <span className="truncate text-xs text-inkMuted">{file || "файл не выбран"}</span>
          <Segmented<Format>
            label="Формат"
            size="sm"
            value={format}
            onChange={setFormat}
            options={[
              { value: "csv", label: "CSV" },
              { value: "json", label: "JSON" },
            ]}
          />
        </div>
        {rows.length ? (
          <div
            tabIndex={0}
            role="region"
            aria-label="Таблица"
            className="table-scroll mt-4 max-h-64 rounded border border-line"
          >
            <table className="table">
              <thead>
                <tr>
                  {rows[0].map((cell, index) => (
                    <th key={`${cell}-${index}`} scope="col">
                      {cell}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(1).map((row, index) => (
                  <tr key={index}>
                    {row.map((cell, position) => (
                      <td key={position} className="mono max-w-[220px] truncate">
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : content ? (
          <p className="hint-error">Не удалось показать предпросмотр. Проверьте формат файла.</p>
        ) : null}
        <div className="mt-4 flex flex-wrap gap-2">
          <Button
            size="sm"
            disabled={!content}
            loading={busy === "dry"}
            onClick={() => void send(true)}
          >
            Проверить
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={!content}
            loading={busy === "import"}
            onClick={() => void send(false)}
          >
            Импортировать
          </Button>
        </div>
        <div className="mt-3">
          <FormError message={error} />
        </div>
        {report ? (
          <div className="mt-4">
            <p className="text-[13px]" role="status">
              Создано: <b className="text-accent">{report.created}</b> · проверено без ошибок:{" "}
              <b>{report.valid}</b> · с ошибками:{" "}
              <b className={report.failed ? "text-down" : ""}>{report.failed}</b>
            </p>
            <ul className="mt-2 grid max-h-64 gap-1 overflow-y-auto">
              {report.rows.map((row) => (
                <li
                  key={row.row}
                  className="flex gap-3 rounded border border-line px-3 py-1.5 text-[13px]"
                >
                  <span className="w-10 shrink-0 text-inkMuted">#{row.row}</span>
                  <span className="min-w-0 flex-1 truncate">{row.name ?? "без названия"}</span>
                  <span className={row.status === "error" ? "text-down" : "text-accent"}>
                    {row.status === "error"
                      ? row.error
                      : row.status === "created"
                        ? "создан"
                        : "готов к импорту"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>
    </div>
  );
}
