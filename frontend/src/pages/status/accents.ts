export const ACCENTS = {
  emerald: { label: "Изумрудный", rgb: "16 185 129" },
  sky: { label: "Голубой", rgb: "14 165 233" },
  violet: { label: "Фиолетовый", rgb: "139 92 246" },
  amber: { label: "Янтарный", rgb: "245 158 11" },
  rose: { label: "Розовый", rgb: "244 63 94" },
  slate: { label: "Графит", rgb: "100 116 139" },
} as const;

export type AccentName = keyof typeof ACCENTS;
