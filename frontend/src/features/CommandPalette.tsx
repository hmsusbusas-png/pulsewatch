import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Bell,
  BookOpen,
  CreditCard,
  Globe,
  History,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Shield,
  Siren,
} from "lucide-react";
import { apiRequest } from "../api/client";
import { invalidate, keys, useMe, useSites } from "../api/queries";
import type { Site } from "../api/types";
import { useAuth } from "../auth/auth-context";
import { hostOf } from "../lib/format";
import { notifyError, toast } from "../ui/toast-store";
import { recentMonitors } from "./recent";

type Command = {
  id: string;
  group: string;
  label: string;
  hint?: string;
  icon: ReactNode;
  keywords?: string;
  run: () => void | Promise<void>;
};

function score(text: string, query: string): number {
  const haystack = text.toLowerCase();
  const needle = query.toLowerCase().trim();
  if (!needle) return 1;
  if (haystack.startsWith(needle)) return 3;
  if (haystack.includes(needle)) return 2;
  let position = 0;
  for (const char of needle) {
    position = haystack.indexOf(char, position);
    if (position < 0) return 0;
    position += 1;
  }
  return 1;
}

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const id = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const navigate = useNavigate();
  const location = useLocation();
  const client = useQueryClient();
  const sites = useSites();
  const me = useMe();
  const { signOut } = useAuth();

  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    if (open && !node.open) {
      setQuery("");
      setActive(0);
      node.showModal();
      input.current?.focus();
    } else if (!open && node.open) node.close();
  }, [open]);

  const current = useMemo(() => {
    const match = location.pathname.match(/^\/sites\/(\d+)/);
    return match ? sites.data?.find((site) => site.id === Number(match[1])) : undefined;
  }, [location.pathname, sites.data]);

  const commands = useMemo<Command[]>(() => {
    const go = (path: string) => () => navigate(path);
    const checkNow = (site: Site) => async () => {
      try {
        await apiRequest(`/sites/${site.id}/check-now`, { method: "POST" });
        toast(`Проверка «${site.name}» поставлена в очередь`);
      } catch (error) {
        notifyError(error);
      }
    };
    const togglePause = (site: Site) => async () => {
      try {
        await apiRequest(`/sites/${site.id}`, { method: "PATCH", body: { paused: !site.paused } });
        invalidate(client, keys.sites, keys.site(site.id), keys.dashboard);
        toast(site.paused ? `«${site.name}» снова проверяется` : `«${site.name}» на паузе`);
      } catch (error) {
        notifyError(error);
      }
    };
    const siteActions = (site: Site, group: string): Command[] =>
      site.monitor_type === "heartbeat"
        ? []
        : [
            {
              id: `check-${site.id}`,
              group,
              label: `Проверить сейчас: ${site.name}`,
              icon: <RefreshCw size={15} />,
              run: checkNow(site),
            },
            {
              id: `pause-${site.id}`,
              group,
              label: `${site.paused ? "Возобновить" : "Поставить на паузу"}: ${site.name}`,
              icon: site.paused ? <Play size={15} /> : <Pause size={15} />,
              run: togglePause(site),
            },
          ];
    const list: Command[] = [];
    if (current) list.push(...siteActions(current, "Этот монитор"));
    if (!query) {
      for (const recent of recentMonitors()) {
        list.push({
          id: `recent-${recent.id}`,
          group: "Недавние",
          label: recent.name,
          icon: <History size={15} />,
          run: go(`/sites/${recent.id}`),
        });
      }
    }
    list.push(
      {
        id: "new",
        group: "Действия",
        label: "Добавить монитор",
        hint: "N",
        icon: <Plus size={15} />,
        run: go("/sites/new"),
      },
      {
        id: "logout",
        group: "Действия",
        label: "Выйти",
        icon: <LogOut size={15} />,
        keywords: "logout выход",
        run: async () => {
          await signOut();
          navigate("/login");
        },
      },
      {
        id: "nav-home",
        group: "Разделы",
        label: "Журнал мониторов",
        hint: "G J",
        icon: <LayoutDashboard size={15} />,
        keywords: "dashboard главная",
        run: go("/"),
      },
      {
        id: "nav-incidents",
        group: "Разделы",
        label: "Инциденты",
        hint: "G I",
        icon: <Siren size={15} />,
        run: go("/incidents"),
      },
      {
        id: "nav-pages",
        group: "Разделы",
        label: "Статус-страницы",
        hint: "G S",
        icon: <Globe size={15} />,
        run: go("/status-pages"),
      },
      {
        id: "nav-channels",
        group: "Разделы",
        label: "Каналы уведомлений",
        hint: "G C",
        icon: <Bell size={15} />,
        keywords: "telegram slack email",
        run: go("/channels"),
      },
      {
        id: "nav-pricing",
        group: "Разделы",
        label: "Тарифы",
        hint: "G T",
        icon: <CreditCard size={15} />,
        run: go("/pricing"),
      },
      {
        id: "nav-keys",
        group: "Разделы",
        label: "API-ключи",
        icon: <KeyRound size={15} />,
        run: go("/api-keys"),
      },
      {
        id: "nav-docs",
        group: "Разделы",
        label: "Документация API",
        icon: <BookOpen size={15} />,
        keywords: "openapi swagger curl",
        run: go("/api-docs"),
      },
      {
        id: "nav-settings",
        group: "Разделы",
        label: "Настройки и безопасность",
        icon: <Settings size={15} />,
        keywords: "сессии журнал часовой пояс",
        run: go("/settings"),
      },
    );
    if (me.data?.is_admin)
      list.push({
        id: "nav-admin",
        group: "Разделы",
        label: "Админка",
        icon: <Shield size={15} />,
        run: go("/admin"),
      });
    if (query) {
      const matches = (sites.data ?? [])
        .map((site) => ({ site, score: Math.max(score(site.name, query), score(site.url, query)) }))
        .filter((item) => item.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, 6);
      matches.forEach(({ site }, index) => {
        list.push({
          id: `site-${site.id}`,
          group: "Мониторы",
          label: site.name,
          hint: site.url ? hostOf(site.url) : undefined,
          icon: <Activity size={15} />,
          run: go(`/sites/${site.id}`),
        });
        if (index === 0) list.push(...siteActions(site, "Мониторы"));
      });
    }
    return list.filter(
      (item) =>
        item.group === "Мониторы" ||
        item.group === "Этот монитор" ||
        score(`${item.label} ${item.keywords ?? ""}`, query) > 0,
    );
  }, [query, sites.data, current, navigate, client, me.data, signOut]);

  useEffect(() => setActive(0), [query]);
  const run = (command: Command | undefined) => {
    if (!command) return;
    onClose();
    void command.run();
  };
  let lastGroup = "";

  return (
    <dialog
      ref={dialog}
      aria-label="Командная палитра"
      className="dialog top-[12vh] mb-auto mt-0 max-w-[600px]"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === dialog.current) onClose();
      }}
    >
      {open ? (
        <div className="flex max-h-[70vh] flex-col">
          <div className="flex items-center gap-3 border-b border-line px-4">
            <Search size={17} className="shrink-0 text-inkMuted" aria-hidden="true" />
            <input
              ref={input}
              role="combobox"
              aria-expanded="true"
              aria-controls={`${id}-list`}
              aria-activedescendant={commands[active] ? `${id}-${commands[active].id}` : undefined}
              aria-label="Команда или монитор"
              placeholder="Найти монитор, раздел или действие…"
              className="h-14 min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-inkFaint"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "ArrowDown")
                  setActive((value) => Math.min(commands.length - 1, value + 1));
                else if (event.key === "ArrowUp") setActive((value) => Math.max(0, value - 1));
                else if (event.key === "Enter") run(commands[active]);
                else return;
                event.preventDefault();
              }}
            />
            <kbd className="kbd">Esc</kbd>
          </div>
          <ul
            id={`${id}-list`}
            role="listbox"
            aria-label="Результаты"
            className="min-h-0 flex-1 overflow-y-auto p-1.5"
          >
            {commands.length === 0 ? (
              <li className="px-3 py-8 text-center text-[13px] text-inkMuted">Ничего не найдено</li>
            ) : null}
            {commands.map((command, index) => {
              const header = command.group !== lastGroup ? command.group : null;
              lastGroup = command.group;
              return (
                <li key={command.id} role="presentation">
                  {header ? <p className="eyebrow px-2.5 pb-1 pt-2.5">{header}</p> : null}
                  <div
                    id={`${id}-${command.id}`}
                    role="option"
                    aria-selected={index === active}
                    data-active={index === active}
                    onMouseMove={() => setActive(index)}
                    onClick={() => run(command)}
                    className="menu-item cursor-pointer"
                  >
                    <span className="text-inkMuted" aria-hidden="true">
                      {command.icon}
                    </span>
                    <span className="min-w-0 flex-1 truncate">{command.label}</span>
                    {command.hint ? (
                      <span className="text-xs text-inkMuted">{command.hint}</span>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
          <div className="flex gap-4 border-t border-line px-4 py-2 text-2xs text-inkMuted">
            <span>
              <kbd className="kbd">↑</kbd> <kbd className="kbd">↓</kbd> выбор
            </span>
            <span>
              <kbd className="kbd">Enter</kbd> выполнить
            </span>
          </div>
        </div>
      ) : null}
    </dialog>
  );
}
