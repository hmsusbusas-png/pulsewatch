import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { AlertCircle, Check, Copy, RotateCcw } from "lucide-react";
import { ApiError, errorMessage } from "../api/errors";
import { formatFullDateTime, formatRelative } from "../lib/format";
import { TONE_DOT, TONE_LABEL, TONE_PILL } from "../lib/status";
import type { Tone } from "../lib/status";
import { Button, ButtonLink } from "./Button";
import { Tooltip } from "./Tooltip";

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />;
}

export function ErrorPanel({
  error,
  onRetry,
  title = "Не удалось загрузить",
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
}) {
  const limit = error instanceof ApiError && error.code.startsWith("plan_");
  return (
    <div
      role="alert"
      className="flex flex-wrap items-start gap-3 rounded-lg border border-down/30 bg-down/[0.06] px-4 py-3"
    >
      <AlertCircle size={18} className="mt-0.5 shrink-0 text-down" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{title}</p>
        <p className="mt-0.5 text-[13px] text-inkMuted">{errorMessage(error)}</p>
      </div>
      {limit ? (
        <ButtonLink to="/pricing" size="sm">
          Тарифы
        </ButtonLink>
      ) : null}
      {onRetry ? (
        <Button size="sm" onClick={onRetry} icon={<RotateCcw size={14} aria-hidden="true" />}>
          Повторить
        </Button>
      ) : null}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon?: ReactNode;
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center px-4 py-10 text-center">
      {icon ? (
        <span className="mb-3 flex h-11 w-11 items-center justify-center rounded-full border border-line bg-panelHover text-inkMuted">
          {icon}
        </span>
      ) : null}
      <p className="text-[15px] font-semibold">{title}</p>
      <div className="mt-1.5 max-w-md text-[13px] leading-relaxed text-inkMuted">{children}</div>
      {action ? <div className="mt-4 flex flex-wrap justify-center gap-2">{action}</div> : null}
    </div>
  );
}

export function StatusDot({ tone, pulse }: { tone: Tone; pulse?: boolean }) {
  return (
    <span
      className="relative inline-flex h-2.5 w-2.5 shrink-0 items-center justify-center"
      aria-hidden="true"
    >
      {pulse && tone === "down" ? (
        <span
          className="absolute inset-0 rounded-full bg-down"
          style={{ animation: "ping 1.8s cubic-bezier(0,0,0.2,1) infinite" }}
        />
      ) : null}
      <span className={`status-dot h-2.5 w-2.5 ${TONE_DOT[tone]}`} />
    </span>
  );
}

export function StatusPill({ tone, label }: { tone: Tone; label?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${TONE_PILL[tone]}`}
    >
      <span className={`status-dot h-1.5 w-1.5 ${TONE_DOT[tone]}`} aria-hidden="true" />
      {label ?? TONE_LABEL[tone]}
    </span>
  );
}

function useNow(interval = 30_000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), interval);
    return () => window.clearInterval(timer);
  }, [interval]);
  return now;
}

export function Time({
  value,
  className = "",
}: {
  value: string | null | undefined;
  className?: string;
}) {
  const now = useNow();
  if (!value) return <span className={`text-inkMuted ${className}`}>нет данных</span>;
  return (
    <Tooltip content={formatFullDateTime(value)}>
      <time dateTime={value} tabIndex={0} className={`whitespace-nowrap ${className}`}>
        {formatRelative(value, now)}
      </time>
    </Tooltip>
  );
}

export function CopyButton({ value, label = "Копировать" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      size="sm"
      variant="subtle"
      aria-label={label}
      onClick={() => {
        void navigator.clipboard.writeText(value).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        });
      }}
      icon={copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
    >
      {copied ? "Скопировано" : "Копировать"}
    </Button>
  );
}

export function CopyField({ value, label }: { value: string; label: string }) {
  return (
    <div className="min-w-0">
      <p className="label">{label}</p>
      <div className="flex min-w-0 items-center gap-2 rounded border border-line bg-sidebar py-1 pl-3 pr-1">
        <code className="mono min-w-0 flex-1 truncate text-ink" title={value}>
          {value}
        </code>
        <CopyButton value={value} label={`Копировать: ${label}`} />
      </div>
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}

export function Metric({
  label,
  value,
  hint,
  tone = "",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: string;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-inkMuted">{label}</dt>
      <dd className={`mt-1 truncate text-xl font-semibold tracking-[-0.02em] ${tone}`}>{value}</dd>
      {hint ? <dd className="mt-0.5 truncate text-xs text-inkMuted">{hint}</dd> : null}
    </div>
  );
}

export function PlanLock({ feature, plan = "Business" }: { feature: string; plan?: string }) {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-dashed border-lineStrong px-4 py-3">
      <p className="min-w-0 flex-1 text-[13px] text-inkMuted">
        {feature} доступно на тарифе {plan}.
      </p>
      <ButtonLink to="/pricing" size="sm">
        Сравнить тарифы
      </ButtonLink>
    </div>
  );
}
