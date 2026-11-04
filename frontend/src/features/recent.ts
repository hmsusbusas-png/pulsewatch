const KEY = "pw.recent";

export type Recent = { id: number; name: string };

export function recentMonitors(): Recent[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(parsed)
      ? parsed.filter(
          (item): item is Recent =>
            typeof item === "object" &&
            item !== null &&
            typeof (item as Recent).id === "number" &&
            typeof (item as Recent).name === "string",
        )
      : [];
  } catch {
    return [];
  }
}

export function rememberMonitor(item: Recent) {
  const next = [item, ...recentMonitors().filter((entry) => entry.id !== item.id)].slice(0, 5);
  localStorage.setItem(KEY, JSON.stringify(next));
}

export function forgetMonitor(id: number) {
  localStorage.setItem(KEY, JSON.stringify(recentMonitors().filter((entry) => entry.id !== id)));
}
