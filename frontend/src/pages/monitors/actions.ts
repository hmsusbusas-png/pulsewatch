import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { apiRequest } from "../../api/client";
import { invalidate, keys } from "../../api/queries";
import type { CheckNow, Site } from "../../api/types";
import { forgetMonitor } from "../../features/recent";
import { deferRemoval, notifyError, toast } from "../../ui/toast-store";

export function useDeferredSiteDelete() {
  const client = useQueryClient();
  const navigate = useNavigate();
  return (site: Site, leaveTo?: string) => {
    let snapshot: Site[] | undefined;
    deferRemoval({
      message: `Монитор «${site.name}» удалён`,
      hide: () => {
        snapshot = client.getQueryData<Site[]>(keys.sites);
        client.setQueryData<Site[]>(keys.sites, (sites) =>
          sites?.filter((item) => item.id !== site.id),
        );
        if (leaveTo) navigate(leaveTo);
      },
      restore: () => {
        if (snapshot) client.setQueryData(keys.sites, snapshot);
      },
      commit: async () => {
        await apiRequest(`/sites/${site.id}`, { method: "DELETE", keepalive: true });
        forgetMonitor(site.id);
        client.removeQueries({ queryKey: keys.site(site.id) });
        invalidate(client, keys.dashboard, keys.daily, keys.subscription);
      },
    });
  };
}

export async function requestCheck(site: Site): Promise<CheckNow | null> {
  try {
    const result = await apiRequest<CheckNow>(`/sites/${site.id}/check-now`, { method: "POST" });
    toast(`Проверка «${site.name}» запущена`, { tone: "info", duration: 2500 });
    return result;
  } catch (error) {
    notifyError(error);
    return null;
  }
}

export function suggestName(url: string): string {
  try {
    const host = new URL(url.includes("://") ? url : `https://${url}`).hostname.replace(
      /^www\./,
      "",
    );
    return host.length > 60 ? host.slice(0, 60) : host;
  } catch {
    return url.slice(0, 60);
  }
}
