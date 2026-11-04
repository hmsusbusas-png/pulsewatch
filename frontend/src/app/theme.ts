export type Theme = "dark" | "light";

export function currentTheme(): Theme {
  return localStorage.getItem("pw.theme") === "light" ? "light" : "dark";
}

export function applyTheme(theme: Theme) {
  localStorage.setItem("pw.theme", theme);
  document.documentElement.dataset.theme = theme;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", theme === "light" ? "#f6f6f7" : "#070708");
}
