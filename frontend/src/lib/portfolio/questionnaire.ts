import { z } from "zod";

/** The native-visitor questionnaire — a flat form now (was a hardcoded branch tree).
 *  Domains come from the owner's real taxonomy at runtime (GET /native/meta/), so this
 *  file holds only the pure form↔search mapping — the tests/ regime's sweet spot. */

export type ExploreSearch = {
  d?: string[];
  lucky?: boolean;
  q?: string;
  focus?: string;
  tone?: string;
  captcha?: string;
};
/** The answer as the URL carries it (no `q`, see splitQuery). An owner's home validates its search
 *  with it, and a native stamp stores the same shape. */
export const exploreSearchSchema = z.object({
  d: z.array(z.string()).optional(),
  lucky: z.boolean().optional(),
  focus: z.string().optional(),
  tone: z.string().optional(),
});

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
 *  result URL is never param-empty — that's how an owner's home distinguishes
 *  "answered" (show the result) from "ask me" (show the questionnaire). */
export function formToSearch(form: QuestForm): ExploreSearch {
  const search: ExploreSearch = { focus: form.focus, tone: form.tone };
  if (form.domains.length) search.d = form.domains;
  const q = form.query.trim();
  if (q) search.q = q;
  return search;
}

/** `q` is the visitor's own words, so it stays out of the URL: a reload would put it in the
 *  proxy's access log, a shared link in someone else's. It rides in history.state, which a
 *  reload keeps. The rest of the search is picks from the owner's own lists. */
export function splitQuery(search: ExploreSearch): {
  url: ExploreSearch;
  q: string | undefined;
  captcha: string | undefined;
} {
  const { q, captcha, ...url } = search;
  return { url, q, captcha };
}

/** "I feel lucky" — random picks, no style/domains. */
export function luckySearch(): ExploreSearch {
  return { lucky: true };
}

/** "Skip the questions": the owner's standard page, which is the questionnaire answered with
 *  nothing picked. spa.portfolio.standard_page_url is the same address, ?focus=balanced&tone=neutral. */
export function standardSearch(): ExploreSearch {
  return formToSearch(EMPTY_FORM);
}

/** Does this answer start the AI job (the ranking and the welcome)? Whenever the visitor told us
 *  something: a topic, what they're looking for, or a style other than the default, which the
 *  welcome is written in. The default answer is the standard page ("Skip the questions", and the
 *  address jac hands out for it), and it shows at once, without a waiting card; so does a lucky
 *  draw. */
export function needsPersonalise(search: ExploreSearch): boolean {
  if (search.lucky) return false;
  if (search.d?.length || search.q?.trim()) return true;
  return (
    (search.focus ?? DEFAULT_FOCUS) !== DEFAULT_FOCUS ||
    (search.tone ?? DEFAULT_TONE) !== DEFAULT_TONE
  );
}
export function checkedAnswer(
  form: QuestForm,
  captcha: string | null,
): ExploreSearch | null {
  const search = formToSearch(form);
  if (!needsPersonalise(search)) return search;
  return captcha ? { ...search, captcha } : null;
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
