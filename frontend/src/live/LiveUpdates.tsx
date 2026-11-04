import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { apiRequest } from "../api/client";
import { invalidate, keys, patchSite } from "../api/queries";
import type { Site } from "../api/types";
import { announce, flashSite, onAnnounce, setLiveState } from "./live-store";

type CheckCompleted = {
  site_id: number;
  ok: boolean;
  status: Site["status"];
  status_code: number | null;
  response_ms: number | null;
  checked_at: string;
};

type SiteUpdated = { site_id: number; deleted?: boolean; created?: boolean } & Partial<Site>;

const MAX_ATTEMPTS = 8;

function applyCheck(client: QueryClient, event: CheckCompleted) {
  let changed = false;
  let name = "";
  patchSite(client, event.site_id, (site) => {
    changed = changed || site.status !== event.status;
    name = site.name;
    const series =
      event.ok && event.response_ms !== null
        ? [...site.response_series, { t: event.checked_at, ms: event.response_ms }].slice(-24)
        : site.response_series;
    return {
      ...site,
      status: event.status,
      last_checked_at: event.checked_at,
      latest_response_ms: event.ok ? event.response_ms : site.latest_response_ms,
      consecutive_failures: event.ok ? 0 : site.consecutive_failures + 1,
      response_series: series,
    };
  });
  if (changed) {
    flashSite(event.site_id);
    announce(
      `${name}: ${event.status === "down" ? "недоступен" : event.status === "up" ? "снова работает" : "статус изменился"}`,
    );
    invalidate(client, keys.dashboard, keys.siteIncidents(event.site_id), ["incidents"]);
  }
  void client.invalidateQueries({
    queryKey: ["site", event.site_id, "checks"],
    refetchType: "active",
  });
}

function applySite(client: QueryClient, event: SiteUpdated) {
  if (event.deleted) {
    client.setQueryData<Site[]>(keys.sites, (sites) =>
      sites?.filter((site) => site.id !== event.site_id),
    );
    return;
  }
  if (event.created) {
    invalidate(client, keys.sites, keys.dashboard);
    return;
  }
  const { site_id: id, ...fields } = event;
  const known = Object.fromEntries(
    Object.entries(fields).filter(([key]) =>
      ["name", "paused", "status", "url", "interval_seconds", "tags", "group"].includes(key),
    ),
  ) as Partial<Site>;
  patchSite(client, id, (site) => ({ ...site, ...known }));
  if ("status" in known || "paused" in known) flashSite(id);
}

export function LiveUpdates() {
  const client = useQueryClient();
  const attempts = useRef(0);
  const [epoch, setEpoch] = useState(0);

  useEffect(() => {
    let source: EventSource | null = null;
    let timer = 0;
    let disposed = false;

    const connect = async () => {
      setLiveState("connecting");
      try {
        const { token } = await apiRequest<{ token: string }>("/stream/token", { method: "POST" });
        if (disposed) return;
        source = new EventSource(`/api/stream?token=${encodeURIComponent(token)}`);
      } catch {
        retry();
        return;
      }
      source.addEventListener("ready", () => {
        attempts.current = 0;
        setLiveState("live");
      });
      source.addEventListener("check_completed", (message) =>
        applyCheck(client, JSON.parse((message as MessageEvent<string>).data) as CheckCompleted),
      );
      source.addEventListener("site_updated", (message) =>
        applySite(client, JSON.parse((message as MessageEvent<string>).data) as SiteUpdated),
      );
      for (const kind of ["incident_opened", "incident_resolved"]) {
        source.addEventListener(kind, (message) => {
          const data = JSON.parse((message as MessageEvent<string>).data) as {
            site_id: number;
            kind: string;
          };
          if (data.kind === "degraded")
            patchSite(client, data.site_id, (site) => ({
              ...site,
              degraded: kind === "incident_opened",
            }));
          flashSite(data.site_id);
          invalidate(
            client,
            keys.dashboard,
            keys.siteIncidents(data.site_id),
            ["incidents"],
            keys.site(data.site_id),
          );
        });
      }
      source.addEventListener("subscription_changed", () =>
        invalidate(client, keys.subscription, keys.plans),
      );
      source.addEventListener("resync", () => void client.invalidateQueries());
      source.addEventListener("close", () => {
        source?.close();
        retry();
      });
      source.onerror = () => {
        source?.close();
        retry();
      };
    };

    const retry = () => {
      if (disposed) return;
      attempts.current += 1;
      if (attempts.current > MAX_ATTEMPTS) {
        setLiveState("paused");
        return;
      }
      setLiveState("paused");
      const delay = Math.min(30_000, 1000 * 2 ** (attempts.current - 1)) + Math.random() * 500;
      timer = window.setTimeout(() => void connect(), delay);
    };

    void connect();
    const resume = () => {
      if (attempts.current > MAX_ATTEMPTS && document.visibilityState === "visible") {
        attempts.current = 0;
        setEpoch((value) => value + 1);
      }
    };
    window.addEventListener("online", resume);
    document.addEventListener("visibilitychange", resume);
    return () => {
      disposed = true;
      window.clearTimeout(timer);
      source?.close();
      setLiveState("offline");
      window.removeEventListener("online", resume);
      document.removeEventListener("visibilitychange", resume);
    };
  }, [client, epoch]);

  return null;
}

export function LiveAnnouncer() {
  const [text, setText] = useState("");
  useEffect(() => onAnnounce(setText), []);
  return (
    <div aria-live="polite" aria-atomic="true" className="sr-only">
      {text}
    </div>
  );
}
