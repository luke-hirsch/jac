import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";

export type AuthStatus =
  | "anonymous"
  | "authenticated"
  | "mfa_required"
  | "verify_email_required"
  | "reauth_required";

export type Flow = { id: string; providers?: string[]; is_pending?: boolean };

export type SessionResponse = {
  status: number;
  data?: {
    user?: { id: number; email: string; display?: string };
    flows?: Flow[];
    methods?: { id: string; at: number; email?: string }[];
  };
  meta?: { is_authenticated?: boolean };
};

export const SESSION_KEY = ["auth", "session"] as const;

export async function fetchSession(): Promise<SessionResponse> {
  try {
    return await api<SessionResponse>("/_allauth/browser/v1/auth/session");
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 410)) {
      return e.data as SessionResponse;
    }
    throw e;
  }
}

// DELETE /auth/session returns 401 on success — the body is the
// post-signout session payload and the status reflects "you are now
// unauthenticated". Treat that as success.
export async function signOut(): Promise<void> {
  try {
    await api("/_allauth/browser/v1/auth/session", { method: "DELETE" });
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return;
    throw e;
  }
}

export function useAuth() {
  const query = useQuery({
    queryKey: SESSION_KEY,
    queryFn: fetchSession,
    staleTime: 30_000,
  });
  const data = query.data;

  const flows = data?.data?.flows || [];
  const pending = flows.find((f) => f.is_pending)?.id;
  let status: AuthStatus = "anonymous";
  if (data?.meta?.is_authenticated) status = "authenticated";
  else if (pending === "verify_email") status = "verify_email_required";
  else if (pending === "mfa_authenticate") status = "mfa_required";
  else if (pending === "reauthenticate" || pending === "mfa_reauthenticate")
    status = "reauth_required";

  return { ...query, status, user: data?.data?.user, flows };
}

export function useInvalidateSession() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: SESSION_KEY });
}
