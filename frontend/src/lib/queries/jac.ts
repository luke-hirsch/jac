import {
  useQuery,
  useMutation,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { api } from "@/lib/api";
import { fetchPage, type ListParams } from "./paginated";

/* ---------- shared row types ---------- */

export type DomainRow = {
  id: number;
  name: string;
  description: string;
  is_default: boolean;
};

export type LocationRow = {
  id: number;
  city: string;
  country: string | null;
  street: string | null;
  zip: string | null;
  longitude: number | null;
  latitude: number | null;
};

export type EducationRow = {
  id: number;
  institution: string;
  field_of_study: string;
  started: string;
  ended: string | null;
  degree: string | null;
  grade: string | null;
  description: string;
  location: number | null;
};

export type CertificationRow = {
  id: number;
  name: string;
  issuer: string;
  issued_on: string | null;
  expires_on: string | null;
  credential_id: string;
  url: string;
  description: string;
};

export type SkillRow = {
  id: number;
  name: string;
  proficiency: "beginner" | "intermediate" | "advanced" | "expert";
  category: "technical" | "soft" | "domain" | "other";
  domains: number[];
  related_skills: number[];
  first_used: string | null;
  certification: number | null;
  years_of_experience: number | null;
  years_of_experience_override: number | null;
  description: string;
};

export type JobRow = {
  id: number;
  title: string;
  company: string;
  location: number | null;
  job_type: "ft" | "pt" | "ct" | "fl" | "in" | "vl";
  skills: number[];
  domains: number[];
  started: string;
  ended: string | null;
  url: string;
  description: string;
};

export type ProjectRow = {
  id: number;
  name: string;
  skills: number[];
  domains: number[];
  location: number | null;
  started: string | null;
  ended: string | null;
  url: string;
  description: string;
};

export type LanguageRow = {
  id: number;
  name: string;
  fluency: "native" | "fluent" | "professional" | "conversational" | "basic";
  description: string;
};

// factory

type Resource = {
  key: string;
  url: string;
};

const R = {
  domains: { key: "domains", url: "/api/jac/domains/" },
  locations: { key: "locations", url: "/api/jac/locations/" },
  education: { key: "education", url: "/api/jac/education/" },
  certifications: { key: "certifications", url: "/api/jac/certifications/" },
  skills: { key: "skills", url: "/api/jac/skills/" },
  jobs: { key: "jobs", url: "/api/jac/jobs/" },
  projects: { key: "projects", url: "/api/jac/projects/" },
  languages: { key: "languages", url: "/api/jac/languages/" },
} as const satisfies Record<string, Resource>;

function bulkUrl(key: ResourceKey) {
  return `${R[key].url}bulk/`;
}

export type ResourceKey = keyof typeof R;

function listKey(key: ResourceKey, params: ListParams) {
  return ["jac", key, "list", params] as const;
}
function detailKey(key: ResourceKey, id: number) {
  return ["jac", key, "detail", id] as const;
}

export function useList<T>(key: ResourceKey, params: ListParams = {}) {
  return useQuery({
    queryKey: listKey(key, params),
    queryFn: () => fetchPage<T>(R[key].url, params),
    placeholderData: (prev) => prev,
  });
}

export function useDetail<T>(key: ResourceKey, id: number | undefined) {
  return useQuery({
    queryKey: id ? detailKey(key, id) : ["jac", key, "detail", "none"],
    queryFn: () => api<T>(`${R[key].url}${id}/`),
    enabled: id !== undefined,
  });
}

function invalidateResource(qc: QueryClient, key: ResourceKey) {
  return qc.invalidateQueries({ queryKey: ["jac", key] });
}

export function useCreate<T, B = Partial<T>>(key: ResourceKey) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: B) =>
      api<T>(R[key].url, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => invalidateResource(qc, key),
  });
}

export function useUpdate<T, B = Partial<T>>(key: ResourceKey) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: B }) =>
      api<T>(`${R[key].url}${id}/`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: detailKey(key, vars.id) });
      invalidateResource(qc, key);
    },
  });
}

export function useDestroy(key: ResourceKey) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      api<void>(`${R[key].url}${id}/`, { method: "DELETE" }),
    onSuccess: () => invalidateResource(qc, key),
  });
}

export function useBulkDestroy(key: ResourceKey) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ids: number[]) =>
      api<{ deleted: number }>(bulkUrl(key), {
        method: "POST",
        body: JSON.stringify({ action: "delete", ids }),
      }),
    onSuccess: () => invalidateResource(qc, key),
  });
}

export function useBulkPatchDomains(
  key: Extract<ResourceKey, "skills" | "jobs" | "projects">,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      ids,
      add,
      remove,
    }: {
      ids: number[];
      add: number[];
      remove: number[];
    }) =>
      api<{ updated: number }>(bulkUrl(key), {
        method: "POST",
        body: JSON.stringify({ action: "patch_domains", ids, add, remove }),
      }),
    onSuccess: () => invalidateResource(qc, key),
  });
}
