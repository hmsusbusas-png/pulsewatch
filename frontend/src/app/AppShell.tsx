import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Bell,
  BookOpen,
  CreditCard,
  Globe,
  HelpCircle,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Menu as MenuIcon,
  Moon,
  Plus,
  Search,
  Settings,
  Shield,
  Siren,
  Sun,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useAuth } from "../auth/auth-context";
import { useDashboard, useMe, useSubscription } from "../api/queries";
import { CommandPalette } from "../features/CommandPalette";
import { Onboarding } from "../features/Onboarding";
import { ShortcutsDialog } from "../features/ShortcutsDialog";
import { useHotkeys } from "../features/useHotkeys";
import { daysUntil, formatDate, setDisplayZone } from "../lib/format";
import { LiveAnnouncer, LiveUpdates } from "../live/LiveUpdates";
import { useLiveState } from "../live/live-store";
import { Button, ButtonLink } from "../ui/Button";
import { Menu } from "../ui/Menu";
import { Logo } from "./Logo";
import { applyTheme, currentTheme } from "./theme";

type NavItem = { to: string; label: string; icon: LucideIcon };

const PRIMARY: NavItem[] = [
  { to: "/", label: "Журнал", icon: LayoutDashboard },
  { to: "/incidents", label: "Инциденты", icon: Siren },
  { to: "/status-pages", label: "Статус-страницы", icon: Globe },
  { to: "/channels", label: "Каналы", icon: Bell },
];
const SECONDARY: NavItem[] = [
  { to: "/pricing", label: "Тарифы", icon: CreditCard },
  { to: "/api-keys", label: "API-ключи", icon: KeyRound },
  { to: "/api-docs", label: "Документация API", icon: BookOpen },
  { to: "/settings", label: "Настройки", icon: Settings },
];

function LiveIndicator() {
  const state = useLiveState();
  const live = state === "live";
  return (
    <span className="inline-flex items-center gap-2 text-xs text-inkMuted" role="status">
      <span
        className={`live-dot ${live ? "bg-accent" : state === "connecting" ? "bg-warn" : "bg-unknown"}`}
        data-state={state}
        aria-hidden="true"
      />
      <span className={live ? "text-ink" : ""}>
        {live ? "В эфире" : state === "connecting" ? "Подключение…" : "Обновления приостановлены"}
      </span>
    </span>
  );
}

function SubscriptionBanner() {
  const subscription = useSubscription();
  const data = subscription.data;
  if (!data) return null;
  const left = daysUntil(data.current_period_end);
  let text: string | null = null;
  if (data.status === "trialing" && left !== null && left <= 7)
    text = `Пробный период ${data.plan.name} закончится ${formatDate(data.current_period_end)}.`;
  else if (data.status === "past_due") text = "Оплата не прошла. Обновите способ оплаты.";
  else if (data.scheduled_plan)
    text = `С ${formatDate(data.current_period_end)} подписка перейдёт на ${data.scheduled_plan === "free" ? "Free" : "Pro"}.`;
  if (!text) return null;
  return (
    <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-warn/30 bg-warn/[0.07] px-4 py-2.5 text-[13px]">
      <span>{text}</span>
      <ButtonLink to="/settings?tab=billing" size="sm">
        Управлять подпиской
      </ButtonLink>
    </div>
  );
}

function NavEntry({ item, badge }: { item: NavItem; badge?: number }) {
  const Icon = item.icon;
  return (
    <NavLink
      to={item.to}
      end={item.to === "/"}
      className={({ isActive }) =>
        `flex min-h-9 items-center gap-2.5 rounded px-2.5 text-[13px] font-medium transition-colors ${isActive ? "bg-panelHover text-ink" : "text-inkMuted hover:bg-panelHover hover:text-ink"}`
      }
    >
      <Icon size={16} className="shrink-0" />
      <span className="min-w-0 flex-1 truncate">{item.label}</span>
      {badge ? (
        <span
          className="rounded-full bg-down/15 px-1.5 text-2xs font-semibold text-down"
          aria-label={`открытых: ${badge}`}
        >
          {badge}
        </span>
      ) : null}
    </NavLink>
  );
}

export function AppShell() {
  const { signOut } = useAuth();
  const me = useMe();
  const dashboard = useDashboard();
  const subscription = useSubscription();
  const navigate = useNavigate();
  const location = useLocation();
  const [palette, setPalette] = useState(false);
  const [help, setHelp] = useState(false);
  const [theme, setTheme] = useState(currentTheme);
  const zone = me.data?.timezone ?? "Europe/Moscow";
  setDisplayZone(zone);
  useEffect(() => setPalette(false), [location.pathname]);

  useHotkeys({
    "mod+k": () => setPalette(true),
    "?": () => setHelp(true),
    n: () => navigate("/sites/new"),
    "/": () => {
      const search = document.querySelector<HTMLInputElement>("[data-search]");
      if (search) search.focus();
      else setPalette(true);
    },
    "g j": () => navigate("/"),
    "g i": () => navigate("/incidents"),
    "g s": () => navigate("/status-pages"),
    "g c": () => navigate("/channels"),
    "g t": () => navigate("/pricing"),
  });

  const leave = async () => {
    await signOut();
    navigate("/login", { replace: true });
  };
  const switchTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    setTheme(next);
  };
  const openIncidents = dashboard.data?.open_incidents ?? 0;
  const admin = me.data?.is_admin ? [{ to: "/admin", label: "Админка", icon: Shield }] : [];
  const usage = subscription.data;

  return (
    <div className="min-h-screen pb-[72px] lg:pb-0 lg:pl-[232px]">
      <a
        href="#main"
        className="sr-only z-[70] rounded bg-accent px-3 py-2 text-onAccent focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
      >
        Перейти к содержимому
      </a>
      <aside className="hidden border-r border-line bg-sidebar lg:fixed lg:inset-y-0 lg:left-0 lg:flex lg:w-[232px] lg:flex-col">
        <div className="flex h-14 items-center px-4">
          <NavLink to="/" aria-label="pulsewatch, журнал мониторов">
            <Logo />
          </NavLink>
        </div>
        <nav aria-label="Основная навигация" className="flex-1 overflow-y-auto px-3 pt-2">
          <div className="grid gap-0.5">
            {PRIMARY.map((item) => (
              <NavEntry
                key={item.to}
                item={item}
                badge={item.to === "/incidents" ? openIncidents : undefined}
              />
            ))}
          </div>
          <p className="eyebrow mb-1.5 mt-6 px-2.5">Аккаунт</p>
          <div className="grid gap-0.5">
            {[...SECONDARY, ...admin].map((item) => (
              <NavEntry key={item.to} item={item} />
            ))}
          </div>
        </nav>
        <Onboarding />
        {usage ? (
          <div className="border-t border-line px-4 py-3">
            <div className="mb-1.5 flex justify-between text-xs">
              <span className="font-medium">{usage.plan.name}</span>
              <span className="text-inkMuted">
                {usage.usage.sites ?? 0} из {usage.plan.max_sites}
              </span>
            </div>
            <div
              className="h-1 overflow-hidden rounded-full bg-line"
              role="progressbar"
              aria-label="Использовано мониторов"
              aria-valuenow={usage.usage.sites ?? 0}
              aria-valuemin={0}
              aria-valuemax={usage.plan.max_sites}
            >
              <div
                className="h-full origin-left bg-accent"
                style={{
                  transform: `scaleX(${Math.min(1, (usage.usage.sites ?? 0) / Math.max(1, usage.plan.max_sites))})`,
                }}
              />
            </div>
          </div>
        ) : null}
        <div className="flex items-center gap-2 border-t border-line px-3 py-3">
          <span
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent/15 text-xs font-semibold text-accent"
            aria-hidden="true"
          >
            {me.data?.email.charAt(0).toUpperCase()}
          </span>
          <span className="min-w-0 flex-1 truncate text-xs" title={me.data?.email}>
            {me.data?.email}
          </span>
          <Button
            size="icon-sm"
            variant="ghost"
            onClick={switchTheme}
            aria-label={theme === "dark" ? "Светлая тема" : "Тёмная тема"}
          >
            {theme === "dark" ? (
              <Sun size={15} aria-hidden="true" />
            ) : (
              <Moon size={15} aria-hidden="true" />
            )}
          </Button>
          <Button size="icon-sm" variant="ghost" onClick={() => void leave()} aria-label="Выйти">
            <LogOut size={15} aria-hidden="true" />
          </Button>
        </div>
      </aside>

      <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-canvas/90 px-4 backdrop-blur sm:px-6 lg:px-8">
        <NavLink to="/" className="lg:hidden" aria-label="pulsewatch">
          <Logo compact />
        </NavLink>
        <LiveIndicator />
        <button
          type="button"
          onClick={() => setPalette(true)}
          className="ml-auto flex h-9 min-w-0 items-center gap-2 rounded border border-line bg-sidebar px-3 text-[13px] text-inkMuted hover:border-lineStrong hover:text-ink sm:w-72"
          aria-label="Открыть командную палитру"
        >
          <Search size={15} aria-hidden="true" />
          <span className="hidden flex-1 text-left sm:inline">Перейти или выполнить…</span>
          <kbd className="kbd hidden sm:inline-flex">Ctrl K</kbd>
        </button>
        <Button
          size="icon-sm"
          variant="ghost"
          onClick={() => setHelp(true)}
          aria-label="Горячие клавиши"
          className="hidden sm:inline-flex"
        >
          <HelpCircle size={16} aria-hidden="true" />
        </Button>
        <ButtonLink
          to="/sites/new"
          variant="primary"
          size="sm"
          className="hidden sm:inline-flex"
          icon={<Plus size={15} aria-hidden="true" />}
        >
          Монитор
        </ButtonLink>
      </header>

      <main id="main" key={zone} className="mx-auto max-w-[1440px] px-4 pb-12 pt-6 sm:px-6 lg:px-8">
        <SubscriptionBanner />
        <Outlet />
      </main>

      <nav
        aria-label="Навигация"
        className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-5 border-t border-line bg-sidebar/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden"
      >
        {[PRIMARY[0], PRIMARY[1]].map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            className={({ isActive }) =>
              `flex min-h-[56px] flex-col items-center justify-center gap-0.5 text-2xs ${isActive ? "text-accent" : "text-inkMuted"}`
            }
          >
            <item.icon size={19} />
            {item.label}
          </NavLink>
        ))}
        <NavLink
          to="/sites/new"
          className="flex min-h-[56px] flex-col items-center justify-center gap-0.5 text-2xs text-inkMuted"
          aria-label="Новый монитор"
        >
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-accent text-onAccent">
            <Plus size={18} aria-hidden="true" />
          </span>
        </NavLink>
        <NavLink
          to="/channels"
          className={({ isActive }) =>
            `flex min-h-[56px] flex-col items-center justify-center gap-0.5 text-2xs ${isActive ? "text-accent" : "text-inkMuted"}`
          }
        >
          <Bell size={19} />
          Каналы
        </NavLink>
        <div className="flex items-center justify-center">
          <Menu
            label="Ещё разделы"
            side="top"
            items={[
              ...[PRIMARY[2], ...SECONDARY, ...admin].map((item) => ({
                label: item.label,
                icon: <item.icon size={15} />,
                onSelect: () => navigate(item.to),
              })),
              {
                label: theme === "dark" ? "Светлая тема" : "Тёмная тема",
                icon: theme === "dark" ? <Sun size={15} /> : <Moon size={15} />,
                onSelect: switchTheme,
              },
              { label: "Выйти", icon: <LogOut size={15} />, onSelect: () => void leave() },
            ]}
            trigger={(props) => (
              <button
                type="button"
                {...props}
                className="flex min-h-[56px] w-full flex-col items-center justify-center gap-0.5 text-2xs text-inkMuted"
              >
                <MenuIcon size={19} aria-hidden="true" />
                Ещё
              </button>
            )}
          />
        </div>
      </nav>

      <CommandPalette open={palette} onClose={() => setPalette(false)} />
      <ShortcutsDialog open={help} onClose={() => setHelp(false)} />
      <LiveUpdates />
      <LiveAnnouncer />
    </div>
  );
}
