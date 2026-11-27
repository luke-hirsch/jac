import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { jobStatusLabel, runAiJob, type AiJob } from "./jobs";
import type { Page } from "./paginated";
import type { CvEntry, RunStatus, TailoredResult } from "./generations";
import {
  editableBody,
  letterMetaFromResult,
  type LetterMeta,
} from "@/lib/letter-doc";
import { mergePinned, pinnedIds, type CvContent } from "@/lib/cv-doc";

export type ApplicationStatus =
  | "draft"
  | "approved"
  | "sent"
  | "response"
  | "follow_up"
  | "inactive";

export type DeliveryMethod = "email" | "manual";
export type ResponseOutcome = "interview" | "offer" | "rejected" | "other";

export type PostingDetail = {
  id: number;
  title: string;
  posting_text: string;
  language: string;
  source_url: string;
  active: boolean;
  created_at: string;
  updated_at: string;
};

export type RunSummary = {
  id: number;
  status: RunStatus;
  stage: string;
  mode: string;
  provider: string;
  model: string;
  created_at: string;
};

export type ApplicationRow = {
  id: number;
  posting: number;
  posting_detail: PostingDetail;
  cv_content: Record<string, CvEntry[]>;
  letter_meta: Partial<LetterMeta>;
  cover_letter: string;
  layout: number;
  status: ApplicationStatus;
  deadline: string | null;
  delivery_method: DeliveryMethod | "";
  response_outcome: ResponseOutcome | "";
  notes: string;
  include_portfolio_link: boolean | null; // null = follow the profile's portfolio_link_by_default
  portfolio_link_on: boolean; // read-only: the choice above, else the default
  fit: number | null; // how well the user fits the posting, 1 to 5; null = not rated
  sent_by_system: boolean;
  approved_at: string | null;
  sent_at: string | null;
  responded_at: string | null;
  runs: RunSummary[];
  created_at: string;
  updated_at: string;
  pinned_entries: string[];
  sections_off: string[]; // sections switched off for this application
  attachments: number[]; // ordered CvAttachment ids appended to the export
};

export type ApplicationPatch = Partial<{
  cv_content: Record<string, CvEntry[]>;
  cover_letter: string;
  layout: number;
  letter_meta: LetterMeta;
  deadline: string | null;
  notes: string;
  pinned_entries?: string[];
  sections_off?: string[];
  attachments: number[];
  include_portfolio_link: boolean | null;
  fit: number | null;
}>;

/* ---------- pure helpers (unit-tested) ---------- */

/** Payload for creating an application from pasted posting text. */
export function toApplicationPayload(postingText: string): {
  posting_text: string;
} {
  return { posting_text: postingText.trim() };
}

/** The PATCH that "applies" a finished run's result onto the application.
 *  `currentCv` (the application's stored cv_content) donates its pinned
 *  entries — pins survive re-generation. */
export function runToApplicationPatch(
  result: TailoredResult,
  currentCv?: CvContent,
): ApplicationPatch {
  const cv = currentCv ? mergePinned(currentCv, result.cv) : result.cv;
  return {
    cv_content: cv,
    cover_letter: editableBody(result.cover_letter),
    letter_meta: letterMetaFromResult(result.cover_letter),
    pinned_entries: pinnedIds(cv),
  };
}

/** Where the export card's portfolio-link choice comes from, said beside the switch. */
export function portfolioLinkNote(
  include: boolean | null,
  byDefault: boolean | undefined,
): string {
  if (include !== null) return "Chosen for this application.";
  return byDefault ? "On by your default." : "Off by your default.";
}

export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  draft: "Draft",
  approved: "Approved",
  sent: "Sent",
  response: "Response",
  follow_up: "Follow-up sent",
  inactive: "Inactive",
};

export const OUTCOME_LABELS: Record<ResponseOutcome, string> = {
  interview: "Interview",
  offer: "Offer",
  rejected: "Rejected",
  other: "Other",
};

/** Every status, in lifecycle order (STATUS_LABELS' key order). */
export const APPLICATION_STATUSES = Object.keys(
  STATUS_LABELS,
) as ApplicationStatus[];

/** What the list files an application under: its status, except that a response saying no
 *  gets its own bucket, so the rejections don't hide among the answers still in play. */
export type ListStatus = ApplicationStatus | "rejected";

/** The list's buckets, in lifecycle order: Rejected right after Response. */
export const LIST_STATUS_LABELS: Record<ListStatus, string> = {
  draft: STATUS_LABELS.draft,
  approved: STATUS_LABELS.approved,
  sent: STATUS_LABELS.sent,
  response: STATUS_LABELS.response,
  rejected: OUTCOME_LABELS.rejected,
  follow_up: STATUS_LABELS.follow_up,
  inactive: STATUS_LABELS.inactive,
};

const LIST_STATUSES = Object.keys(LIST_STATUS_LABELS) as ListStatus[];

/** The bucket a row belongs in. The outcome only counts while the status is `response`. */
export function listStatus(row: {
  status: ApplicationStatus;
  response_outcome: ResponseOutcome | "";
}): ListStatus {
  return row.status === "response" && row.response_outcome === "rejected"
    ? "rejected"
    : row.status;
}

/** The list's `?status=`: a known bucket, anything else is no filter. */
export function parseStatusFilter(raw: unknown): ListStatus | undefined {
  return LIST_STATUSES.find((s) => s === raw);
}

/** The list's orders. `newest` is the default and stays out of the URL. */
export const SORT_LABELS = {
  newest: "Newest first",
  oldest: "Oldest first",
  title: "Title A–Z",
  deadline: "Next deadline",
  fit: "Best fit",
} as const;

export type ApplicationSort = keyof typeof SORT_LABELS;

export const APPLICATION_SORTS = Object.keys(SORT_LABELS) as ApplicationSort[];

/** The list's `?sort=`: a known order other than the default, anything else is the default. */
export function parseSort(
  raw: unknown,
): Exclude<ApplicationSort, "newest"> | undefined {
  return APPLICATION_SORTS.find(
    (s): s is Exclude<ApplicationSort, "newest"> => s !== "newest" && s === raw,
  );
}

export type ApplicationsSearch = {
  status?: ListStatus;
  q?: string;
  sort?: Exclude<ApplicationSort, "newest">;
};

/** validateSearch for the applications section (the list and, retained, the detail page).
 *  `q` stays as typed (the input writes it on every keystroke); only matching trims it. The
 *  router parses `?q=2026` as a number, so numbers come back as text. */
export function applicationsSearch(
  search: Record<string, unknown>,
): ApplicationsSearch {
  const q =
    typeof search.q === "string" || typeof search.q === "number"
      ? String(search.q)
      : "";
  return {
    status: parseStatusFilter(search.status),
    q: q || undefined,
    sort: parseSort(search.sort),
  };
}

/** What the search and the orders read of a row. */
type ListedApplication = {
  posting_detail: Pick<PostingDetail, "title" | "posting_text">;
  notes: string;
  created_at: string;
  deadline: string | null;
  fit: number | null;
};

/** Lower case, accents off, ß as ss: "München" finds "munchen", "Straße" finds "strasse". */
function fold(text: string): string {
  return text
    .toLocaleLowerCase("de")
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .replace(/ß/g, "ss");
}

/** The rows whose title, posting or notes hold every word of the search, in any order. */
export function searchApplications<T extends ListedApplication>(
  rows: T[],
  q: string | undefined,
): T[] {
  const words = fold(q ?? "")
    .split(/\s+/)
    .filter(Boolean);
  if (!words.length) return rows;
  return rows.filter((r) => {
    const text = fold(
      `${r.posting_detail.title}\n${r.posting_detail.posting_text}\n${r.notes}`,
    );
    return words.every((w) => text.includes(w));
  });
}

const titleOrder = new Intl.Collator("de", {
  sensitivity: "base",
  numeric: true,
});

/** A local YYYY-MM-DD, comparable with `deadline` as text. */
function localDay(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** A sorted copy. `deadline`: the coming ones (today included) soonest first, then the passed
 *  ones most recent first, then those without. `fit`: the best first, the unrated last. Ties
 *  go newest first. */
export function sortApplications<T extends ListedApplication>(
  rows: T[],
  sort: ApplicationSort | undefined,
  now: Date = new Date(),
): T[] {
  const newest = (a: T, b: T) => b.created_at.localeCompare(a.created_at);
  const today = localDay(now);
  const deadlineRank = (r: T) =>
    r.deadline === null ? 2 : r.deadline >= today ? 0 : 1;

  const compare: Record<ApplicationSort, (a: T, b: T) => number> = {
    newest,
    oldest: (a, b) => -newest(a, b),
    title: (a, b) => {
      const at = a.posting_detail.title.trim();
      const bt = b.posting_detail.title.trim();
      if (!at || !bt) return Number(!at) - Number(!bt) || newest(a, b);
      return titleOrder.compare(at, bt) || newest(a, b);
    },
    deadline: (a, b) => {
      const rank = deadlineRank(a) - deadlineRank(b);
      if (rank || a.deadline === null || b.deadline === null)
        return rank || newest(a, b);
      const byDay =
        deadlineRank(a) === 0
          ? a.deadline.localeCompare(b.deadline)
          : b.deadline.localeCompare(a.deadline);
      return byDay || newest(a, b);
    },
    fit: (a, b) => (b.fit ?? 0) - (a.fit ?? 0) || newest(a, b),
  };
  return [...rows].sort(compare[sort ?? "newest"]);
}

type Bucketed = Parameters<typeof listStatus>[0];

/** The filter's options: each bucket the list holds, in lifecycle order, with its count. */
export function statusCounts(
  rows: Bucketed[],
): { status: ListStatus; count: number }[] {
  return LIST_STATUSES.map((status) => ({
    status,
    count: rows.filter((r) => listStatus(r) === status).length,
  })).filter((s) => s.count > 0);
}

export function filterByStatus<T extends Bucketed>(
  rows: T[],
  status: ListStatus | undefined,
): T[] {
  return status ? rows.filter((r) => listStatus(r) === status) : rows;
}
/* ---------- query hooks ---------- */

const URL = "/api/jac/applications/";
const KEY = ["jac", "applications"] as const;

export function useApplications() {
  return useQuery({
    queryKey: [...KEY, "list"],
    // One page is plenty for a personal application tracker (PAGE_SIZE 100).
    queryFn: async () => (await api<Page<ApplicationRow>>(URL)).results,
    // A batch is on its way through the queue: keep its badges moving while the page is open.
    refetchInterval: (query) =>
      query.state.data?.some(hasActiveRun) ? ACTIVE_RUNS_POLL_MS : false,
  });
}

const ACTIVE_RUNS_POLL_MS = 10_000;

function hasActiveRun(row: ApplicationRow): boolean {
  const latest = row.runs[0];
  return latest?.status === "pending" || latest?.status === "running";
}

/** Several postings in one paste, separated by a line of three or more dashes. */
export function splitPostings(text: string): string[] {
  return text
    .split(/^\s*-{3,}\s*$/m)
    .map((posting) => posting.trim())
    .filter(Boolean);
}

/** The postings to queue as a batch, or none for an ordinary single application. Only ever behind
 *  the explicit "several" checkbox: an ad can contain a line of dashes and still be one ad, and
 *  splitting it would spend the daily cap on fragments. */
export function postingsToBatch(text: string, several: boolean): string[] {
  const postings = several ? splitPostings(text) : [];
  return postings.length > 1 ? postings : [];
}

/** Queue several postings at once. All of them fit today's budget or none starts (429). */
export function useCreateApplicationsBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (postings: string[]) =>
      api<ApplicationRow[]>(`${URL}batch/`, {
        method: "POST",
        body: JSON.stringify({ postings }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useApplication(id: number | undefined) {
  return useQuery({
    queryKey: [...KEY, "detail", id],
    queryFn: () => api<ApplicationRow>(`${URL}${id}/`),
    enabled: id !== undefined,
  });
}

export function useCreateApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (postingText: string) =>
      api<ApplicationRow>(URL, {
        method: "POST",
        body: JSON.stringify(toApplicationPayload(postingText)),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useUpdateApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: ApplicationPatch }) =>
      api<ApplicationRow>(`${URL}${id}/`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useDeleteApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<void>(`${URL}${id}/`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

/** Rewrite one passage: a queued job, so it also works while the AI has to wake first. `jobLabel`
 *  says so beside the spinner. */
export function useRewriteParagraph() {
  const [jobLabel, setJobLabel] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: ({
      id,
      text,
      instruction,
      language,
      addressForm,
    }: {
      id: number;
      text: string;
      instruction?: string;
      /** The LETTER's language + form of "you" (letter_meta): a rewrite keeps both. */
      language?: string;
      addressForm?: string;
    }) =>
      runAiJob(
        () =>
          api<AiJob<{ text: string }>>(`${URL}${id}/rewrite/`, {
            method: "POST",
            body: JSON.stringify({
              text,
              instruction: instruction ?? "",
              language: language ?? "",
              address_form: addressForm ?? "",
            }),
          }),
        { onUpdate: (job) => setJobLabel(jobStatusLabel(job)) },
      ),
    onSettled: () => setJobLabel(null),
  });
  return { ...mutation, jobLabel };
}

/** Shorten the whole letter body to a word budget the CLIENT measured (`shortenTarget`).
 *  Sends no provider/model — the server falls back to the user's executor. Throws when the
 *  model yields nothing, like the rewrite. */
export function useShortenLetter() {
  const [jobLabel, setJobLabel] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: ({
      id,
      body,
      targetWords,
      language,
      addressForm,
    }: {
      id: number;
      body: string;
      targetWords: number;
      language: string;
      addressForm?: string;
    }) =>
      runAiJob(
        () =>
          api<AiJob<{ body: string }>>(`${URL}${id}/shorten/`, {
            method: "POST",
            body: JSON.stringify({
              body,
              target_words: targetWords,
              language,
              address_form: addressForm ?? "",
            }),
          }),
        { onUpdate: (job) => setJobLabel(jobStatusLabel(job)) },
      ),
    onSettled: () => setJobLabel(null),
  });
  return { ...mutation, jobLabel };
}

export type TransitionPayload = {
  to: ApplicationStatus;
  delivery_method?: DeliveryMethod;
  response_outcome?: ResponseOutcome;
  note?: string;
};

/** Drive the application through its guarded lifecycle. 400 on an illegal move — the
 *  server owns the legal-move map; the UI only offers the currently-legal buttons. */
export function useTransitionApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: TransitionPayload }) =>
      api<ApplicationRow>(`${URL}${id}/transition/`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

// Chat is streamed SSE now ([fullstack]-chat-assistant-rework) — RefineChat talks to
// /applications/<pk>/chat/ directly via a raw fetch (api() buffers the whole body,
// which defeats a stream), so there is no query-layer mutation for it here.
