import { createFileRoute, Outlet } from "@tanstack/react-router";

export const Route = createFileRoute("/_authenticated/applications")({
  component: ApplicationsLayout,
});

function ApplicationsLayout() {
  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      <Outlet />
    </div>
  );
}
