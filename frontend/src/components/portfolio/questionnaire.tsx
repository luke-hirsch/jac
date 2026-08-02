import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useNativeMeta } from "@/lib/queries/portfolio";
import {
  DEFAULT_FOCUS,
  DEFAULT_TONE,
  EMPTY_FORM,
  formToSearch,
  luckySearch,
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
          <Button
            key={o.value}
            size="sm"
            variant={value === o.value ? "default" : "outline"}
            onClick={() => onChange(o.value)}
          >
            {o.label}
          </Button>
        ))}
      </div>
    </div>
  );
}

export function Questionnaire({
  onDone,
}: {
  onDone: (search: ExploreSearch) => void;
}) {
  const meta = useNativeMeta();
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
    <main className="min-h-screen flex flex-col items-center justify-center gap-6 px-4 py-10">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <CardTitle>What do you want to see?</CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="space-y-2">
            <p className="text-sm font-medium">What are you interested in?</p>
            <div className="flex flex-wrap gap-2">
              {(meta.data?.domains ?? []).map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => toggleDomain(d)}
                  className="cursor-pointer"
                >
                  <Badge
                    variant={form.domains.includes(d) ? "default" : "outline"}
                  >
                    {d}
                  </Badge>
                </button>
              ))}
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
            <Button onClick={() => onDone(formToSearch(form))}>Show me</Button>
            <Button variant="outline" onClick={() => onDone(luckySearch())}>
              I feel lucky
            </Button>
          </div>
        </CardContent>
      </Card>

      <p className="text-sm text-muted-foreground">
        Want your own tailored CV?{" "}
        <Link to="/auth/signup" className="underline">
          Create one here
        </Link>
      </p>
    </main>
  );
}
