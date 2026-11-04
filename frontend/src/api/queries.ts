import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import type { QueryClient, QueryKey } from "@tanstack/react-query";
import { apiRequest } from "./client";
import type {
  AdminStats,
  AdminUser,
  Announcement,
  ApiKey,
  AuditEntry,
  Channel,
  Check,
  Dashboard,
  Delivery,
  IncidentDetail,
  IncidentListItem,
  Incident,
  Payment,
  Plan,
  PublicPage,
  Region,
  Rule,
  Session,
  Site,
  SiteDays,
  SiteUpdate,
  Slo,
  Stats,
  StatsRange,
  StatusPage,
  Subscription,
  TelegramStatus,
  Template,
  User,
} from "./types";

export const keys = {
  me: ["me"] as const,
  sites: ["sites"] as const,
  site: (id: number) => ["site", id] as const,
  stats: (id: number, range: StatsRange) => ["site", id, "stats", range] as const,
  slo: (id: number) => ["site", id, "slo"] as const,
  checks: (id: number, range: StatsRange) => ["site", id, "checks", range] as const,
  siteIncidents: (id: number) => ["site", id, "incidents"] as const,
  siteDaily: (id: number) => ["site", id, "daily"] as const,
  daily: ["daily"] as const,
  dashboard: ["dashboard"] as const,
  incidents: (state: string) => ["incidents", state] as const,
  incident: (id: number) => ["incident", id] as const,
  channels: ["channels"] as const,
  deliveries: (id: number) => ["channel", id, "deliveries"] as const,
  escalations: ["escalations"] as const,
  templates: ["templates"] as const,
  telegram: ["telegram"] as const,
  pages: ["pages"] as const,
  announcements: (id: number) => ["page", id, "announcements"] as const,
  publicPage: (slug: string) => ["public", slug] as const,
  regions: ["regions"] as const,
  subscription: ["billing", "subscription"] as const,
  plans: ["plans"] as const,
  payments: ["billing", "payments"] as const,
  sessions: ["account", "sessions"] as const,
  audit: (type: string) => ["account", "audit", type] as const,
  apiKeys: ["keys"] as const,
  adminStats: ["admin", "stats"] as const,
  adminUsers: (search: string) => ["admin", "users", search] as const,
};

async function fetchAll<T extends { id: number }>(path: string, limit = 100): Promise<T[]> {
  const items: T[] = [];
  let cursor = 0;
  for (let page = 0; page < 20; page += 1) {
    const separator = path.includes("?") ? "&" : "?";
    const batch = await apiRequest<T[]>(`${path}${separator}cursor=${cursor}&limit=${limit}`);
    items.push(...batch);
    if (batch.length < limit) break;
    cursor = batch[batch.length - 1].id;
  }
  return items;
}

export function useMe() {
  return useQuery({ queryKey: keys.me, queryFn: () => apiRequest<User>("/me") });
}

export function useSites() {
  return useQuery({ queryKey: keys.sites, queryFn: () => fetchAll<Site>("/sites") });
}

export function useSite(id: number) {
  return useQuery({
    queryKey: keys.site(id),
    queryFn: () => apiRequest<Site>(`/sites/${id}`),
    enabled: id > 0,
  });
}

export function useStats(id: number, range: StatsRange) {
  return useQuery({
    queryKey: keys.stats(id, range),
    queryFn: () => apiRequest<Stats>(`/sites/${id}/stats?range=${range}`),
    enabled: id > 0,
    placeholderData: keepPreviousData,
  });
}

export function useSlo(id: number, enabled = true) {
  return useQuery({
    queryKey: keys.slo(id),
    queryFn: () => apiRequest<Slo>(`/sites/${id}/slo`),
    enabled: id > 0 && enabled,
  });
}

export function useChecks(id: number, range: StatsRange, failedOnly = false) {
  return useInfiniteQuery({
    queryKey: [...keys.checks(id, range), failedOnly],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      apiRequest<Check[]>(
        `/sites/${id}/checks?range=${range}&order=desc&limit=50&cursor=${pageParam}${failedOnly ? "&failed=true" : ""}`,
      ),
    getNextPageParam: (last) => (last.length === 50 ? last[last.length - 1].id : undefined),
    enabled: id > 0,
  });
}

export function useSiteIncidents(id: number) {
  return useQuery({
    queryKey: keys.siteIncidents(id),
    queryFn: () => apiRequest<Incident[]>(`/sites/${id}/incidents?limit=50`),
    enabled: id > 0,
  });
}

export function useSiteDaily(id: number) {
  return useQuery({
    queryKey: keys.siteDaily(id),
    queryFn: () => apiRequest<SiteDays>(`/sites/${id}/daily?days=90`),
    enabled: id > 0,
  });
}

export function useDaily(enabled = true) {
  return useQuery({
    queryKey: keys.daily,
    queryFn: () => apiRequest<SiteDays[]>("/sites/daily?days=90"),
    enabled,
    staleTime: 120_000,
  });
}

export function useDashboard() {
  return useQuery({
    queryKey: keys.dashboard,
    queryFn: () => apiRequest<Dashboard>("/dashboard/summary"),
  });
}

export function useIncidents(state: "all" | "open" | "resolved") {
  return useInfiniteQuery({
    queryKey: keys.incidents(state),
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      apiRequest<IncidentListItem[]>(
        `/incidents?limit=50&cursor=${pageParam}${state === "all" ? "" : `&state=${state}`}`,
      ),
    getNextPageParam: (last) => (last.length === 50 ? last[last.length - 1].id : undefined),
  });
}

export function useIncident(id: number) {
  return useQuery({
    queryKey: keys.incident(id),
    queryFn: () => apiRequest<IncidentDetail>(`/incidents/${id}`),
    enabled: id > 0,
  });
}

export function useChannels() {
  return useQuery({ queryKey: keys.channels, queryFn: () => fetchAll<Channel>("/channels") });
}

export function useDeliveries(id: number, enabled: boolean) {
  return useQuery({
    queryKey: keys.deliveries(id),
    queryFn: () => apiRequest<Delivery[]>(`/channels/${id}/deliveries`),
    enabled,
  });
}

export function useEscalations() {
  return useQuery({
    queryKey: keys.escalations,
    queryFn: () => apiRequest<Rule[]>("/escalations"),
  });
}

export function useTemplates() {
  return useQuery({
    queryKey: keys.templates,
    queryFn: () => apiRequest<Template[]>("/templates"),
  });
}

export function useTelegram() {
  return useQuery({
    queryKey: keys.telegram,
    queryFn: () => apiRequest<TelegramStatus>("/telegram/status"),
  });
}

export function usePages() {
  return useQuery({ queryKey: keys.pages, queryFn: () => fetchAll<StatusPage>("/status-pages") });
}

export function useAnnouncements(id: number) {
  return useQuery({
    queryKey: keys.announcements(id),
    queryFn: () => apiRequest<Announcement[]>(`/status-pages/${id}/announcements`),
    enabled: id > 0,
  });
}

export function usePublicPage(slug: string) {
  return useQuery({
    queryKey: keys.publicPage(slug),
    queryFn: () =>
      apiRequest<PublicPage>(`/public/pages/${encodeURIComponent(slug)}`, {
        authenticated: false,
      }),
    refetchInterval: 60_000,
  });
}

export function useRegions() {
  return useQuery({
    queryKey: keys.regions,
    queryFn: () => apiRequest<Region[]>("/regions"),
    staleTime: 60_000,
  });
}

export function useSubscription(enabled = true) {
  return useQuery({
    queryKey: keys.subscription,
    queryFn: () => apiRequest<Subscription>("/billing/subscription"),
    enabled,
  });
}

export function usePlans() {
  return useQuery({
    queryKey: keys.plans,
    queryFn: () => apiRequest<Plan[]>("/plans", { authenticated: false }),
    staleTime: 600_000,
  });
}

export function usePayments() {
  return useQuery({
    queryKey: keys.payments,
    queryFn: () => apiRequest<Payment[]>("/billing/payments"),
  });
}

export function useSessions() {
  return useQuery({
    queryKey: keys.sessions,
    queryFn: () => fetchAll<Session>("/account/sessions"),
  });
}

export function useAudit(type: string) {
  return useInfiniteQuery({
    queryKey: keys.audit(type),
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      apiRequest<AuditEntry[]>(
        `/account/audit?limit=30&cursor=${pageParam}${type ? `&resource_type=${type}` : ""}`,
      ),
    getNextPageParam: (last) => (last.length === 30 ? last[last.length - 1].id : undefined),
  });
}

export function useApiKeys(enabled: boolean) {
  return useQuery({
    queryKey: keys.apiKeys,
    queryFn: () => apiRequest<ApiKey[]>("/keys"),
    enabled,
  });
}

export function useAdminStats(enabled: boolean) {
  return useQuery({
    queryKey: keys.adminStats,
    queryFn: () => apiRequest<AdminStats>("/admin/stats"),
    enabled,
    refetchInterval: 30_000,
  });
}

export function useAdminUsers(search: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.adminUsers(search),
    queryFn: () =>
      apiRequest<AdminUser[]>(`/admin/users?limit=100&search=${encodeURIComponent(search)}`),
    enabled,
    placeholderData: keepPreviousData,
  });
}

export function patchSite(client: QueryClient, id: number, patch: (site: Site) => Site) {
  client.setQueryData<Site[]>(keys.sites, (sites) =>
    sites?.map((site) => (site.id === id ? patch(site) : site)),
  );
  client.setQueryData<Site>(keys.site(id), (site) => (site ? patch(site) : site));
}

export function invalidate(client: QueryClient, ...queryKeys: QueryKey[]) {
  for (const queryKey of queryKeys) void client.invalidateQueries({ queryKey });
}

export function useUpdateSite(id: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: SiteUpdate) =>
      apiRequest<Site>(`/sites/${id}`, { method: "PATCH", body: payload }),
    onMutate: async (payload) => {
      await client.cancelQueries({ queryKey: keys.site(id) });
      const previousList = client.getQueryData<Site[]>(keys.sites);
      const previous = client.getQueryData<Site>(keys.site(id));
      const optimistic: Partial<Site> = {};
      if (payload.paused !== undefined && payload.paused !== null)
        optimistic.paused = payload.paused;
      if (payload.name) optimistic.name = payload.name;
      if (Object.keys(optimistic).length)
        patchSite(client, id, (site) => ({ ...site, ...optimistic }));
      return { previousList, previous };
    },
    onError: (_error, _payload, context) => {
      if (context?.previousList) client.setQueryData(keys.sites, context.previousList);
      if (context?.previous) client.setQueryData(keys.site(id), context.previous);
    },
    onSuccess: (site) => {
      patchSite(client, id, () => site);
      invalidate(client, keys.dashboard);
    },
  });
}

export function useEnvironment() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => apiRequest<{ environment?: string }>("/health", { authenticated: false }),
    staleTime: Infinity,
    select: (data) => data.environment ?? "production",
  });
}
