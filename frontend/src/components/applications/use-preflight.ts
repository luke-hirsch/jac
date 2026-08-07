/**
 * Background page-fit measurement for the editor. Renders the real documents off-screen
 * (react-pdf into a Blob, never mounted) and reports what the export WILL do — which
 * entries get shortened, which get cut, which get pulled back in, how many pages, whether
 * the letter spills.
 *
 * Three things keep it cheap:
 *  - a debounce, so typing doesn't queue renders;
 *  - a run token, so a superseded measurement's result is discarded rather than raced in;
 *  - a module-level cache keyed by `preflightKey`, shared with the export card — pressing
 *    Download right after the editor settled reuses the measurement instead of redoing it.
 */
import { useEffect, useRef, useState } from "react";
import type { CvContent } from "@/lib/cv-doc";
import { stripSoftStub, type LetterMeta } from "@/lib/letter-doc";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import {
  effectiveCaps,
  fitContent,
  preflightKey,
  type PreflightResult,
} from "@/lib/render/fit";
import { isFavouriteLookup } from "@/lib/render/parts";
import type { LayoutSpec } from "@/lib/render/spec";
import { CvDocument, LetterDocument, pdfPages } from "@/lib/render/templates";

export type Preflight = {
  result: PreflightResult | null;
  letterPages: number | null;
  measuring: boolean;
};

const IDLE: Preflight = { result: null, letterPages: null, measuring: false };

const CACHE = new Map<string, Preflight>();
const CACHE_MAX = 12; // a handful of editor states; this is a measurement, not a store.

export function readPreflightCache(key: string): Preflight | undefined {
  return CACHE.get(key);
}

const DEBOUNCE_MS = 800;

export function usePreflight(args: {
  spec: LayoutSpec | undefined;
  db: CvEntriesResponse | undefined;
  content: CvContent; // active content, pre-cap
  sectionsOff: string[];
  name: string;
  contact: string;
  summary: string;
  meta: LetterMeta;
  body: string;
}): Preflight {
  const { spec, db, content, sectionsOff, name, contact, summary, meta, body } =
    args;
  // Only *finished* measurements are state; "which state am I looking at" is derived
  // below from the key, so the effect never sets state synchronously (the react-hooks
  // lint rejects that, and it would cascade a render per keystroke anyway).
  const [done, setDone] = useState<{ key: string; value: Preflight } | null>(
    null,
  );
  const token = useRef(0);

  const key = spec
    ? preflightKey({
        spec,
        content,
        sectionsOff,
        cvHeader: { name, contact, summary },
        letterBody: body,
        letterMeta: meta,
      })
    : "";

  useEffect(() => {
    if (!spec || !key || CACHE.has(key)) return;
    const mine = ++token.current;
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const result = await fitContent(
            content,
            effectiveCaps(spec.cv.max_entries, sectionsOff),
            spec.cv.detailed,
            spec.cv.pages,
            (c, demoted) =>
              pdfPages(
                CvDocument({
                  spec,
                  name,
                  content: c,
                  db,
                  demoted,
                  contact,
                  summary,
                }),
              ),
            isFavouriteLookup(db),
          );
          const stripped = stripSoftStub(body);
          const letterPages = stripped
            ? await pdfPages(LetterDocument({ spec, meta, body: stripped }))
            : null;
          if (token.current !== mine) return; // superseded
          const value: Preflight = { result, letterPages, measuring: false };
          if (CACHE.size >= CACHE_MAX) CACHE.delete(CACHE.keys().next().value!);
          CACHE.set(key, value);
          setDone({ key, value });
        } catch {
          if (token.current === mine) setDone({ key, value: IDLE });
        }
      })();
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // `key` already folds in content / header / letter: listing those objects would
    // re-fire the measurement on every identity change instead of every real change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, spec, db]);

  if (!spec) return IDLE;
  const cached = CACHE.get(key);
  if (cached) return cached;
  return done?.key === key
    ? done.value
    : { result: null, letterPages: null, measuring: true };
}
