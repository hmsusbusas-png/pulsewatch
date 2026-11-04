import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import { AppShell } from "./app/AppShell";
import { ProtectedRoute, PublicOnlyRoute } from "./app/RouteGuards";
import { AuthPage } from "./pages/auth/AuthPage";
import { MonitorsPage } from "./pages/monitors/MonitorsPage";
import { Toaster } from "./ui/Toaster";
import { Skeleton } from "./ui/Feedback";

const MonitorPage = lazy(() =>
  import("./pages/monitors/MonitorPage").then((module) => ({ default: module.MonitorPage })),
);
const MonitorForm = lazy(() =>
  import("./pages/monitors/MonitorForm").then((module) => ({ default: module.MonitorForm })),
);
const IncidentsPage = lazy(() =>
  import("./pages/incidents/IncidentsPage").then((module) => ({ default: module.IncidentsPage })),
);
const IncidentPage = lazy(() =>
  import("./pages/incidents/IncidentPage").then((module) => ({ default: module.IncidentPage })),
);
const ChannelsPage = lazy(() =>
  import("./pages/channels/ChannelsPage").then((module) => ({ default: module.ChannelsPage })),
);
const StatusPagesPage = lazy(() =>
  import("./pages/status/StatusPagesPage").then((module) => ({ default: module.StatusPagesPage })),
);
const StatusPageEditor = lazy(() =>
  import("./pages/status/StatusPageEditor").then((module) => ({
    default: module.StatusPageEditor,
  })),
);
const PublicPage = lazy(() =>
  import("./pages/status/PublicPage").then((module) => ({ default: module.PublicPage })),
);
const SitePublicPage = lazy(() =>
  import("./pages/status/PublicPage").then((module) => ({ default: module.SitePublicPage })),
);
const PricingPage = lazy(() =>
  import("./pages/billing/BillingPages").then((module) => ({ default: module.PricingPage })),
);
const DevPaymentPage = lazy(() =>
  import("./pages/billing/BillingPages").then((module) => ({ default: module.DevPaymentPage })),
);
const BillingReturnPage = lazy(() =>
  import("./pages/billing/BillingPages").then((module) => ({ default: module.BillingReturnPage })),
);
const SettingsPage = lazy(() =>
  import("./pages/settings/SettingsPage").then((module) => ({ default: module.SettingsPage })),
);
const KeysPage = lazy(() =>
  import("./pages/docs/KeysPage").then((module) => ({ default: module.KeysPage })),
);
const ApiDocsPage = lazy(() =>
  import("./pages/docs/ApiDocsPage").then((module) => ({ default: module.ApiDocsPage })),
);
const AdminPage = lazy(() =>
  import("./pages/admin/AdminPage").then((module) => ({ default: module.AdminPage })),
);
const NotFound = lazy(() =>
  import("./pages/NotFound").then((module) => ({ default: module.NotFound })),
);

function PageFallback() {
  return (
    <div className="grid gap-4 p-4" role="status" aria-label="Загрузка страницы">
      <Skeleton className="h-8 w-56" />
      <Skeleton className="h-40 rounded-lg" />
      <Skeleton className="h-64 rounded-lg" />
    </div>
  );
}

export function App() {
  return (
    <>
      <Toaster />
      <Suspense fallback={<PageFallback />}>
        <Routes>
          <Route path="/public/:slug" element={<PublicPage />} />
          <Route path="/status/:slug" element={<SitePublicPage />} />
          <Route path="/pricing" element={<PricingPage />} />
          <Route
            path="/login"
            element={
              <PublicOnlyRoute>
                <AuthPage key="login" mode="login" />
              </PublicOnlyRoute>
            }
          />
          <Route
            path="/register"
            element={
              <PublicOnlyRoute>
                <AuthPage key="register" mode="register" />
              </PublicOnlyRoute>
            }
          />
          <Route path="/forgot-password" element={<AuthPage key="forgot" mode="forgot" />} />
          <Route path="/account/signup" element={<AuthPage key="signup" mode="signup" />} />
          <Route path="/account/reset" element={<AuthPage key="reset" mode="reset" />} />
          <Route path="/account/email" element={<AuthPage key="email" mode="email" />} />
          <Route
            path="/public/subscribers/confirm"
            element={<AuthPage key="sc" mode="subscriber-confirm" />}
          />
          <Route
            path="/public/subscribers/unsubscribe"
            element={<AuthPage key="su" mode="subscriber-unsubscribe" />}
          />
          <Route
            path="/channels/unsubscribe"
            element={<AuthPage key="cu" mode="channel-unsubscribe" />}
          />
          <Route path="/channels/verify" element={<AuthPage key="cv" mode="channel-verify" />} />
          <Route
            element={
              <ProtectedRoute>
                <AppShell />
              </ProtectedRoute>
            }
          >
            <Route path="/" element={<MonitorsPage />} />
            <Route path="/sites/new" element={<MonitorForm key="create" mode="create" />} />
            <Route path="/sites/:id" element={<MonitorPage />} />
            <Route path="/sites/:id/edit" element={<MonitorForm key="edit" mode="edit" />} />
            <Route path="/incidents" element={<IncidentsPage />} />
            <Route path="/incidents/:id" element={<IncidentPage />} />
            <Route path="/channels" element={<ChannelsPage />} />
            <Route path="/status-pages" element={<StatusPagesPage />} />
            <Route path="/status-pages/new" element={<StatusPageEditor />} />
            <Route path="/status-pages/:id" element={<StatusPageEditor />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/api-keys" element={<KeysPage />} />
            <Route path="/api-docs" element={<ApiDocsPage />} />
            <Route path="/admin" element={<AdminPage />} />
            <Route path="/billing/dev" element={<DevPaymentPage />} />
            <Route path="/billing/return" element={<BillingReturnPage />} />
          </Route>
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Suspense>
    </>
  );
}
