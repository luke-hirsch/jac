import { useEffect, useState } from "react";
import { CheckIcon, CircleIcon, Loader2Icon, SparklesIcon } from "lucide-react";
import { StudioButton } from "@/components/portfolio/studio";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import type {
  PersonaliseView,
  Step,
  StepKey,
} from "@/lib/portfolio/personalise";
import { cn } from "@/lib/utils";

const STEP_LABEL: Record<StepKey, string> = {
  wake: "Waking the AI server",
  rank: "Sorting the entries by your interest",
  write: "Writing you a personal welcome",
};

/** Seconds since `running` turned true; 0 while it's false. A tick count, close enough for a
 *  clock on a waiting card. */
function useElapsed(running: boolean): number {
  const [seconds, setSeconds] = useState(0);
  const [wasRunning, setWasRunning] = useState(running);
  if (running !== wasRunning) {
    setWasRunning(running);
    setSeconds(0);
  }
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, [running]);
  return running ? seconds : 0;
}

const mmss = (s: number) =>
  `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

function StepIcon({ state }: { state: Step["state"] }) {
  if (state === "done") return <CheckIcon className="size-4 shrink-0" />;
  if (state === "active")
    return <Loader2Icon className="size-4 shrink-0 animate-spin" />;
  return <CircleIcon className="size-4 shrink-0 opacity-40" />;
}

/** The waiting card of a questionnaire visit: what the AI is doing, and the way out to the
 *  standard portfolio, so the wait is the visitor's choice. */
export function PersonaliseGate({
  view,
  ownerName,
  onBrowse,
}: {
  view: Extract<PersonaliseView, { phase: "working" }>;
  ownerName?: string;
  onBrowse: () => void;
}) {
  const elapsed = useElapsed(view.waking);
  return (
    <main className="grid min-h-[70vh] place-items-center p-6">
      <Card className="w-full max-w-md rounded-[18px] border shadow-none ring-0">
        <CardContent className="space-y-6 pt-6">
          <div className="flex items-start gap-3">
            <Loader2Icon
              className="mt-0.5 size-7 shrink-0 animate-spin text-muted-foreground"
              aria-hidden
            />
            <div className="space-y-1">
              <h1 className="font-display text-xl font-semibold leading-tight">
                Getting your personalised portfolio ready
              </h1>
              <p className="text-sm text-muted-foreground">
                {ownerName ? `${ownerName}'s` : "The"} entries, picked and
                introduced for what you're looking for.
              </p>
            </div>
          </div>

          <ol className="space-y-2.5 text-sm" aria-live="polite">
            {view.steps.map((step) => (
              <li
                key={step.key}
                className={cn(
                  "flex items-center gap-2.5",
                  step.state === "todo" && "text-muted-foreground",
                )}
              >
                <StepIcon state={step.state} />
                <span>{STEP_LABEL[step.key]}</span>
                {step.key === "wake" && step.state === "active" ? (
                  <span className="ml-auto tabular-nums text-muted-foreground">
                    {mmss(elapsed)}
                  </span>
                ) : null}
              </li>
            ))}
          </ol>

          {view.waking ? (
            <p className="rounded-[10px] bg-muted px-3 py-2 text-sm text-muted-foreground">
              The AI server was asleep. Waking it takes about three minutes.
            </p>
          ) : null}

          <div className="space-y-2">
            <StudioButton
              variant="outline"
              className="w-full"
              onClick={onBrowse}
            >
              Browse the standard portfolio meanwhile
            </StudioButton>
            <p className="text-center text-xs text-muted-foreground">
              A note at the top tells you when yours is ready.
            </p>
          </div>
        </CardContent>
      </Card>
    </main>
  );
}

/** The bar above a questionnaire result: on the way, ready to switch to, or why this is the
 *  standard portfolio. Sticky, so a visitor reading further down still sees it change. */
export function PersonaliseNotice({
  view,
  browsing,
  hasQuery,
  onShow,
  onAsk,
}: {
  view: PersonaliseView;
  browsing: boolean;
  hasQuery: boolean;
  onShow: () => void;
  /** Back to the questionnaire, for a fresh check. */
  onAsk: () => void;
}) {
  const bar =
    "flex flex-wrap items-center justify-center gap-x-3 gap-y-1 border-b px-4 py-2.5 text-sm backdrop-blur";
  let body = null;
  if (view.phase === "working" && browsing) {
    body = (
      <div className={cn(bar, "bg-background/95")}>
        <Loader2Icon className="size-4 animate-spin" aria-hidden />
        <span>
          Your personalised portfolio is on its way
          {view.waking
            ? ": the AI server is waking up, about three minutes"
            : ""}
          .
        </span>
      </div>
    );
  } else if (view.phase === "ready" && browsing) {
    body = (
      <div
        className={cn(
          bar,
          "bg-primary text-primary-foreground animate-in fade-in slide-in-from-top-2",
        )}
      >
        <SparklesIcon className="size-4" aria-hidden />
        <span className="font-medium">
          Your personalised portfolio is ready.
        </span>
        <StudioButton
          size="sm"
          variant="secondary"
          className="h-7"
          onClick={onShow}
        >
          Show it
        </StudioButton>
      </div>
    );
  } else if (view.phase === "unchecked") {
    body = (
      <div className={cn(bar, "bg-muted/80 text-muted-foreground")}>
        <span>
          The AI part starts with the tick on the questions page, so this is the
          standard portfolio.
        </span>
        <Button size="sm" variant="secondary" className="h-7" onClick={onAsk}>
          Back to the questions
        </Button>
      </div>
    );
  } else if (view.phase === "failed") {
    body = (
      <div className={cn(bar, "bg-muted/80 text-muted-foreground")}>
        The AI couldn't personalise this page just now, so this is the standard
        portfolio.
      </div>
    );
  } else if (view.phase === "ready" && hasQuery && view.ranked === null) {
    body = (
      <div className={cn(bar, "bg-muted/80 text-muted-foreground")}>
        Couldn't sort by your interest this time, so the entries are in their
        usual order.
      </div>
    );
  }
  // The wrapper is what sticks: a sticky child only sticks within its parent.
  return (
    <div role="status" aria-live="polite" className="sticky top-0 z-20">
      {body}
    </div>
  );
}
