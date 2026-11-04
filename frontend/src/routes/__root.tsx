import { HeadContent, Outlet, createRootRoute } from "@tanstack/react-router";
import { useResolvedTheme } from "@/components/appearance-context";
import { AppearanceProvider } from "@/components/appearance-provider";
import { LegalFooter } from "@/components/legal-footer";
import { Toaster } from "@/components/ui/sonner";

export const Route = createRootRoute({
  component: () => (
    <AppearanceProvider>
      <div className="min-h-screen">
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
