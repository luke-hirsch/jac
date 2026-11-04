import { useEffect, useRef, useState } from "react";
import type { ApplicationRow } from "@/lib/queries/applications";
import type { CvEntry } from "@/lib/queries/generations";

/** How long freshly generated/applied content stays highlighted before fading back. */
export const FRESH_FOR_MS = 60_000;
/** A server-content change this soon after arming is attributed to the run/apply;
 *  anything later (e.g. the user's own save) must not light up. */
const ARM_WINDOW_MS = 15_000;

export type Fresh = { ids: Set<string>; letter: boolean };
const NONE: Fresh = { ids: new Set<string>(), letter: false };

/**
 * Ephemeral "what did the run just put into my application" marker. `arm()` is called
 * when a run finishes or its result is applied; the next server-content change within
 * the arm window is diffed against the previous server state, and the added entry ids
 * (+ a changed letter) glow for a minute. Pure view state — nothing is persisted.
 */
export function useFreshHighlight(app: ApplicationRow | undefined) {
  const [fresh, setFresh] = useState<Fresh>(NONE);
  const armedAt = useRef(0);
  const prev = useRef<{ cv: string; letter: string } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cvJson = JSON.stringify(app?.cv_content ?? {});
  const letter = app?.cover_letter ?? "";

  useEffect(() => {
    if (!app) return;
    const last = prev.current;
    prev.current = { cv: cvJson, letter };
    if (!last || (last.cv === cvJson && last.letter === letter)) return;
    const armed =
      armedAt.current !== 0 && Date.now() - armedAt.current <= ARM_WINDOW_MS;
    armedAt.current = 0;
    if (!armed) return;

    const before = new Set(
      Object.values(JSON.parse(last.cv) as Record<string, CvEntry[]>)
        .flat()
        .map((e) => e.id),
    );
    const ids = new Set<string>();
    for (const list of Object.values(app.cv_content ?? {})) {
      for (const e of list) if (!before.has(e.id)) ids.add(e.id);
    }
    const letterChanged = last.letter !== letter;
    if (ids.size === 0 && !letterChanged) return;

    setFresh({ ids, letter: letterChanged });
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setFresh(NONE), FRESH_FOR_MS);
  }, [app, cvJson, letter]);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  return { fresh, arm: () => (armedAt.current = Date.now()) };
}
