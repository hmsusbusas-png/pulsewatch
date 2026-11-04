import { useEffect, useSyncExternalStore } from "react";
import { CheckCircle2, CircleAlert, Info, X } from "lucide-react";
import { currentToasts, dismissToast, subscribeToasts } from "./toast-store";
import type { Toast } from "./toast-store";

function ToastItem({ item }: { item: Toast }) {
  useEffect(() => {
    const timer = window.setTimeout(() => dismissToast(item.id), item.duration);
    return () => window.clearTimeout(timer);
  }, [item.id, item.duration]);
  const Icon = item.tone === "error" ? CircleAlert : item.tone === "info" ? Info : CheckCircle2;
  const color =
    item.tone === "error" ? "text-down" : item.tone === "info" ? "text-info" : "text-accent";
  return (
    <div
      role={item.tone === "error" ? "alert" : "status"}
      className="pointer-events-auto relative flex items-start gap-3 overflow-hidden rounded-lg border border-line bg-raised px-3.5 py-3 text-[13px] shadow-pop"
      style={{ animation: "rise 180ms ease-out" }}
    >
      <Icon size={17} className={`mt-px shrink-0 ${color}`} aria-hidden="true" />
      <div className="min-w-0 flex-1 leading-relaxed">{item.message}</div>
      {item.action ? (
        <button
          type="button"
          onClick={item.action.run}
          className="shrink-0 rounded px-2 py-0.5 text-[13px] font-semibold text-accent hover:bg-accent/10"
        >
          {item.action.label}
        </button>
      ) : null}
      <button
        type="button"
        onClick={() => dismissToast(item.id)}
        aria-label="Скрыть уведомление"
        className="-mr-1 shrink-0 rounded p-0.5 text-inkMuted hover:text-ink"
      >
        <X size={14} aria-hidden="true" />
      </button>
      {item.countdown ? (
        <span
          aria-hidden="true"
          className="absolute bottom-0 left-0 h-0.5 w-full origin-left bg-accent/70"
          style={{ animation: `shrink ${item.duration}ms linear forwards` }}
        />
      ) : null}
    </div>
  );
}

export function Toaster() {
  const items = useSyncExternalStore(subscribeToasts, currentToasts, currentToasts);
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed inset-x-3 bottom-[calc(env(safe-area-inset-bottom)+76px)] z-[60] flex flex-col gap-2 sm:inset-x-auto sm:bottom-4 sm:right-4 sm:w-[380px] lg:bottom-4"
    >
      {items.map((item) => (
        <ToastItem key={item.id} item={item} />
      ))}
    </div>
  );
}
