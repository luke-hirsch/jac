import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  runToApplicationPatch,
  useUpdateApplication,
} from "@/lib/queries/applications";
import {
  aiShareBadge,
  groundingBadge,
  type CoverLetterResult,
  type CvEntry,
  type RunState,
} from "@/lib/queries/generations";

function toneClass(tone: "green" | "amber" | "muted") {
  return tone === "green"
    ? "bg-green-100 text-green-800"
    : tone === "amber"
      ? "bg-amber-100 text-amber-900"
      : "bg-muted text-muted-foreground";
}

export function ResultView({
  applicationId,
  state,
  applied,
}: {
  applicationId: number;
  state: RunState;
  applied: boolean;
}) {
  const update = useUpdateApplication();
  const result = state.result!;

  function onApply() {
    update.mutate(
      { id: applicationId, body: runToApplicationPatch(result) },
      {
        onSuccess: () => toast.success("Run applied to the application"),
        onError: () => toast.error("Could not apply the run"),
      },
    );
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Tailored CV</CardTitle>
          <div className="flex items-center gap-2">
            <Badge variant="outline">
              {result.meta.grade} · {result.meta.alias}
            </Badge>
            <Button
              size="sm"
              onClick={onApply}
              disabled={applied || update.isPending}
            >
              {applied ? "Applied" : "Apply to application"}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {Object.entries(result.cv).map(([section, entries]) =>
            entries.length === 0 ? null : (
              <CvSection key={section} section={section} entries={entries} />
            ),
          )}
        </CardContent>
      </Card>

      <CoverLetterCard letter={result.cover_letter} />
    </div>
  );
}

function CvSection({
  section,
  entries,
}: {
  section: string;
  entries: CvEntry[];
}) {
  return (
    <div>
      <h3 className="text-sm font-semibold capitalize">{section}</h3>
      <ul className="space-y-1">
        {entries.map((e) => (
          <li
            key={e.id}
            className="flex items-center justify-between gap-2 text-sm"
          >
            <span>{e.label}</span>
            {e.relevance_score != null && (
              <Badge variant="outline">{e.relevance_score.toFixed(2)}</Badge>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function CoverLetterCard({ letter }: { letter: CoverLetterResult }) {
  const ai = aiShareBadge(letter.ai_share);
  const g = groundingBadge(letter.grounding);
  const pg = groundingBadge(letter.personal_paragraph_grounding);
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Cover letter</CardTitle>
        <div className="flex gap-2">
          <span className={`rounded px-2 py-0.5 text-xs ${toneClass(ai.tone)}`}>
            {ai.label}
          </span>
          <span
            className={`rounded px-2 py-0.5 text-xs ${toneClass(g.tone)}`}
            title={letter.grounding.claims.join("\n")}
          >
            {g.label}
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <pre className="whitespace-pre-wrap font-sans text-sm">
          {letter.text}
        </pre>
        {(letter.personal_paragraph_is_stub || letter.personal_paragraph) && (
          <div
            className={
              letter.personal_paragraph_is_stub
                ? "rounded border border-destructive/50 bg-destructive/10 p-3"
                : "rounded border bg-muted/40 p-3"
            }
          >
            <div className="mb-1 flex items-center gap-2">
              <p className="text-xs font-medium">
                {letter.personal_paragraph_is_stub
                  ? "Personal paragraph — STUB (not sendable)"
                  : "Personal paragraph"}
              </p>
              {!letter.personal_paragraph_is_stub && (
                <span
                  className={`rounded px-2 py-0.5 text-xs ${toneClass(pg.tone)}`}
                  title={letter.personal_paragraph_grounding.claims.join("\n")}
                >
                  {pg.label}
                </span>
              )}
            </div>
            <p className="text-sm">{letter.personal_paragraph}</p>
            {!letter.personal_paragraph_is_stub &&
              letter.personal_paragraph_sources.length > 0 && (
                <ul className="mt-2 text-xs text-muted-foreground">
                  {letter.personal_paragraph_sources.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ul>
              )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
