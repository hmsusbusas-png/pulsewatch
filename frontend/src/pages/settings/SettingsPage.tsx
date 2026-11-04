import { useEffect, useRef } from "react";
import type { KeyboardEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { AuditSection, BillingSection, ProfileSection, SecuritySection } from "./SettingsSections";
import { TransferSection } from "./TransferSection";

const TABS = [
  { id: "profile", label: "Профиль" },
  { id: "security", label: "Безопасность" },
  { id: "audit", label: "Журнал действий" },
  { id: "billing", label: "Подписка" },
  { id: "import", label: "Импорт и экспорт" },
] as const;
type Tab = (typeof TABS)[number]["id"];

export function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab") === "subscription" ? "billing" : params.get("tab");
  const tab: Tab = TABS.some((item) => item.id === requested) ? (requested as Tab) : "profile";
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  useEffect(() => {
    document.title = "Настройки. pulsewatch";
  }, []);
  const move = (event: KeyboardEvent, index: number) => {
    const delta = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!delta) return;
    event.preventDefault();
    const next = (index + delta + TABS.length) % TABS.length;
    setParams({ tab: TABS[next].id }, { replace: true });
    refs.current[next]?.focus();
  };
  return (
    <>
      <h1 className="page-title mb-4">Настройки</h1>
      <div
        role="tablist"
        aria-label="Разделы настроек"
        className="mb-5 flex gap-1 overflow-x-auto border-b border-line"
      >
        {TABS.map((item, index) => (
          <button
            key={item.id}
            ref={(node) => {
              refs.current[index] = node;
            }}
            id={`tab-${item.id}`}
            role="tab"
            type="button"
            aria-selected={tab === item.id}
            aria-controls={`panel-${item.id}`}
            tabIndex={tab === item.id ? 0 : -1}
            onKeyDown={(event) => move(event, index)}
            onClick={() => setParams({ tab: item.id }, { replace: true })}
            className={`-mb-px min-h-10 whitespace-nowrap border-b-2 px-3 text-[13px] font-medium transition-colors ${tab === item.id ? "border-accent text-ink" : "border-transparent text-inkMuted hover:text-ink"}`}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="max-w-4xl">
        {tab === "profile" ? <ProfileSection /> : null}
        {tab === "security" ? <SecuritySection /> : null}
        {tab === "audit" ? <AuditSection /> : null}
        {tab === "billing" ? <BillingSection /> : null}
        {tab === "import" ? <TransferSection /> : null}
      </div>
    </>
  );
}
