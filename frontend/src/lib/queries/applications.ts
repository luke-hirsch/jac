import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
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
  sent_by_system: boolean;
  approved_at: string | null;
  sent_at: string | null;
  responded_at: string | null;
  runs: RunSummary[];
  created_at: string;
  updated_at: string;
  pinned_entries: string[];
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
  attachments: number[];
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
/* ---------- query hooks ---------- */

const URL = "/api/jac/applications/";
const KEY = ["jac", "applications"] as const;

export function useApplications() {
  return useQuery({
    queryKey: [...KEY, "list"],
    // One page is plenty for a personal application tracker (PAGE_SIZE 100).
    queryFn: async () => (await api<Page<ApplicationRow>>(URL)).results,
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

export function useRewriteParagraph() {
  return useMutation({
    mutationFn: ({
      id,
      text,
      instruction,
    }: {
      id: number;
      text: string;
      instruction?: string;
    }) =>
      api<{ text: string }>(`${URL}${id}/rewrite/`, {
        method: "POST",
        body: JSON.stringify({
          text,
          instruction: instruction ?? "",
        }),
      }),
  });
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
