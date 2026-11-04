import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/golos-text/cyrillic-400.css";
import "@fontsource/golos-text/latin-400.css";
import "@fontsource/golos-text/cyrillic-500.css";
import "@fontsource/golos-text/latin-500.css";
import "@fontsource/golos-text/cyrillic-600.css";
import "@fontsource/golos-text/latin-600.css";
import "@fontsource/ibm-plex-mono/cyrillic-400.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { queryClient } from "./api/query-client";
import { AuthProvider } from "./auth/AuthProvider";
import { applyTheme, currentTheme } from "./app/theme";
import "./index.css";

applyTheme(currentTheme());

const container = document.getElementById("root");

if (!container) {
  throw new Error("Root container is missing");
}

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
