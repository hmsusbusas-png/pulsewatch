import { createContext, useContext } from "react";

export type AuthStatus = "checking" | "authenticated" | "anonymous";

export type AuthContextValue = {
  status: AuthStatus;
  isAuthenticated: boolean;
  signIn: (accessToken: string) => void;
  signOut: () => Promise<void>;
};

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return context;
}
