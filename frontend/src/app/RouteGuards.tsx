import { Navigate, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "../auth/auth-context";
import { Skeleton } from "../ui/Feedback";

function Checking() {
  return (
    <div
      className="mx-auto grid max-w-3xl gap-4 px-4 py-16"
      role="status"
      aria-label="Проверяем сессию"
    >
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-40 rounded-lg" />
    </div>
  );
}

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "checking") return <Checking />;
  if (status === "anonymous")
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return <>{children}</>;
}

export function PublicOnlyRoute({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  if (status === "checking") return <Checking />;
  if (status === "authenticated") return <Navigate to="/" replace />;
  return <>{children}</>;
}
