/** The native-visitor questionnaire — a flat form now (was a hardcoded branch tree).
 *  Domains come from the owner's real taxonomy at runtime (GET /native/meta/), so this
 *  file holds only the pure form↔search mapping — the tests/ regime's sweet spot. */

export type ExploreSearch = {
  d?: string[];
  lucky?: boolean;
  q?: string;
  focus?: string;
  tone?: string;
};

export const DEFAULT_FOCUS = "balanced";
export const DEFAULT_TONE = "neutral";

export type QuestForm = {
  domains: string[];
  focus: string;
  tone: string;
  query: string;
};

export const EMPTY_FORM: QuestForm = {
  domains: [],
  focus: DEFAULT_FOCUS,
  tone: DEFAULT_TONE,
  query: "",
};

/** The answered questionnaire → the result search. ALWAYS carries focus+tone, so the
 *  result URL is never param-empty — that's how `/` (the handle-host home) distinguishes
 *  "answered" (show the result) from "ask me" (show the questionnaire). */
export function formToSearch(form: QuestForm): ExploreSearch {
  const search: ExploreSearch = { focus: form.focus, tone: form.tone };
  if (form.domains.length) search.d = form.domains;
  const q = form.query.trim();
  if (q) search.q = q;
  return search;
}

/** "I feel lucky" — random picks, no style/domains. */
export function luckySearch(): ExploreSearch {
  return { lucky: true };
}

/** Has the visitor answered? The home route shows the result when true, the questionnaire
 *  when false. focus/tone are always present on a real answer; lucky/d/q also count. */
export function hasAnswer(search: ExploreSearch): boolean {
  return Boolean(
    search.lucky ||
    search.focus ||
    search.tone ||
    (search.d && search.d.length) ||
    search.q,
  );
}

/** Prefill the form from a search (e.g. re-opening the questionnaire on an answer). */
export function searchToForm(search: ExploreSearch): QuestForm {
  return {
    domains: search.d ?? [],
    focus: search.focus ?? DEFAULT_FOCUS,
    tone: search.tone ?? DEFAULT_TONE,
    query: search.q ?? "",
  };
}
