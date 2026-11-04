import { useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useDismiss } from "./useDismiss";

export type MenuItem = {
  label: string;
  icon?: ReactNode;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
  hint?: ReactNode;
};

export function Menu({
  trigger,
  items,
  label,
  align = "end",
  side = "bottom",
}: {
  trigger: (props: {
    "aria-haspopup": "menu";
    "aria-expanded": boolean;
    "aria-controls": string;
    onClick: () => void;
  }) => ReactNode;
  items: MenuItem[];
  label: string;
  align?: "start" | "end";
  side?: "top" | "bottom";
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  useDismiss(root, open, () => setOpen(false));

  useEffect(() => {
    if (open) {
      setActive(0);
      list.current?.focus();
    }
  }, [open]);

  const select = (item: MenuItem) => {
    if (item.disabled) return;
    setOpen(false);
    item.onSelect();
  };

  return (
    <div className="relative" ref={root}>
      {trigger({
        "aria-haspopup": "menu",
        "aria-expanded": open,
        "aria-controls": id,
        onClick: () => setOpen((value) => !value),
      })}
      {open ? (
        <div
          ref={list}
          id={id}
          role="menu"
          aria-label={label}
          tabIndex={-1}
          aria-activedescendant={`${id}-${active}`}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown") setActive((value) => (value + 1) % items.length);
            else if (event.key === "ArrowUp")
              setActive((value) => (value - 1 + items.length) % items.length);
            else if (event.key === "Enter" || event.key === " ") select(items[active]);
            else if (event.key === "Tab") setOpen(false);
            else return;
            event.preventDefault();
          }}
          className={`popover absolute min-w-[200px] ${side === "top" ? "bottom-full mb-1" : "top-full mt-1"} ${align === "end" ? "right-0" : "left-0"}`}
        >
          {items.map((item, index) => (
            <div
              key={item.label}
              id={`${id}-${index}`}
              role="menuitem"
              aria-disabled={item.disabled || undefined}
              data-active={index === active}
              onMouseEnter={() => setActive(index)}
              onClick={() => select(item)}
              className={`menu-item cursor-pointer ${item.danger ? "text-down" : ""}`}
            >
              {item.icon}
              <span className="flex-1">{item.label}</span>
              {item.hint}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
