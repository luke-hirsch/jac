import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, csrfHeaders } from "@/lib/api";

/** The career-DB entry types an attachment may link to (one at most). */
export type EntryLink = "job" | "education" | "certification";

export type Attachment = {
  id: number;
  file: string; // media URL
  label: string;
  job: number | null;
  education: number | null;
  certification: number | null;
  created_at: string;
};

export type UploadVars = {
  file: File;
  label?: string;
  job?: number | null;
  education?: number | null;
  certification?: number | null;
};

const URL = "/api/jac/attachments/";
const KEY = ["jac", "attachments"] as const;

type ListResponse = Attachment[] | { results: Attachment[] };
const unwrap = (r: ListResponse) => ("results" in r ? r.results : r);

/** The user's whole attachment library, or filtered to one entry's linked files
 *  (`useAttachments({ education: 5 })`). */
export function useAttachments(filter?: Partial<Record<EntryLink, number>>) {
  const params = new URLSearchParams({ page_size: "100" });
  for (const [k, v] of Object.entries(filter ?? {})) {
    if (v != null) params.set(k, String(v));
  }
  return useQuery({
    queryKey: [...KEY, "list", filter ?? {}],
    queryFn: () => api<ListResponse>(`${URL}?${params}`).then(unwrap),
  });
}

function buildForm(vars: UploadVars): FormData {
  const fd = new FormData();
  fd.append("file", vars.file);
  if (vars.label) fd.append("label", vars.label);
  for (const link of ["job", "education", "certification"] as EntryLink[]) {
    if (vars[link] != null) fd.append(link, String(vars[link]));
  }
  return fd;
}

export function useUploadAttachment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: UploadVars) => {
      const res = await fetch(URL, {
        method: "POST",
        headers: csrfHeaders(), // NOT Content-Type — the browser sets the multipart boundary
        credentials: "same-origin",
        body: buildForm(vars),
      });
      if (!res.ok) throw new Error(`upload failed: HTTP ${res.status}`);
      return (await res.json()) as Attachment;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

/** Relabel or re-link an existing attachment (JSON PATCH — the file itself is immutable). */
export function useUpdateAttachment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: {
      id: number;
      body: Partial<Pick<Attachment, "label" | "job" | "education" | "certification">>;
    }) =>
      api<Attachment>(`${URL}${vars.id}/`, {
        method: "PATCH",
        body: JSON.stringify(vars.body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useDeleteAttachment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<void>(`${URL}${id}/`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

/** The link type set on an attachment (for a badge), or null when standalone. */
export function linkOf(a: Attachment): EntryLink | null {
  if (a.job != null) return "job";
  if (a.education != null) return "education";
  if (a.certification != null) return "certification";
  return null;
}
