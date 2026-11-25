import { HeadContent, Outlet, createRootRoute } from "@tanstack/react-router";
import { useResolvedTheme } from "@/components/appearance-context";
import { AppearanceProvider } from "@/components/appearance-provider";
import { LegalFooter } from "@/components/legal-footer";
import { Toaster } from "@/components/ui/sonner";
import { siteHost } from "@/lib/host";

export const Route = createRootRoute({
  component: () => (
    <AppearanceProvider>
      {/* The portfolio host serves the public portfolio and nothing else, so every page there,
          the legal footer and a loading state included, stands on the Studio's ground
          (index.css `.studio`). jac's own look stays on jac. */}
      <div
        className={
          siteHost() === "portfolio" ? "studio min-h-screen" : "min-h-screen"
        }
      >
        <HeadContent />
        <Outlet />
        <LegalFooter />
        <ThemedToaster />
      </div>
    </AppearanceProvider>
  ),
});

function ThemedToaster() {
  return <Toaster theme={useResolvedTheme()} richColors position="top-right" />;
}
