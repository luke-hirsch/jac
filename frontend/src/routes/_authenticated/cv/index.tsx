import { createFileRoute, Link } from "@tanstack/react-router";
import { useQueries } from "@tanstack/react-query";
import { fetchPage, type Page } from "@/lib/queries/paginated";
import type { ResourceKey } from "@/lib/queries/jac";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const SECTIONS: { key: ResourceKey; label: string; to: string; url: string }[] =
  [
    { key: "jobs", label: "Jobs", to: "/cv/jobs", url: "/api/jac/jobs/" },
    {
      key: "education",
      label: "Education",
      to: "/cv/education",
      url: "/api/jac/education/",
    },
    {
      key: "skills",
      label: "Skills",
      to: "/cv/skills",
      url: "/api/jac/skills/",
    },
    {
      key: "certifications",
      label: "Certifications",
      to: "/cv/certifications",
      url: "/api/jac/certifications/",
    },
    {
      key: "projects",
      label: "Projects",
      to: "/cv/projects",
      url: "/api/jac/projects/",
    },
    {
      key: "languages",
      label: "Languages",
      to: "/cv/languages",
      url: "/api/jac/languages/",
    },
    {
      key: "snippets",
      label: "Snippets",
      to: "/cv/snippets",
      url: "/api/jac/resume-snippets/",
    },
  ];

export const Route = createFileRoute("/_authenticated/cv/")({
  component: CvDashboard,
});

function CvDashboard() {
  const queries = useQueries({
    queries: SECTIONS.map((s) => ({
      queryKey: ["jac", s.key, "list", { ordering: "-updated_at", page: 1 }],
      queryFn: () =>
        fetchPage<{ id: number; updated_at?: string }>(s.url, {
          ordering: "-updated_at",
        }),
    })),
  });

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {SECTIONS.map((s, i) => {
        const q = queries[i];
        const data = q.data as Page<{ id: number }> | undefined;
        return (
          <Link key={s.key} to={s.to} className="block">
            <Card className="hover:bg-muted/40 transition-colors">
              <CardHeader>
                <CardTitle className="text-base">{s.label}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-semibold">
                  {q.isLoading ? "…" : (data?.count ?? 0)}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  {(data?.count ?? 0) === 0
                    ? "No entries yet"
                    : "Click to manage"}
                </p>
              </CardContent>
            </Card>
          </Link>
        );
      })}
    </div>
  );
}
