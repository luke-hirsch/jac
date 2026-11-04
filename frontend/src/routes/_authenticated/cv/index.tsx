import { createFileRoute, Link } from "@tanstack/react-router";
import { useQueries } from "@tanstack/react-query";
import { fetchPage, type Page } from "@/lib/queries/paginated";
import type { ResourceKey } from "@/lib/queries/jac";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyCareerCard } from "@/components/cv/first-run";
import { QueryError } from "@/components/query-error";

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
  const careerEmpty = queries.every((q) => q.isSuccess && q.data.count === 0);
  const failed = queries.filter((q) => q.isError && !q.data);

  return (
    <div className="space-y-6">
      {careerEmpty && <EmptyCareerCard />}
      {failed.length > 0 && (
        <QueryError
          error={failed[0].error}
          what="your CV overview"
          onRetry={() => failed.forEach((q) => q.refetch())}
          retrying={failed.some((q) => q.isFetching)}
        />
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {SECTIONS.map((s, i) => {
          const q = queries[i];
          const data = q.data as Page<{ id: number }> | undefined;
          const loadFailed = q.isError && !data;
          return (
            <Link key={s.key} to={s.to} className="block">
              <Card className="hover:bg-muted/40 transition-colors">
                <CardHeader>
                  <CardTitle className="text-base">{s.label}</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-3xl font-semibold">
                    {q.isLoading ? "…" : loadFailed ? "—" : (data?.count ?? 0)}
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    {loadFailed
                      ? "Couldn't load"
                      : (data?.count ?? 0) === 0
                        ? "No entries yet"
                        : "Click to manage"}
                  </p>
                </CardContent>
              </Card>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
