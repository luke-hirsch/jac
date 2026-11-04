import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  MAX_ANSWER_LEN,
  answeredCount,
  answersDirty,
  cleanAnswers,
  dossierState,
  overlongAnswers,
  useCreateQuestion,
  useDeleteQuestion,
  usePersonality,
  useRebuildDossier,
  useRebuildStyleDossier,
  useUpdateAnswers,
  validateQuestionPrompt,
  TONE_OPTIONS,
  FOCUS_OPTIONS,
  type LetterTone,
  type LetterFocus,
  styleState,
  useUpdateLetterSettings,
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
  const rebuildStyle = useRebuildStyleDossier();
  const createQuestion = useCreateQuestion();
  const deleteQuestion = useDeleteQuestion();
  const settings = useUpdateLetterSettings();
  const [sample, setSample] = useState<string | null>(null);
  if (personality.data && sample === null)
    setSample(personality.data.writing_sample);
  // Seeded from the server once; refetches must not clobber edits (adjust-state-
  // during-render, same pattern as the content card's server re-seed).
  const [draft, setDraft] = useState<Record<string, string> | null>(null);
  const [newQuestion, setNewQuestion] = useState("");
  if (personality.data && draft === null) setDraft(personality.data.answers);

  if (!personality.data || draft === null)
    return <p className="text-sm">loading…</p>;
  const row = personality.data;

  const overlong = overlongAnswers(draft);
  const dirty = answersDirty(row.answers, draft);
  const state = dossierState(row);
  const answered = answeredCount(draft);
  const newQuestionError = validateQuestionPrompt(newQuestion);

  const styleFresh = styleState(row);
  const sampleDirty =
    sample !== null && sample.trim() !== row.writing_sample.trim();

  function saveMatrix(patch: {
    letter_tone?: LetterTone;
    letter_focus?: LetterFocus;
  }) {
    settings.mutate(patch, {
      onError: () => toast.error("Could not save the letter setting"),
    });
  }
  function saveSample() {
    settings.mutate(
      { writing_sample: (sample ?? "").trim() },
      {
        onSuccess: () => toast.success("Writing sample saved"),
        onError: () => toast.error("Could not save the writing sample"),
      },
    );
  }
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

  function onRebuildStyle() {
    rebuildStyle.mutate(undefined, {
      onSuccess: () => toast.success("Writing dossier rebuilt"),
      onError: () => toast.error("Could not rebuild the writing dossier"),
    });
  }

  function onAddQuestion() {
    if (validateQuestionPrompt(newQuestion)) return;
    createQuestion.mutate(newQuestion.trim(), {
      onSuccess: () => {
        setNewQuestion("");
        toast.success("Question added");
      },
      onError: () => toast.error("Could not add the question"),
    });
  }

  function onDeleteQuestion(pk: number, slug: string) {
    deleteQuestion.mutate(pk, {
      onSuccess: () => {
        // Drop any local draft answer for the removed question so it can't be re-sent.
        setDraft((d) => {
          if (!d) return d;
          const { [slug]: _gone, ...rest } = d;
          return rest;
        });
        toast.success("Question removed");
      },
      onError: () => toast.error("Could not remove the question"),
    });
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-medium">Personality</h2>
        <p className="text-sm text-muted-foreground">
          Oblique and work-values questions — answer the ones that spark
          something (about five to eight is plenty, one tweet each). A model
          distils them into the dossier the cover letter's personal paragraph
          grounds "you" in. Add your own questions at the bottom.
        </p>
      </div>

      <div className="space-y-4">
        {row.questions.map((q) => {
          const value = draft[q.slug] ?? "";
          const over = value.trim().length > MAX_ANSWER_LEN;
          return (
            <div key={q.slug} className="space-y-1">
              <div className="flex items-center gap-2">
                <Label htmlFor={`q-${q.slug}`}>{q.prompt}</Label>
                {q.editable && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-xs text-muted-foreground"
                    onClick={() => onDeleteQuestion(q.pk, q.slug)}
                    disabled={deleteQuestion.isPending}
                  >
                    Remove
                  </Button>
                )}
              </div>
              <Textarea
                id={`q-${q.slug}`}
                value={value}
                rows={2}
                onChange={(e) =>
                  setDraft({ ...draft, [q.slug]: e.target.value })
                }
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

      <div className="flex items-end gap-2">
        <div className="flex-1 space-y-1">
          <Label htmlFor="new-question">Add your own question</Label>
          <Input
            id="new-question"
            value={newQuestion}
            placeholder="e.g. What does a great week at work look like for you?"
            onChange={(e) => setNewQuestion(e.target.value)}
          />
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={onAddQuestion}
          disabled={!!newQuestionError || createQuestion.isPending}
        >
          {createQuestion.isPending ? "Adding…" : "Add"}
        </Button>
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

      <div className="space-y-4">
        <div>
          <h3 className="text-sm font-medium">Cover-letter voice</h3>
          <p className="text-sm text-muted-foreground">
            The default tone × focus for every letter, and a sample of your own
            writing the model imitates. A run can override the tone/focus for
            one application.
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <Label>Tone</Label>
            <Select
              value={row.letter_tone}
              onValueChange={(v) =>
                saveMatrix({ letter_tone: v as LetterTone })
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TONE_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>Focus</Label>
            <Select
              value={row.letter_focus}
              onValueChange={(v) =>
                saveMatrix({ letter_focus: v as LetterFocus })
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {FOCUS_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="space-y-1">
          <Label htmlFor="writing-sample">Writing sample</Label>
          <Textarea
            id="writing-sample"
            value={sample ?? ""}
            rows={6}
            placeholder="Paste a few paragraphs you wrote — an email, a blog post, an old cover letter…"
            onChange={(e) => setSample(e.target.value)}
          />
          <div className="flex items-center gap-3">
            <Button
              onClick={saveSample}
              disabled={!sampleDirty || settings.isPending}
            >
              {settings.isPending ? "Saving…" : "Save sample"}
            </Button>
            <Badge variant="outline">
              {styleFresh === "none"
                ? "no style yet"
                : styleFresh === "stale"
                  ? "rebuilds on the next generation"
                  : "up to date"}
            </Badge>
            <Button
              size="sm"
              variant="outline"
              className="ml-auto"
              onClick={onRebuildStyle}
              disabled={styleFresh === "none" || rebuildStyle.isPending}
            >
              {rebuildStyle.isPending ? "Rebuilding…" : "Rebuild now"}
            </Button>
          </div>
          {row.style_dossier && (
            <p className="whitespace-pre-wrap rounded border bg-muted/40 p-3 text-sm">
              {row.style_dossier}
            </p>
          )}
        </div>
      </div>
      <Separator />

      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium">Dossier</h3>
          <Badge variant="outline">{STATE_LABEL[state]}</Badge>
          <div className="ml-auto flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={onRebuild}
              disabled={state === "none" || rebuild.isPending}
            >
              {rebuild.isPending ? "Rebuilding…" : "Rebuild now"}
            </Button>
          </div>
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
