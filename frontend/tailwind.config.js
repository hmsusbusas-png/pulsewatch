const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    colors: {
      transparent: "transparent",
      current: "currentColor",
      white: "#ffffff",
      black: "#000000",
      canvas: token("canvas"),
      sidebar: token("sidebar"),
      panel: token("panel"),
      panelHover: token("panel-hover"),
      raised: token("raised"),
      line: token("line"),
      lineStrong: token("line-strong"),
      ink: token("ink"),
      inkMuted: token("ink-muted"),
      inkFaint: token("ink-faint"),
      accent: token("accent"),
      onAccent: token("on-accent"),
      down: token("down"),
      unknown: token("unknown"),
      info: token("info"),
      warn: token("warn"),
      orange: token("orange"),
      yellow: token("yellow"),
      violet: token("violet"),
    },
    extend: {
      fontFamily: {
        sans: ['"Golos Text"', "system-ui", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "monospace"],
      },
      borderRadius: {
        DEFAULT: "8px",
        sm: "6px",
        md: "8px",
        lg: "12px",
      },
      fontSize: {
        "2xs": ["11px", "16px"],
      },
      boxShadow: {
        pop: "0 12px 40px -8px rgb(0 0 0 / 0.55), 0 0 0 1px rgb(var(--line) / 1)",
      },
      transitionDuration: {
        DEFAULT: "150ms",
      },
    },
  },
  plugins: [],
};
