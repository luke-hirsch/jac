import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Page } from "./paginated";
import type { CvEntry, RunStatus, TailoredResult } from "./generations";

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
}>;

/* ---------- pure helpers (unit-tested) ---------- */

/** Payload for creating an application from pasted posting text. */
export function toApplicationPayload(postingText: string): { posting_text: string } {
  return { posting_text: postingText.trim() };
}

/** The PATCH that "applies" a finished run's result onto the application. */
export function runToApplicationPatch(result: TailoredResult): ApplicationPatch {
  return {
    cv_content: result.cv,
    cover_letter: result.cover_letter.text,
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
