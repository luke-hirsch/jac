import { useEffect, useEffectEvent, useId, useState } from "react";
import { Loader2Icon } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { solveCaptcha } from "@/lib/captcha";

type CheckState = "idle" | "solving" | "done" | "failed";

const NOTE: Record<CheckState, string> = {
  idle: "A second of arithmetic in your browser. No cookie, nothing from elsewhere.",
  solving: "Checking…",
  done: "Thanks, that's done.",
  failed: "The check didn't work. Tick it to try again.",
};

/** The proof-of-work check in jac's look (lib/captcha.ts, spa/captcha.py). Ticking it fetches a
 *  challenge and has the browser solve it; `onChange` gets the payload for a request's `captcha`
 *  field once it's solved, and null when a try fails. A solved check stays ticked: the payload is
 *  single use per purpose, so a parent that needs a fresh one remounts this (`key`). */
export function CaptchaCheck({
  onChange,
}: {
  onChange: (payload: string | null) => void;
}) {
  const id = useId();
  const [state, setState] = useState<CheckState>("idle");
  const report = useEffectEvent(onChange);

  // The solve belongs to the "solving" state: leaving the page aborts it.
  useEffect(() => {
    if (state !== "solving") return;
    const controller = new AbortController();
    solveCaptcha(controller.signal).then(
      (payload) => {
        if (controller.signal.aborted) return;
        setState("done");
        report(payload);
      },
      () => {
        if (controller.signal.aborted) return;
        setState("failed");
        report(null);
      },
    );
    return () => controller.abort();
  }, [state]);

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <Checkbox
          id={id}
          checked={state === "done"}
          disabled={state === "solving" || state === "done"}
          onCheckedChange={(v) => {
            if (v === true) setState("solving");
          }}
        />
        <Label htmlFor={id}>I'm a person</Label>
        {state === "solving" ? (
          <Loader2Icon
            className="size-4 animate-spin text-muted-foreground"
            aria-hidden
          />
        ) : null}
      </div>
      <p className="text-xs text-muted-foreground" aria-live="polite">
        {NOTE[state]}
      </p>
    </div>
  );
}
