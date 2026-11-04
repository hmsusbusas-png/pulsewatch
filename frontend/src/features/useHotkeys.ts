import { useEffect, useRef } from "react";

export type Bindings = Record<string, () => void>;

function typing(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) ||
    target.getAttribute("role") === "listbox" ||
    target.closest("dialog[open]") !== null
  );
}

export function useHotkeys(bindings: Bindings, enabled = true) {
  const latest = useRef(bindings);
  latest.current = bindings;
  useEffect(() => {
    if (!enabled) return;
    let prefix = "";
    let timer = 0;
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        latest.current["mod+k"]?.();
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey || typing(event.target)) return;
      const key = event.key === "?" ? "?" : event.key.toLowerCase();
      const combo = prefix ? `${prefix} ${key}` : key;
      window.clearTimeout(timer);
      if (latest.current[combo]) {
        event.preventDefault();
        prefix = "";
        latest.current[combo]();
        return;
      }
      if (!prefix && Object.keys(latest.current).some((name) => name.startsWith(`${key} `))) {
        prefix = key;
        timer = window.setTimeout(() => {
          prefix = "";
        }, 1200);
        return;
      }
      prefix = "";
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.clearTimeout(timer);
    };
  }, [enabled]);
}
