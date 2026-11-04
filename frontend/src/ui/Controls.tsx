import { useId, useRef } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { Check, Minus } from "lucide-react";

type CheckboxProps = {
  checked: boolean | "mixed";
  onChange: (next: boolean, event: React.MouseEvent | KeyboardEvent) => void;
  label?: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
  ariaLabel?: string;
  className?: string;
};

export function Checkbox({
  checked,
  onChange,
  label,
  description,
  disabled,
  ariaLabel,
  className = "",
}: CheckboxProps) {
  const id = useId();
  const on = checked === true || checked === "mixed";
  const box = (
    <button
      type="button"
      role="checkbox"
      id={id}
      aria-checked={checked}
      aria-label={label ? undefined : ariaLabel}
      aria-labelledby={label ? `${id}-label` : undefined}
      disabled={disabled}
      onClick={(event) => onChange(checked !== true, event)}
      className={`flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-[5px] border transition-colors disabled:opacity-50 ${on ? "border-accent bg-accent text-onAccent" : "border-lineStrong bg-sidebar hover:border-inkFaint"}`}
    >
      {checked === "mixed" ? (
        <Minus size={12} strokeWidth={3} aria-hidden="true" />
      ) : checked ? (
        <Check size={12} strokeWidth={3} aria-hidden="true" />
      ) : null}
    </button>
  );
  if (!label) return <span className={`inline-flex p-1.5 ${className}`}>{box}</span>;
  return (
    <div className={`flex min-h-10 items-start gap-3 py-1 ${className}`}>
      <span className="mt-[3px]">{box}</span>
      <span className="min-w-0">
        <label
          id={`${id}-label`}
          htmlFor={id}
          className="cursor-pointer text-sm text-ink"
          onClick={(event) => {
            event.preventDefault();
            if (!disabled) onChange(checked !== true, event);
          }}
        >
          {label}
        </label>
        {description ? <span className="hint mt-0.5 block">{description}</span> : null}
      </span>
    </div>
  );
}

type SwitchProps = {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
  compact?: boolean;
};

export function Switch({ checked, onChange, label, description, disabled, compact }: SwitchProps) {
  const id = useId();
  const control = (
    <button
      type="button"
      role="switch"
      id={id}
      aria-checked={checked}
      aria-labelledby={`${id}-label`}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="group relative inline-flex h-11 w-11 shrink-0 items-center justify-center disabled:cursor-not-allowed disabled:opacity-50"
    >
      <span
        aria-hidden="true"
        className={`relative block h-5 w-9 rounded-full border transition-colors ${checked ? "border-accent bg-accent" : "border-lineStrong bg-panelHover"}`}
      >
        <span
          className={`absolute left-0.5 top-0.5 h-3.5 w-3.5 rounded-full bg-ink shadow transition-transform duration-150 ${checked ? "translate-x-4 bg-onAccent" : ""}`}
        />
      </span>
    </button>
  );
  if (compact)
    return (
      <span className="inline-flex items-center">
        <span id={`${id}-label`} className="sr-only">
          {label}
        </span>
        {control}
      </span>
    );
  return (
    <div className="flex items-center justify-between gap-4 py-1.5">
      <div className="min-w-0">
        <p id={`${id}-label`} className="text-sm text-ink">
          {label}
        </p>
        {description ? <p className="hint mt-0.5">{description}</p> : null}
      </div>
      {control}
    </div>
  );
}

type Option<T extends string> = {
  value: T;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
  icon?: ReactNode;
};

function arrowNavigation<T extends string>(
  event: KeyboardEvent,
  options: Option<T>[],
  value: T,
  onChange: (next: T) => void,
  refs: (HTMLButtonElement | null)[],
) {
  const enabled = options.filter((option) => !option.disabled);
  const index = enabled.findIndex((option) => option.value === value);
  let next = -1;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % enabled.length;
  if (event.key === "ArrowLeft" || event.key === "ArrowUp")
    next = (index - 1 + enabled.length) % enabled.length;
  if (event.key === "Home") next = 0;
  if (event.key === "End") next = enabled.length - 1;
  if (next < 0) return;
  event.preventDefault();
  onChange(enabled[next].value);
  refs[options.indexOf(enabled[next])]?.focus();
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
  size = "md",
}: {
  options: Option<T>[];
  value: T;
  onChange: (next: T) => void;
  label: string;
  size?: "sm" | "md";
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  return (
    <div role="radiogroup" aria-label={label} className="segment-group">
      {options.map((option, index) => (
        <button
          key={option.value}
          ref={(node) => {
            refs.current[index] = node;
          }}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          tabIndex={option.value === value ? 0 : -1}
          disabled={option.disabled}
          onClick={() => onChange(option.value)}
          onKeyDown={(event) => arrowNavigation(event, options, value, onChange, refs.current)}
          className={`segment ${size === "sm" ? "min-h-7 px-2.5 text-xs" : ""}`}
        >
          {option.icon}
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function RadioCards<T extends string>({
  options,
  value,
  onChange,
  label,
  columns = "sm:grid-cols-2",
}: {
  options: Option<T>[];
  value: T;
  onChange: (next: T) => void;
  label: string;
  columns?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  return (
    <div role="radiogroup" aria-label={label} className={`grid gap-2 ${columns}`}>
      {options.map((option, index) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            ref={(node) => {
              refs.current[index] = node;
            }}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            disabled={option.disabled}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) => arrowNavigation(event, options, value, onChange, refs.current)}
            className={`flex min-h-[64px] items-start gap-3 rounded-lg border p-3 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${active ? "border-accent bg-accent/[0.07]" : "border-line bg-sidebar hover:border-lineStrong"}`}
          >
            <span
              aria-hidden="true"
              className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${active ? "border-accent" : "border-lineStrong"}`}
            >
              {active ? <span className="h-2 w-2 rounded-full bg-accent" /> : null}
            </span>
            <span className="min-w-0">
              <span className="flex items-center gap-2 text-sm font-medium text-ink">
                {option.icon}
                {option.label}
              </span>
              {option.description ? (
                <span className="mt-0.5 block text-xs leading-relaxed text-inkMuted">
                  {option.description}
                </span>
              ) : null}
            </span>
          </button>
        );
      })}
    </div>
  );
}
