import { useState } from "react";
import { Beaker, Plus, Trash2 } from "lucide-react";
import { apiRequest } from "../../api/client";
import type { Assertion, AssertionPreview } from "../../api/types";
import { errorMessage } from "../../api/errors";
import { Button } from "../../ui/Button";
import { Checkbox } from "../../ui/Controls";
import { DateField, TimeField } from "../../ui/DateTimeField";
import { Select } from "../../ui/Select";
import { ZONES } from "../../lib/zones";
import type { Window } from "./form-model";

const SOURCES = [
  { value: "json", label: "JSON", hint: "по JSONPath" },
  { value: "header", label: "Заголовок" },
  { value: "body", label: "Тело" },
] as const;
const OPERATIONS = [
  { value: "equals", label: "равно" },
  { value: "not_equals", label: "не равно" },
  { value: "contains", label: "содержит" },
  { value: "regex", label: "регулярное выражение" },
  { value: "exists", label: "существует" },
  { value: "gt", label: "больше" },
  { value: "lt", label: "меньше" },
] as const;
const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

export function AssertionsEditor({
  value,
  onChange,
  siteId,
}: {
  value: Assertion[];
  onChange: (next: Assertion[]) => void;
  siteId: number | null;
}) {
  const [preview, setPreview] = useState<AssertionPreview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const update = (index: number, patch: Partial<Assertion>) =>
    onChange(value.map((item, position) => (position === index ? { ...item, ...patch } : item)));
  const test = async () => {
    if (!siteId) return;
    setBusy(true);
    setProblem(null);
    try {
      setPreview(
        await apiRequest<AssertionPreview>(`/sites/${siteId}/assertions/preview`, {
          method: "POST",
          body: { assertions: value },
        }),
      );
    } catch (error) {
      setPreview(null);
      setProblem(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid gap-2">
      {value.map((item, index) => {
        const outcome = preview?.results.find((result) => result.index === index);
        return (
          <div
            key={index}
            className="grid gap-2 rounded border border-line bg-sidebar p-2.5 sm:grid-cols-[120px_minmax(0,1.3fr)_150px_minmax(0,1fr)_32px]"
          >
            <Select
              ariaLabel="Источник"
              size="sm"
              value={item.source ?? "json"}
              onChange={(source) => update(index, { source })}
              options={SOURCES.map((option) => ({ ...option }))}
            />
            <input
              aria-label="Путь"
              className="field min-h-8 py-1 font-mono text-[12.5px]"
              placeholder={
                item.source === "header"
                  ? "content-type"
                  : item.source === "body"
                    ? "н/д"
                    : "$.status"
              }
              disabled={item.source === "body"}
              value={item.path ?? ""}
              onChange={(event) => update(index, { path: event.target.value })}
            />
            <Select
              ariaLabel="Условие"
              size="sm"
              value={item.op ?? "equals"}
              onChange={(op) => update(index, { op })}
              options={OPERATIONS.map((option) => ({ ...option }))}
            />
            <input
              aria-label="Ожидаемое значение"
              className="field min-h-8 py-1 font-mono text-[12.5px]"
              placeholder="ok"
              disabled={item.op === "exists"}
              value={item.value ?? ""}
              onChange={(event) => update(index, { value: event.target.value })}
            />
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label="Удалить условие"
              onClick={() => onChange(value.filter((_, position) => position !== index))}
            >
              <Trash2 size={14} aria-hidden="true" />
            </Button>
            {outcome ? (
              <p
                className={`text-xs sm:col-span-5 ${outcome.passed ? "text-accent" : "text-down"}`}
                role="status"
              >
                {outcome.passed ? "✓ выполняется" : "✗ не выполняется"} · {outcome.message}
              </p>
            ) : null}
          </div>
        );
      })}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="subtle"
          disabled={value.length >= 10}
          onClick={() =>
            onChange([...value, { source: "json", path: "$.status", op: "equals", value: "ok" }])
          }
          icon={<Plus size={14} aria-hidden="true" />}
        >
          Условие
        </Button>
        {siteId && value.length ? (
          <Button
            size="sm"
            variant="subtle"
            loading={busy}
            onClick={() => void test()}
            icon={<Beaker size={14} aria-hidden="true" />}
          >
            Проверить на последнем ответе
          </Button>
        ) : null}
      </div>
      <p className="hint mt-0">
        JSONPath: <code className="mono">$.data.items[0].id</code>,{" "}
        <code className="mono">{"$['key']"}</code>, <code className="mono">$.items[*].state</code>.
        Регулярные выражения без вложенных квантификаторов.
      </p>
      {problem ? (
        <p className="hint-error" role="alert">
          {problem}
        </p>
      ) : null}
      {preview ? (
        <details className="rounded border border-line">
          <summary className="cursor-pointer px-3 py-2 text-xs text-inkMuted">
            Последний ответ: HTTP {preview.status_code ?? "н/д"}
          </summary>
          <pre className="mono max-h-48 overflow-auto border-t border-line p-3">
            {preview.body_sample || "пустое тело"}
          </pre>
        </details>
      ) : null}
    </div>
  );
}

export function HeadersEditor({
  value,
  onChange,
}: {
  value: { key: string; value: string }[];
  onChange: (next: { key: string; value: string }[]) => void;
}) {
  return (
    <div className="grid gap-2">
      {value.map((row, index) => (
        <div key={index} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_32px] gap-2">
          <input
            aria-label="Заголовок"
            className="field min-h-8 py-1 font-mono text-[12.5px]"
            placeholder="Authorization"
            value={row.key}
            onChange={(event) =>
              onChange(
                value.map((item, position) =>
                  position === index ? { ...item, key: event.target.value } : item,
                ),
              )
            }
          />
          <input
            aria-label="Значение"
            className="field min-h-8 py-1 font-mono text-[12.5px]"
            placeholder="Bearer …"
            value={row.value}
            onChange={(event) =>
              onChange(
                value.map((item, position) =>
                  position === index ? { ...item, value: event.target.value } : item,
                ),
              )
            }
          />
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label="Удалить заголовок"
            onClick={() => onChange(value.filter((_, position) => position !== index))}
          >
            <Trash2 size={14} aria-hidden="true" />
          </Button>
        </div>
      ))}
      <div>
        <Button
          size="sm"
          variant="subtle"
          onClick={() => onChange([...value, { key: "", value: "" }])}
          icon={<Plus size={14} aria-hidden="true" />}
        >
          Заголовок
        </Button>
      </div>
      <p className="hint mt-0">Значения шифруются и не возвращаются в ответах API.</p>
    </div>
  );
}

export function MaintenanceEditor({
  value,
  onChange,
}: {
  value: Window[];
  onChange: (next: Window[]) => void;
}) {
  const update = (index: number, next: Window) =>
    onChange(value.map((item, position) => (position === index ? next : item)));
  const today = new Date().toISOString().slice(0, 10);
  return (
    <div className="grid gap-3">
      {value.map((item, index) => (
        <div key={index} className="rounded border border-line bg-sidebar p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-medium text-inkMuted">
              {item.kind === "weekly" ? "Повторяющееся окно" : "Разовое окно"}
            </span>
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label="Удалить окно"
              onClick={() => onChange(value.filter((_, position) => position !== index))}
            >
              <Trash2 size={14} aria-hidden="true" />
            </Button>
          </div>
          {item.kind === "weekly" ? (
            <div className="grid gap-3">
              <div role="group" aria-label="Дни недели" className="flex flex-wrap gap-1">
                {WEEKDAYS.map((day, weekday) => (
                  <button
                    key={day}
                    type="button"
                    aria-pressed={item.weekdays.includes(weekday)}
                    onClick={() =>
                      update(index, {
                        ...item,
                        weekdays: item.weekdays.includes(weekday)
                          ? item.weekdays.filter((value) => value !== weekday)
                          : [...item.weekdays, weekday].sort(),
                      })
                    }
                    className={`h-8 w-9 rounded border text-xs ${item.weekdays.includes(weekday) ? "border-accent bg-accent/15 text-ink" : "border-line text-inkMuted hover:border-lineStrong"}`}
                  >
                    {day}
                  </button>
                ))}
              </div>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                <TimeField
                  label="С"
                  value={item.start}
                  onChange={(start) => update(index, { ...item, start })}
                />
                <TimeField
                  label="До"
                  value={item.end}
                  onChange={(end) => update(index, { ...item, end })}
                />
                <Select
                  label="Часовой пояс"
                  value={item.timezone}
                  onChange={(timezone) => update(index, { ...item, timezone })}
                  options={ZONES.map((zone) => ({ value: zone, label: zone }))}
                />
              </div>
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-4">
              <DateField
                label="Начало"
                value={item.date}
                onChange={(date) => update(index, { ...item, date })}
              />
              <TimeField
                label="Время"
                value={item.start}
                onChange={(start) => update(index, { ...item, start })}
              />
              <DateField
                label="Конец"
                value={item.endDate}
                onChange={(endDate) => update(index, { ...item, endDate })}
              />
              <TimeField
                label="Время"
                value={item.end}
                onChange={(end) => update(index, { ...item, end })}
              />
            </div>
          )}
        </div>
      ))}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="subtle"
          icon={<Plus size={14} aria-hidden="true" />}
          onClick={() =>
            onChange([
              ...value,
              {
                kind: "weekly",
                weekdays: [6],
                start: "03:00",
                end: "04:00",
                timezone: "Europe/Moscow",
              },
            ])
          }
        >
          Повторяющееся
        </Button>
        <Button
          size="sm"
          variant="subtle"
          icon={<Plus size={14} aria-hidden="true" />}
          onClick={() =>
            onChange([
              ...value,
              {
                kind: "once",
                date: today,
                start: "23:00",
                endDate: today,
                end: "23:59",
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
              },
            ])
          }
        >
          Разовое
        </Button>
      </div>
    </div>
  );
}

export function ThresholdsEditor({
  value,
  onChange,
}: {
  value: number[];
  onChange: (next: number[]) => void;
}) {
  const options = [60, 30, 14, 7, 3, 1];
  return (
    <div role="group" aria-label="Пороги оповещения" className="flex flex-wrap gap-1.5">
      {options.map((days) => (
        <Checkbox
          key={days}
          className="min-h-0 rounded border border-line px-2 py-1"
          checked={value.includes(days)}
          onChange={(next) => {
            const updated = next ? [...value, days] : value.filter((item) => item !== days);
            if (updated.length) onChange([...new Set(updated)].sort((a, b) => b - a).slice(0, 5));
          }}
          label={`${days} дн.`}
        />
      ))}
    </div>
  );
}
