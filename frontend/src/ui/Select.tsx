import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { Check, ChevronDown } from "lucide-react";
import { useDismiss } from "./useDismiss";

export type SelectOption<T extends string> = {
  value: T;
  label: string;
  hint?: string;
  icon?: ReactNode;
  disabled?: boolean;
};

type SelectProps<T extends string> = {
  label?: string;
  ariaLabel?: string;
  value: T;
  options: SelectOption<T>[];
  onChange: (value: T) => void;
  hint?: string;
  disabled?: boolean;
  size?: "sm" | "md";
  className?: string;
  placeholder?: string;
};

export function Select<T extends string>({
  label,
  ariaLabel,
  value,
  options,
  onChange,
  hint,
  disabled,
  size = "md",
  className = "",
  placeholder = "Выберите",
}: SelectProps<T>) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const typed = useRef({ text: "", at: 0 });
  const selected = options.find((option) => option.value === value);
  useDismiss(root, open, () => setOpen(false));
  const enabled = useMemo(() => options.filter((option) => !option.disabled), [options]);

  useEffect(() => {
    if (!open) return;
    const index = Math.max(
      0,
      enabled.findIndex((option) => option.value === value),
    );
    setActive(index);
    list.current?.focus();
  }, [open, enabled, value]);

  useEffect(() => {
    if (!open) return;
    list.current
      ?.querySelector<HTMLElement>(`[data-index="${active}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  const choose = (option: SelectOption<T> | undefined) => {
    if (!option || option.disabled) return;
    onChange(option.value);
    setOpen(false);
    button.current?.focus();
  };

  const onListKey = (event: KeyboardEvent<HTMLUListElement>) => {
    if (event.key === "ArrowDown") setActive((index) => Math.min(enabled.length - 1, index + 1));
    else if (event.key === "ArrowUp") setActive((index) => Math.max(0, index - 1));
    else if (event.key === "Home") setActive(0);
    else if (event.key === "End") setActive(enabled.length - 1);
    else if (event.key === "Enter" || event.key === " ") choose(enabled[active]);
    else if (event.key === "Escape" || event.key === "Tab") {
      setOpen(false);
      if (event.key === "Escape") button.current?.focus();
      return;
    } else if (event.key.length === 1) {
      const now = Date.now();
      typed.current = {
        text: (now - typed.current.at < 700 ? typed.current.text : "") + event.key.toLowerCase(),
        at: now,
      };
      const match = enabled.findIndex((option) =>
        option.label.toLowerCase().startsWith(typed.current.text),
      );
      if (match >= 0) setActive(match);
      return;
    } else return;
    event.preventDefault();
  };

  return (
    <div className={`relative min-w-0 ${className}`} ref={root}>
      {label ? (
        <span className="label" id={`${id}-label`}>
          {label}
        </span>
      ) : null}
      <button
        ref={button}
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        aria-labelledby={label ? `${id}-label ${id}-value` : undefined}
        aria-label={label ? undefined : ariaLabel}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            setOpen(true);
          }
        }}
        className={`field flex items-center justify-between gap-2 text-left ${size === "sm" ? "min-h-8 py-1 text-[13px]" : ""}`}
      >
        <span
          id={`${id}-value`}
          className={`flex min-w-0 items-center gap-2 truncate ${selected ? "" : "text-inkFaint"}`}
        >
          {selected?.icon}
          <span className="truncate">{selected?.label ?? placeholder}</span>
        </span>
        <ChevronDown size={15} className="shrink-0 text-inkMuted" aria-hidden="true" />
      </button>
      {open ? (
        <ul
          ref={list}
          id={`${id}-list`}
          role="listbox"
          tabIndex={-1}
          aria-labelledby={label ? `${id}-label` : undefined}
          aria-label={label ? undefined : ariaLabel}
          aria-activedescendant={`${id}-option-${active}`}
          onKeyDown={onListKey}
          className="popover absolute left-0 right-0 top-full mt-1 max-h-72 min-w-[180px] overflow-y-auto"
        >
          {enabled.map((option, index) => (
            <li
              key={option.value}
              id={`${id}-option-${index}`}
              data-index={index}
              role="option"
              aria-selected={option.value === value}
              data-active={index === active}
              onMouseEnter={() => setActive(index)}
              onClick={() => choose(option)}
              className="menu-item cursor-pointer"
            >
              <span className="flex w-4 shrink-0 justify-center text-accent">
                {option.value === value ? <Check size={14} aria-hidden="true" /> : null}
              </span>
              {option.icon}
              <span className="min-w-0 flex-1">
                <span className="block truncate">{option.label}</span>
                {option.hint ? (
                  <span className="block truncate text-xs text-inkMuted">{option.hint}</span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {hint ? <p className="hint">{hint}</p> : null}
    </div>
  );
}
