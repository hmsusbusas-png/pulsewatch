import { forwardRef } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Link } from "react-router-dom";
import type { LinkProps } from "react-router-dom";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "subtle";
type Size = "xs" | "sm" | "md" | "icon" | "icon-sm";

const BASE =
  "relative inline-flex max-w-full shrink-0 select-none items-center justify-center gap-2 rounded border font-medium " +
  "transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50 aria-[busy=true]:cursor-progress";

const VARIANTS: Record<Variant, string> = {
  primary: "border-accent bg-accent text-onAccent hover:bg-accent/90",
  secondary: "border-line bg-panelHover text-ink hover:border-lineStrong hover:bg-raised",
  ghost: "border-transparent bg-transparent text-inkMuted hover:bg-panelHover hover:text-ink",
  subtle: "border-transparent bg-panelHover text-ink hover:bg-raised",
  danger: "border-down/40 bg-down/10 text-down hover:border-down hover:bg-down/15",
};

const SIZES: Record<Size, string> = {
  xs: "min-h-7 px-2 text-xs",
  sm: "min-h-8 px-2.5 text-[13px]",
  md: "min-h-10 px-3.5 text-sm",
  icon: "h-10 w-10 p-0",
  "icon-sm": "h-8 w-8 p-0",
};

function buttonClass(variant: Variant = "secondary", size: Size = "md", extra = "") {
  return `${BASE} ${VARIANTS[variant]} ${SIZES[size]} ${extra}`;
}

export function Spinner({ size = 14 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className="animate-spin"
      aria-hidden="true"
      fill="none"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  icon?: ReactNode;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "secondary",
    size = "md",
    loading = false,
    icon,
    disabled,
    className = "",
    children,
    type = "button",
    onClick,
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={buttonClass(variant, size, className)}
      disabled={disabled}
      aria-busy={loading || undefined}
      onClick={(event) => {
        if (loading) {
          event.preventDefault();
          return;
        }
        onClick?.(event);
      }}
      {...rest}
    >
      {loading ? <Spinner /> : icon}
      {children}
    </button>
  );
});

export function ButtonLink({
  variant = "secondary",
  size = "md",
  className = "",
  icon,
  children,
  ...props
}: LinkProps & { variant?: Variant; size?: Size; icon?: ReactNode }) {
  return (
    <Link className={buttonClass(variant, size, className)} {...props}>
      {icon}
      {children}
    </Link>
  );
}
