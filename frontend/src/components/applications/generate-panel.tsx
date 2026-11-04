import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";

import {
  useExecutors,
  type Mode,
  defaultExecutorRow,
  executorDisabledReason,
  defaultModeFor,
  defaultModelFor,
  providerLabel,
} from "@/lib/queries/llm";

import {
  runToApplicationPatch,
  useUpdateApplication,
  type ApplicationRow,
} from "@/lib/queries/applications";
import {
  groundingBadge,
  isRunInFlight,
  isStuckInQueue,
  isWaitingForCompute,
  runStatusLine,
  knobParams,
  pendingAgeSeconds,
  registerBadge,
  useCreateGeneration,
  type RunState,
} from "@/lib/queries/generations";
import { type SocketStatus } from "@/lib/ws";
import {
  personalityHint,
  usePersonality,
  type LetterTone,
  type LetterFocus,
  TONE_OPTIONS,
  FOCUS_OPTIONS,
} from "@/lib/queries/personality";

function toneClass(tone: "green" | "amber" | "muted") {
  return tone === "green"
    ? "bg-green-100 text-green-800"
    : tone === "amber"
      ? "bg-amber-100 text-amber-900"
      : "bg-muted text-muted-foreground";
}

// Radix Select forbids an empty-string item value; this sentinel = "no knob,
// let the model default decide". Mapped back to "" before it enters the pick.
const KNOB_DEFAULT = "__default__";

// knobs: generic per-knob values keyed by the spec name (effort/temperature);
// "" means unset. Reset to {} on every executor pick.
type Pick = {
  provider: string;
  model: string;
  mode: Mode;
  knobs: Record<string, string>;
};

export function GeneratePanel({
  app,
  activeRunId,
  onRunSelected,
  runState,
  runCreatedAt,
  now,
  socket,
  onAbort,
  aborting,
  applied,
  onApplied,
}: {
  app: ApplicationRow;
  activeRunId: number | null;
  onRunSelected: (id: number) => void;
  runState: RunState;
  runCreatedAt: string | null;
  now: Date;
  socket: SocketStatus;
  onAbort: () => void;
  aborting: boolean;
  applied: boolean;
  onApplied: () => void;
}) {
  const executors = useExecutors();
  const rows = executors.data ?? [];
  const [picked, setPicked] = useState<Pick | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const create = useCreateGeneration();
  const update = useUpdateApplication();

  // Preselect the backend default once rows arrive; never overwrite an explicit pick.
  if (picked === null && rows.length > 0) {
    const def = defaultExecutorRow(rows);
    if (def)
      setPicked({
        provider: def.provider,
        model: defaultModelFor(def),
        mode: defaultModeFor(def),
        knobs: {},
      });
  }

  const pickedRow = rows.find((r) => r.provider === picked?.provider) ?? null;
  // Never yank: a refetch that disables the picked row keeps the selection and
  // surfaces the reason on the Generate button instead.
  const pickedReason = pickedRow ? executorDisabledReason(pickedRow) : null;

  // A real personal paragraph is possible ⇔ a commercial (web-search-capable) pick.
  const capable = pickedRow != null && !pickedRow.self_hosted;
  const personality = usePersonality(capable);
  const hint = personalityHint(capable, personality.data);
  const personalityRow = personality.data;
  const [toneOverride, setToneOverride] = useState<LetterTone | "">("");
  const [focusOverride, setFocusOverride] = useState<LetterFocus | "">("");

  const running = activeRunId != null && isRunInFlight(runState.status);
  const ageSeconds = runCreatedAt ? pendingAgeSeconds(runCreatedAt, now) : 0;
  const stuckInQueue =
    running &&
    runCreatedAt != null &&
    isStuckInQueue(runState.status, runState.queueAhead, runCreatedAt, now);

  async function onGenerate() {
    if (!picked) return;
    try {
      const run = await create.mutateAsync({
        job_application: app.id,
        // The toggle only ever offers a row's modes (standard/high); manual never
        // reaches here. Narrow for GenerationForm's non-manual mode type.
        mode: picked.mode === "manual" ? "standard" : picked.mode,
        provider: picked.provider,
        model: picked.model, // "" for HirschAI → omitted by toPayload
        params: knobParams(picked.knobs), // blanks omitted; {} → omitted by toPayload
        letter_tone: toneOverride,
        letter_focus: focusOverride,
      });
      onRunSelected(run.id);
    } catch (e) {
      if (e instanceof ApiError && e.status === 400) {
        const data = e.data as {
          mode?: string[];
          provider?: string[];
          params?: string[];
        };
        const msg = data.mode?.[0] ?? data.provider?.[0] ?? data.params?.[0];
        if (msg) {
          toast.error(msg);
          return;
        }
      }
      if (e instanceof ApiError && e.status === 429) {
        const data = e.data as { detail?: string };
        toast.error(data.detail ?? "You've reached today's generation limit.");
        return;
      }
      toast.error("Could not start generation");
    }
  }

  function onApply() {
    if (!runState.result) return;
    onApplied(); // arm the fresh-highlight before the refetched content lands
    update.mutate(
      {
        id: app.id,
        body: runToApplicationPatch(runState.result, app.cv_content),
      },
      {
        onSuccess: () => toast.success("Run applied to the application"),
        onError: () => toast.error("Could not apply the run"),
      },
    );
  }

  const result = runState.status === "done" ? runState.result : null;

  const grounding = result
    ? groundingBadge(result.cover_letter.grounding)
    : null;
  const register = result ? registerBadge(result.cover_letter.register) : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Generate</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-end gap-4">
          <div className="space-y-1">
            <Label>AI</Label>
            <Select
              value={picked?.provider ?? ""}
              onValueChange={(provider) => {
                const row = rows.find((r) => r.provider === provider);
                if (row)
                  setPicked({
                    provider: row.provider,
                    model: defaultModelFor(row),
                    mode: defaultModeFor(row),
                    knobs: {}, // reset knobs on every executor pick
                  });
              }}
            >
              <SelectTrigger className="w-64" aria-label="AI">
                <SelectValue placeholder="Pick an executor" />
              </SelectTrigger>
              <SelectContent>
                {rows.map((r) => {
                  const reason = executorDisabledReason(r);
                  return (
                    <SelectItem
                      key={r.provider}
                      value={r.provider}
                      disabled={reason !== null}
                    >
                      {r.label}
                      {r.default ? " · default" : ""}
                      {reason ? ` · ${reason}` : ""}
                      {r.self_hosted && r.reachable === false
                        ? " · asleep"
                        : ""}
                    </SelectItem>
                  );
                })}
              </SelectContent>
            </Select>
          </div>

          {pickedRow && !pickedRow.self_hosted && (
            <>
              <div className="space-y-1">
                <Label>Model</Label>
                <Select
                  value={picked?.model ?? ""}
                  onValueChange={(model) =>
                    setPicked((p) => (p ? { ...p, model } : p))
                  }
                >
                  <SelectTrigger className="w-56" aria-label="Model">
                    <SelectValue placeholder="Model" />
                  </SelectTrigger>
                  <SelectContent>
                    {pickedRow.models.map((m) => (
                      <SelectItem key={m.id} value={m.id}>
                        {m.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Mode</Label>
                <div className="flex gap-1" role="group" aria-label="Mode">
                  {pickedRow.modes.map((m) => (
                    <Button
                      key={m}
                      type="button"
                      size="sm"
                      variant={picked?.mode === m ? "default" : "outline"}
                      onClick={() =>
                        setPicked((p) => (p ? { ...p, mode: m } : p))
                      }
                    >
                      {m}
                    </Button>
                  ))}
                </div>
              </div>
              {Object.keys(pickedRow.knobs).length > 0 && (
                <>
                  <button
                    type="button"
                    className="self-end pb-2 text-sm text-muted-foreground hover:text-primary"
                    onClick={() => setShowAdvanced((s) => !s)}
                  >
                    {showAdvanced ? "Hide advanced" : "Advanced settings"}
                  </button>
                  {showAdvanced &&
                    Object.entries(pickedRow.knobs).map(([name, spec]) => {
                      const value = picked?.knobs[name] ?? "";
                      const setKnob = (v: string) =>
                        setPicked((p) => {
                          if (!p) return p;
                          const next = { ...p.knobs, [name]: v };
                          // Setting a knob clears any mutually-exclusive one
                          // (excludes is declared one-directionally) so a
                          // disabled control never submits a stale value the
                          // server would 400 on.
                          if (v)
                            for (const [other, os] of Object.entries(
                              pickedRow.knobs,
                            ))
                              if (
                                other !== name &&
                                ((spec.excludes ?? []).includes(other) ||
                                  (os.excludes ?? []).includes(name))
                              )
                                next[other] = "";
                          return { ...p, knobs: next };
                        });
                      // A choices knob (effort) → a Select with a "model
                      // default" blank; a bounded knob (temperature) → a
                      // numeric input, disabled while any excluding knob is set.
                      if (spec.choices)
                        return (
                          <div key={name} className="space-y-1">
                            <Label className="capitalize">{name}</Label>
                            <Select
                              value={value || KNOB_DEFAULT}
                              onValueChange={(v) =>
                                setKnob(v === KNOB_DEFAULT ? "" : v)
                              }
                            >
                              <SelectTrigger className="w-40" aria-label={name}>
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value={KNOB_DEFAULT}>
                                  model default
                                </SelectItem>
                                {spec.choices.map((c) => (
                                  <SelectItem
                                    key={c}
                                    value={c}
                                    className="capitalize"
                                  >
                                    {c}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                        );
                      const blockedBy = (spec.excludes ?? []).filter(
                        (other) => (picked?.knobs[other] ?? "") !== "",
                      );
                      const disabled = blockedBy.length > 0;
                      return (
                        <div key={name} className="space-y-1">
                          <Label className="capitalize">{name}</Label>
                          <Input
                            aria-label={name}
                            type="number"
                            className="w-28"
                            min={spec.min}
                            max={spec.max}
                            step={0.1}
                            placeholder={
                              spec.min != null
                                ? `${spec.min}–${spec.max}`
                                : undefined
                            }
                            value={value}
                            disabled={disabled}
                            onChange={(e) => setKnob(e.target.value)}
                          />
                          {disabled && (
                            <p className="text-xs text-muted-foreground">
                              unset {blockedBy.join(", ")} to use
                            </p>
                          )}
                        </div>
                      );
                    })}
                </>
              )}
              <div className="grid grid-cols-2 gap-2">
                <Select
                  value={
                    toneOverride || (personalityRow?.letter_tone ?? "neutral")
                  }
                  onValueChange={(v) => setToneOverride(v as LetterTone)}
                >
                  <SelectTrigger
                    className="h-8 text-xs"
                    aria-label="Letter tone"
                  >
                    <SelectValue placeholder="Tone" />
                  </SelectTrigger>
                  <SelectContent>
                    {TONE_OPTIONS.map((o) => (
                      <SelectItem key={o.value} value={o.value}>
                        {o.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select
                  value={
                    focusOverride ||
                    (personalityRow?.letter_focus ?? "balanced")
                  }
                  onValueChange={(v) => setFocusOverride(v as LetterFocus)}
                >
                  <SelectTrigger
                    className="h-8 text-xs"
                    aria-label="Letter focus"
                  >
                    <SelectValue placeholder="Focus" />
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
            </>
          )}

          <Button
            onClick={onGenerate}
            disabled={
              running || create.isPending || !picked || pickedReason != null
            }
          >
            {running
              ? `${runState.status === "pending" ? "Queued" : "Generating"} · ${ageSeconds}s`
              : "Generate"}
          </Button>
          {!running && pickedReason && (
            <span className="text-sm text-amber-700">
              {providerLabel(rows, picked!.provider)} is {pickedReason}.
            </span>
          )}

          {running && (
            <Button variant="outline" onClick={onAbort} disabled={aborting}>
              {aborting ? "Aborting…" : "Abort"}
            </Button>
          )}
        </div>

        {/* Always mounted: a live region has to exist before its text changes. The clock
            stays on the button, which a screen reader doesn't re-read. */}
        <p role="status" className="text-sm text-muted-foreground">
          {running && runStatusLine(runState)}
        </p>

        {pickedRow && !pickedRow.self_hosted && (
          <p className="text-xs text-muted-foreground">
            High mode selects holistically and sees the posting under an
            always-on audit; standard is the lighter, label-driven rung.
          </p>
        )}

        {hint && <p className="text-xs text-amber-700">{hint}</p>}

        {stuckInQueue && (
          <p className="text-sm text-amber-700">
            Nothing is ahead of this run, and nobody has picked it up for{" "}
            {ageSeconds}s — the generation worker may not be running. It stays
            in line; you can abort it.
          </p>
        )}
        {isWaitingForCompute(runState) && (
          <p className="text-sm text-muted-foreground">
            The model server is off or starting. This run keeps its place and
            continues as soon as the server answers. You can leave this page.
          </p>
        )}
        {!running &&
          pickedRow?.self_hosted &&
          pickedRow.reachable === false && (
            <p className="text-sm text-muted-foreground">
              HirschAI didn't answer its last call. A new run waits in line
              until it's back.
            </p>
          )}

        {running && socket.kind === "retrying" && (
          <p className="text-sm text-amber-700">
            Live connection lost — retrying in{" "}
            {Math.max(1, Math.round(socket.delayMs / 1000))}s…
          </p>
        )}
        {running && socket.kind === "closed" && (
          <p className="text-sm text-muted-foreground">
            Live updates unavailable — refresh the page to see progress.
          </p>
        )}

        {runState.status === "failed" && activeRunId != null && (
          <p role="alert" className="text-sm text-destructive">
            Generation failed: {runState.error}
          </p>
        )}

        {result?.cover_letter.is_stub && (
          <p className="text-sm text-amber-700">
            The model couldn't write a letter for this run, so the letter below
            is a placeholder. Export refuses it until it's replaced: generate
            again, or write it by hand.
          </p>
        )}
        {result && grounding && (
          <div className="flex flex-wrap items-center gap-2 rounded border bg-muted/40 p-2 text-sm">
            <span
              className="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground"
              title="How this run was produced."
            >
              {result.meta.mode} · {providerLabel(rows, result.meta.provider)}
              {result.meta.model ? ` · ${result.meta.model}` : ""}
            </span>

            <span
              className={`rounded px-2 py-0.5 text-xs ${toneClass(grounding.tone)}`}
              title={result.cover_letter.grounding.claims.join("\n")}
            >
              {grounding.label}
            </span>

            {register && (
              <span
                className={`rounded px-2 py-0.5 text-xs ${toneClass(register.tone)}`}
                title={
                  register.tone === "amber"
                    ? "These pronouns contradict the letter's form of address. Flagged, not rewritten — fix them in the letter editor."
                    : "How the German letter addresses the company (set by the letter tone)."
                }
              >
                {register.label}
              </span>
            )}

            <span className="text-xs text-muted-foreground">
              {applied
                ? "Result is in the application below."
                : "Apply replaces the unpinned content below — pinned entries survive."}
            </span>
            <Button
              size="sm"
              className="ml-auto"
              onClick={onApply}
              disabled={applied || update.isPending}
            >
              {applied ? "Applied" : "Apply to application"}
            </Button>
          </div>
        )}

        {app.runs.length > 0 && (
          <>
            <Separator />
            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground">Runs</p>
              <ul className="space-y-1">
                {app.runs.map((r) => (
                  <li key={r.id}>
                    <button
                      type="button"
                      onClick={() => onRunSelected(r.id)}
                      className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-muted ${
                        r.id === activeRunId ? "bg-muted" : ""
                      }`}
                    >
                      <Badge variant="outline">{r.status}</Badge>
                      <span className="text-xs text-muted-foreground">
                        {r.mode} · {providerLabel(rows, r.provider)}
                      </span>
                      <span className="ml-auto text-xs text-muted-foreground">
                        {new Date(r.created_at).toLocaleString()}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
