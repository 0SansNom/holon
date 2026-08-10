import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { router } from "./router";
import { queryClient } from "./queryClient";
import { useAuthStore } from "./store/auth";
import { ErrorBoundary } from "./components/common/ErrorBoundary";
import "@fontsource-variable/inter";
import "./theme/theme.css";
import { initTheme } from "./store/theme";

initTheme();

let cachedPrincipal = useAuthStore.getState().session?.principal.urn ?? null;
useAuthStore.subscribe((state) => {
  const nextPrincipal = state.session?.principal.urn ?? null;
  if (nextPrincipal !== cachedPrincipal) queryClient.clear();
  cachedPrincipal = nextPrincipal;
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <div className="hl-h-full">
          <RouterProvider router={router} />
        </div>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
);
