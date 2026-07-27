import { HeadContent, Outlet, createRootRoute } from "@tanstack/react-router";
import { Toaster } from "@/components/ui/sonner";

export const Route = createRootRoute({
  component: () => (
    <div className="min-h-screen">
      <HeadContent />
      <Outlet />
      <Toaster richColors position="top-right" />
    </div>
  ),
});
