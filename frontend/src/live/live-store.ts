import { useSyncExternalStore } from "react";

export type LiveState = "connecting" | "live" | "paused" | "offline";

let state: LiveState = "offline";
const stateListeners = new Set<() => void>();

export function setLiveState(next: LiveState) {
  if (next === state) return;
  state = next;
  for (const listener of stateListeners) listener();
}

export function useLiveState(): LiveState {
  return useSyncExternalStore(
    (listener) => {
      stateListeners.add(listener);
      return () => stateListeners.delete(listener);
    },
    () => state,
    () => state,
  );
}

const flashes = new Map<number, number>();
const flashListeners = new Map<number, Set<() => void>>();

export function flashSite(id: number) {
  flashes.set(id, Date.now());
  for (const listener of flashListeners.get(id) ?? []) listener();
}

export function useFlash(id: number): number {
  return useSyncExternalStore(
    (listener) => {
      const set = flashListeners.get(id) ?? new Set();
      set.add(listener);
      flashListeners.set(id, set);
      return () => set.delete(listener);
    },
    () => flashes.get(id) ?? 0,
    () => 0,
  );
}

const announcements = new Set<(text: string) => void>();

export function announce(text: string) {
  for (const listener of announcements) listener(text);
}

export function onAnnounce(listener: (text: string) => void) {
  announcements.add(listener);
  return () => {
    announcements.delete(listener);
  };
}
