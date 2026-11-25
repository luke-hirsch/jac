import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { Studio, StudioButton } from "@/components/portfolio/studio";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { prewarmForVisitor } from "@/lib/prewarm";
import { useNativeMeta } from "@/lib/queries/portfolio";
import {
  DEFAULT_FOCUS,
  DEFAULT_TONE,
  EMPTY_FORM,
  formToSearch,
  luckySearch,
  standardSearch,
  type ExploreSearch,
  type QuestForm,
} from "@/lib/portfolio/questionnaire";

const MAX_QUERY_LEN = 280; // mirrors the rank/intro serializer cap

/** One segmented row of the style axis. Options come from /native/meta/; a fallback
 *  keeps the control usable if meta is still loading. */
function StyleAxis({
  label,
  options,
  value,
  onChange,
  fallback,
}: {
  label: string;
  options?: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
  fallback: string;
}) {
  const opts = options?.length
    ? options
    : [{ value: fallback, label: fallback }];
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium">{label}</p>
      <div className="flex flex-wrap gap-2">
        {opts.map((o) => (
          <StudioButton
            key={o.value}
            size="sm"
            variant={value === o.value ? "default" : "outline"}
            aria-pressed={value === o.value}
            onClick={() => onChange(o.value)}
          >
            {o.label}
          </StudioButton>
        ))}
      </div>
    </div>
  );
}

export function Questionnaire({
  handle,
  onDone,
}: {
  handle: string;
  onDone: (search: ExploreSearch) => void;
}) {
  const meta = useNativeMeta(handle);
  const prewarm = () => prewarmForVisitor(handle);
  const [form, setForm] = useState<QuestForm>(EMPTY_FORM);

  function toggleDomain(name: string) {
    setForm((f) => ({
      ...f,
      domains: f.domains.includes(name)
        ? f.domains.filter((d) => d !== name)
        : [...f.domains, name],
    }));
  }

  return (
    <Studio
      as="main"
      accent={meta.data?.accent}
      className="min-h-screen flex flex-col items-center justify-center gap-6 px-4 py-10"
    >
      {/* The first click or key in here, not the page load: only a person gets this far. */}
      <Card
        className="w-full max-w-lg rounded-[18px] border shadow-none ring-0"
        onPointerDownCapture={prewarm}
        onKeyDownCapture={prewarm}
      >
        <CardHeader>
          <CardTitle className="font-display text-2xl font-bold tracking-[-0.02em] normal-case">
            What do you want to see?
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="space-y-2">
            <p className="text-sm font-medium">What are you interested in?</p>
            <div className="flex flex-wrap gap-2">
              {(meta.data?.domains ?? []).map((d) => {
                const picked = form.domains.includes(d);
                return (
                  <StudioButton
                    key={d}
                    type="button"
                    size="xs"
                    variant={picked ? "default" : "outline"}
                    aria-pressed={picked}
                    onClick={() => toggleDomain(d)}
                  >
                    {d}
                  </StudioButton>
                );
              })}
              {meta.data && meta.data.domains.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No topics to pick yet — try “I feel lucky”.
                </p>
              ) : null}
            </div>
          </div>

          <StyleAxis
            label="Angle"
            options={meta.data?.focuses}
            value={form.focus}
            onChange={(v) => setForm((f) => ({ ...f, focus: v }))}
            fallback={DEFAULT_FOCUS}
          />
          <StyleAxis
            label="Tone"
            options={meta.data?.tones}
            value={form.tone}
            onChange={(v) => setForm((f) => ({ ...f, tone: v }))}
            fallback={DEFAULT_TONE}
          />

          <div className="space-y-2">
            <p className="text-sm font-medium">Anything specific? (optional)</p>
            <Input
              value={form.query}
              maxLength={MAX_QUERY_LEN}
              placeholder="e.g. building things with local AI models"
              onChange={(e) =>
                setForm((f) => ({ ...f, query: e.target.value }))
              }
            />
          </div>

          <div className="flex flex-wrap gap-2">
            <StudioButton onClick={() => onDone(formToSearch(form))}>
              Show me
            </StudioButton>
            <StudioButton
              variant="outline"
              onClick={() => onDone(luckySearch())}
            >
              I feel lucky
            </StudioButton>
            <StudioButton
              variant="ghost"
              onClick={() => onDone(standardSearch())}
            >
              Skip the questions
            </StudioButton>
          </div>
        </CardContent>
      </Card>

      <p className="text-sm text-muted-foreground">
        Want your own tailored CV?{" "}
        <Link to="/auth/signup" className="underline">
          Create one here
        </Link>
      </p>
    </Studio>
  );
}
