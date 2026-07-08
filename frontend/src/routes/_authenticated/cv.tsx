import { createFileRoute, Link, Outlet } from "@tanstack/react-router";

const TABS = [
  { to: "/cv", label: "Overview" },
  { to: "/cv/jobs", label: "Jobs" },
  { to: "/cv/education", label: "Education" },
  { to: "/cv/skills", label: "Skills" },
  { to: "/cv/certifications", label: "Certifications" },
  { to: "/cv/projects", label: "Projects" },
  { to: "/cv/languages", label: "Languages" },
  { to: "/cv/snippets", label: "Snippets" },
] as const;

export const Route = createFileRoute("/_authenticated/cv")({
  component: CvLayout,
});

function CvLayout() {
  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      <nav className="flex gap-1 border-b">
        {TABS.map((t) => (
          <Link
            key={t.to}
            to={t.to}
            className="px-3 py-2 text-sm rounded-t-md hover:bg-muted"
            activeProps={{
              className: "bg-muted font-medium border-b-2 border-primary",
            }}
            activeOptions={{ exact: t.to === "/cv" }}
          >
            {t.label}
          </Link>
        ))}
      </nav>
      <Outlet />
    </div>
  );
}
