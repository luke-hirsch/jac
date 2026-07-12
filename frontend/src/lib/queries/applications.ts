import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Page } from "./paginated";
import type { CvEntry, RunStatus, TailoredResult } from "./generations";
import {
  editableBody,
  letterMetaFromResult,
  type LetterMeta,
} from "@/lib/letter-doc";
import type { ChatPayload } from "@/lib/letter-chat";

export type ApplicationStatus =
  | "draft"
  | "sent"
  | "response"
  | "follow_up"
  | "inactive";

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
  grade: string;
  alias: string;
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
  runs: RunSummary[];
  created_at: string;
  updated_at: string;
};

export type ApplicationPatch = Partial<{
  cv_content: Record<string, CvEntry[]>;
  cover_letter: string;
  status: ApplicationStatus;
  layout: number;
  letter_meta: LetterMeta;
}>;

/* ---------- pure helpers (unit-tested) ---------- */

/** Payload for creating an application from pasted posting text. */
export function toApplicationPayload(postingText: string): {
  posting_text: string;
} {
  return { posting_text: postingText.trim() };
}

/** The PATCH that "applies" a finished run's result onto the application. */
export function runToApplicationPatch(
  result: TailoredResult,
): ApplicationPatch {
  return {
    cv_content: result.cv,
    cover_letter: editableBody(result.cover_letter),
    letter_meta: letterMetaFromResult(result.cover_letter),
  };
}

export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  draft: "Draft",
  sent: "Sent",
  response: "Response",
  follow_up: "Follow-up sent",
  inactive: "Inactive",
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
      alias,
    }: {
      id: number;
      text: string;
      instruction?: string;
      alias?: string;
    }) =>
      api<{ text: string }>(`${URL}${id}/rewrite/`, {
        method: "POST",
        body: JSON.stringify({
          text,
          instruction: instruction ?? "",
          alias: alias ?? "default",
        }),
      }),
  });
}

export type ChatReply = { reply: string; revision: string | null };

/** One turn of the ephemeral letter-refinement chat (sync, like rewrite) — the client
 *  holds the transcript; nothing is persisted server-side. */
export function useLetterChat() {
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: ChatPayload }) =>
      api<ChatReply>(`${URL}${id}/chat/`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
  });
}

export type FoundAddress = {
  address: Record<string, string>;
  sources: string[];
};

/** Web-search the employer's postal address (sync, like rewrite) — the caller
 *  merges the fields into its letter_meta draft; nothing is persisted here. */
export function useFindAddress() {
  return useMutation({
    mutationFn: ({ id, alias }: { id: number; alias: string }) =>
      api<FoundAddress>(`${URL}${id}/find_address/`, {
        method: "POST",
        body: JSON.stringify({ alias }),
      }),
  });
}
