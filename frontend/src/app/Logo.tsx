export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2">
      <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
        <rect width="32" height="32" rx="8" fill="rgb(var(--accent))" />
        <path
          d="M5 17h6l3-9 5 16 3-7h5"
          fill="none"
          stroke="rgb(var(--on-accent))"
          strokeWidth="2.5"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      </svg>
      {compact ? null : (
        <span className="text-[17px] font-semibold tracking-[-0.04em] text-ink">pulsewatch</span>
      )}
    </span>
  );
}
