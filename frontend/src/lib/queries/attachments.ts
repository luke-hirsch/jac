import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, csrfHeaders } from "@/lib/api";

export type Attachment = {
  id: number;
  application: number;
  file: string; // media URL
  label: string;
  position: number;
  created_at: string;
};

const key = (appId: number) => ["jac", "attachments", appId] as const;

export function useAttachments(appId: number) {
  return useQuery({
    queryKey: key(appId),
    queryFn: () =>
      api<Attachment[] | { results: Attachment[] }>(
        `/api/jac/attachments/?application=${appId}&page_size=100`,
      ).then((r) => ("results" in r ? r.results : r)),
  });
}

export function useUploadAttachment(appId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: {
      file: File;
      label: string;
      position: number;
    }) => {
      const fd = new FormData();
      fd.append("application", String(appId));
      fd.append("file", vars.file);
      fd.append("label", vars.label);
      fd.append("position", String(vars.position));
      const res = await fetch("/api/jac/attachments/", {
        method: "POST",
        headers: csrfHeaders(), // NOT Content-Type — the browser sets the multipart boundary
        credentials: "same-origin",
        body: fd,
      });
      if (!res.ok) throw new Error(`upload failed: HTTP ${res.status}`);
      return (await res.json()) as Attachment;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: key(appId) }),
  });
}

export function useDeleteAttachment(appId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      api(`/api/jac/attachments/${id}/`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: key(appId) }),
  });
}

export function useReorderAttachment(appId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: number; position: number }) =>
      api(`/api/jac/attachments/${vars.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ position: vars.position }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: key(appId) }),
  });
}
