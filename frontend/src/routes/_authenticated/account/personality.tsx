import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import {
  MAX_ANSWER_LEN,
  answeredCount,
  answersDirty,
  cleanAnswers,
  dossierState,
  overlongAnswers,
  usePersonality,
  useRebuildDossier,
  useUpdateAnswers,
} from "@/lib/queries/personality";

export const Route = createFileRoute("/_authenticated/account/personality")({
  component: PersonalityPage,
});

const STATE_LABEL = {
  none: "no dossier yet",
  stale: "rebuilds on the next generation",
  fresh: "up to date",
} as const;

function PersonalityPage() {
  const personality = usePersonality();
  const update = useUpdateAnswers();
  const rebuild = useRebuildDossier();
  // Seeded from the server once; refetches must not clobber edits (adjust-state-
  // during-render, same pattern as the content card's server re-seed).
  const [draft, setDraft] = useState<Record<string, string> | null>(null);
  if (personality.data && draft === null) setDraft(personality.data.answers);

  if (!personality.data || draft === null)
    return <p className="text-sm">loading…</p>;
  const row = personality.data;

  const overlong = overlongAnswers(draft);
  const dirty = answersDirty(row.answers, draft);
  const state = dossierState(row);
  const answered = answeredCount(draft);

  function onSave() {
    update.mutate(cleanAnswers(draft!), {
      onSuccess: () => toast.success("Answers saved"),
      onError: () => toast.error("Could not save the answers"),
    });
  }

  function onRebuild() {
    rebuild.mutate(undefined, {
      onSuccess: () => toast.success("Dossier rebuilt"),
      onError: () => toast.error("Could not rebuild the dossier"),
    });
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-medium">Personality</h2>
        <p className="text-sm text-muted-foreground">
          Oblique questions, on purpose — answer the ones that spark something
          (about five is plenty, one tweet each). A small model distils them
          into the dossier the cover letter's personal paragraph grounds "you"
          in.
        </p>
      </div>

      <div className="space-y-4">
        {row.questions.map((q) => {
          const value = draft[q.id] ?? "";
          const over = value.trim().length > MAX_ANSWER_LEN;
          return (
            <div key={q.id} className="space-y-1">
              <Label htmlFor={`q-${q.id}`}>{q.prompt}</Label>
              <Textarea
                id={`q-${q.id}`}
                value={value}
                rows={2}
                onChange={(e) => setDraft({ ...draft, [q.id]: e.target.value })}
              />
              <p
                className={`text-xs ${over ? "text-destructive" : "text-muted-foreground"}`}
              >
                {value.trim().length}/{MAX_ANSWER_LEN}
              </p>
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-3">
        <Button
          onClick={onSave}
          disabled={!dirty || overlong.length > 0 || update.isPending}
        >
          {update.isPending ? "Saving…" : "Save answers"}
        </Button>
        <span className="text-xs text-muted-foreground">
          {answered} of {row.questions.length} answered
        </span>
      </div>

      <Separator />

      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-medium">Dossier</h3>
          <Badge variant="outline">{STATE_LABEL[state]}</Badge>
          <Button
            size="sm"
            variant="outline"
            className="ml-auto"
            onClick={onRebuild}
            disabled={state === "none" || rebuild.isPending}
          >
            {rebuild.isPending ? "Rebuilding…" : "Rebuild now"}
          </Button>
        </div>
        {row.dossier ? (
          <p className="whitespace-pre-wrap rounded border bg-muted/40 p-3 text-sm">
            {row.dossier}
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">
            No dossier yet — save some answers first. It is built automatically
            on the next generation, or on demand here (one LLM call).
          </p>
        )}
      </div>
    </div>
  );
}
