import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  apiRequest,
  getAccessToken,
  onSessionExpired,
  refreshAccessToken,
  setAccessToken,
} from "../api/client";
import { AuthContext } from "./auth-context";
import type { AuthContextValue, AuthStatus } from "./auth-context";
import { queryClient } from "../api/query-client";

const HINT = "pw.session";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>(() =>
    getAccessToken() ? "authenticated" : localStorage.getItem(HINT) ? "checking" : "anonymous",
  );

  useEffect(() => {
    let active = true;

    onSessionExpired(() => {
      localStorage.removeItem(HINT);
      setAccessToken(null);
      queryClient.clear();
      setStatus("anonymous");
    });

    if (!getAccessToken() && localStorage.getItem(HINT)) {
      refreshAccessToken()
        .then((token) => {
          if (!token) localStorage.removeItem(HINT);
          if (active) {
            setStatus(token ? "authenticated" : "anonymous");
          }
        })
        .catch(() => {
          if (active) {
            setStatus("anonymous");
          }
        });
    }

    return () => {
      active = false;
      onSessionExpired(null);
    };
  }, []);

  const signIn = useCallback((accessToken: string) => {
    localStorage.setItem(HINT, "1");
    queryClient.clear();
    setAccessToken(accessToken);
    setStatus("authenticated");
  }, []);

  const signOut = useCallback(async () => {
    try {
      await apiRequest("/auth/logout", { method: "POST" });
    } catch {
      setStatus("anonymous");
    } finally {
      localStorage.removeItem(HINT);
      setAccessToken(null);
      queryClient.clear();
      setStatus("anonymous");
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      isAuthenticated: status === "authenticated",
      signIn,
      signOut,
    }),
    [status, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
