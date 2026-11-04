import { useId, useState } from "react";
import type { ReactElement, ReactNode } from "react";
import { cloneElement } from "react";

export function Tooltip({
  content,
  children,
  side = "top",
}: {
  content: ReactNode;
  children: ReactElement;
  side?: "top" | "bottom";
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const show = () => setOpen(true);
  const hide = () => setOpen(false);
  return (
    <span
      className="relative inline-flex max-w-full"
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      onKeyDown={(event) => {
        if (event.key === "Escape") hide();
      }}
    >
      {cloneElement(children, { "aria-describedby": open ? id : undefined })}
      {open ? (
        <span
          role="tooltip"
          id={id}
          className={`pointer-events-none absolute left-1/2 z-50 w-max max-w-[280px] -translate-x-1/2 rounded border border-line bg-raised px-2.5 py-1.5 text-xs leading-relaxed text-ink shadow-pop ${side === "top" ? "bottom-full mb-1.5" : "top-full mt-1.5"}`}
        >
          {content}
        </span>
      ) : null}
    </span>
  );
}
