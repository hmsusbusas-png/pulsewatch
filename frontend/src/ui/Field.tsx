import { forwardRef, useId } from "react";
import type { InputHTMLAttributes, ReactNode, TextareaHTMLAttributes } from "react";

type Shell = {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  suffix?: ReactNode;
};

type TextFieldProps = Omit<InputHTMLAttributes<HTMLInputElement>, "id"> & Shell;

export const TextField = forwardRef<HTMLInputElement, TextFieldProps>(function TextField(
  { label, hint, error, suffix, className = "", ...rest },
  ref,
) {
  const id = useId();
  const describedBy = [hint ? `${id}-hint` : "", error ? `${id}-error` : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <div className="min-w-0">
      <label className="label" htmlFor={id}>
        {label}
      </label>
      <div className="relative">
        <input
          ref={ref}
          id={id}
          className={`field ${error ? "field-invalid" : ""} ${suffix ? "pr-14" : ""} ${className}`}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy || undefined}
          {...rest}
        />
        {suffix ? (
          <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-xs text-inkMuted">
            {suffix}
          </span>
        ) : null}
      </div>
      {hint && !error ? (
        <p className="hint" id={`${id}-hint`}>
          {hint}
        </p>
      ) : null}
      {error ? (
        <p className="hint-error" id={`${id}-error`} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
});

type TextAreaProps = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id"> & Shell;

export function TextArea({ label, hint, error, className = "", ...rest }: TextAreaProps) {
  const id = useId();
  return (
    <div className="min-w-0">
      <label className="label" htmlFor={id}>
        {label}
      </label>
      <textarea
        id={id}
        className={`field ${error ? "field-invalid" : ""} ${className}`}
        aria-invalid={error ? true : undefined}
        aria-describedby={hint ? `${id}-hint` : undefined}
        {...rest}
      />
      {hint && !error ? (
        <p className="hint" id={`${id}-hint`}>
          {hint}
        </p>
      ) : null}
      {error ? (
        <p className="hint-error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function FormError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div role="alert" className="rounded border border-down/35 bg-down/10 px-3 py-2.5 text-sm">
      {message}
    </div>
  );
}
