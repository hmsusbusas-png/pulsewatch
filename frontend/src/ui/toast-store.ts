import type { ReactNode } from "react";
import { errorMessage } from "../api/errors";

export type Tone = "success" | "error" | "info";
export type Toast = {
  id: number;
  tone: Tone;
  message: ReactNode;
  action?: { label: string; run: () => void };
  duration: number;
  countdown?: boolean;
  createdAt: number;
};
let toasts: Toast[] = [];
let sequence = 0;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

export function dismissToast(id: number) {
  toasts = toasts.filter((toast) => toast.id !== id);
  emit();
}

export function toast(
  message: ReactNode,
  options: Partial<Pick<Toast, "tone" | "action" | "duration" | "countdown">> = {},
): number {
  sequence += 1;
  const item: Toast = {
    id: sequence,
    tone: options.tone ?? "success",
    message,
    action: options.action,
    duration: options.duration ?? (options.tone === "error" ? 7000 : 4000),
    countdown: options.countdown,
    createdAt: Date.now(),
  };
  toasts = [...toasts.slice(-3), item];
  emit();
  return item.id;
}

export function notifyError(error: unknown) {
  return toast(errorMessage(error), { tone: "error" });
}

export function deferRemoval({
  message,
  hide,
  restore,
  commit,
  delay = 7000,
}: {
  message: ReactNode;
  hide: () => void;
  restore: () => void;
  commit: () => Promise<unknown>;
  delay?: number;
}) {
  let settled = false;
  const finish = () => {
    if (settled) return;
    settled = true;
    window.clearTimeout(timer);
    window.removeEventListener("pagehide", finish);
    commit().catch((error: unknown) => {
      restore();
      notifyError(error);
    });
  };
  hide();
  const id = toast(message, {
    tone: "info",
    duration: delay,
    countdown: true,
    action: {
      label: "Отменить",
      run: () => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        window.removeEventListener("pagehide", finish);
        restore();
        dismissToast(id);
        toast("Удаление отменено", { tone: "info", duration: 2500 });
      },
    },
  });
  const timer = window.setTimeout(finish, delay);
  window.addEventListener("pagehide", finish);
}

export function subscribeToasts(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function currentToasts() {
  return toasts;
}
