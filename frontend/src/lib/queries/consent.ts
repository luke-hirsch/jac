import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

/** The kept "I agree" to data leaving the EU, one per kind of transfer (backend
 *  `llm_connector.TransferConsent`): your own API keys, or Claude tokens. Given once, like a
 *  cookie choice; withdrawing deletes the keys or tokens it covered. */
export type ConsentScope = "providers" | "claude";

export type Consent = { scope: ConsentScope; given_at: string | null };

const URLS: Record<ConsentScope, string> = {
  providers: "/api/llm/consent/",
  claude: "/api/jac/skill/consent/",
};

// What withdrawing deletes, so those lists refetch.
const COVERED: Record<ConsentScope, readonly string[]> = {
  providers: ["llm"],
  claude: ["jac", "skill", "tokens"],
};

const key = (scope: ConsentScope) => ["consent", scope] as const;

/* ---------- pure helpers (unit-tested) ---------- */

/** Whether the user agreed: the kept consent, or anything saved on it (a key or a token carries
 *  its own stamp, and only exists after an "I agree"). The card shows only when neither does. */
export function consentGiven(
  givenAt: string | null | undefined,
  stamps: (string | null | undefined)[],
): boolean {
  return Boolean(givenAt) || stamps.some(Boolean);
}

/* ---------- hooks ---------- */

export function useConsent(scope: ConsentScope) {
  return useQuery({
    queryKey: key(scope),
    queryFn: () => api<Consent>(URLS[scope]),
  });
}

export function useGiveConsent(scope: ConsentScope) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<Consent>(URLS[scope], { method: "POST" }),
    onSuccess: (consent) => qc.setQueryData(key(scope), consent),
  });
}

export function useWithdrawConsent(scope: ConsentScope) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<void>(URLS[scope], { method: "DELETE" }),
    onSuccess: () => {
      qc.setQueryData(key(scope), { scope, given_at: null });
      return qc.invalidateQueries({ queryKey: COVERED[scope] });
    },
  });
}
