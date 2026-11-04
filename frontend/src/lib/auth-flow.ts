import { ApiError, allauthErrorsByField } from "@/lib/api";
import type { SessionResponse } from "@/lib/auth";

export type AuthOutcome =
  | { kind: "authenticated"; response: SessionResponse }
  | { kind: "already_authenticated" }
  | { kind: "verify_email" }
  | { kind: "mfa_authenticate" }
  | { kind: "error"; fields: Record<string, string> };

/**
 * Decode an allauth response (success body or thrown ApiError) into a single
 * outcome the call site can switch on. Pass `useMutation`'s `onSuccess` payload
 * or `onError` payload directly.
 */
export function resolveAuthOutcome(input: unknown): AuthOutcome {
  if (input instanceof ApiError) {
    if (input.status === 409) return { kind: "already_authenticated" };
    if (input.status === 401) {
      const body = input.data as
        | { data?: { flows?: { id: string; is_pending?: boolean }[] } }
        | undefined;
      const flows = body?.data?.flows ?? [];
      const pending = flows.find((f) => f.is_pending)?.id ?? flows[0]?.id;
      if (pending === "verify_email") return { kind: "verify_email" };
      if (pending === "mfa_authenticate") return { kind: "mfa_authenticate" };
    }
    return { kind: "error", fields: allauthErrorsByField(input) };
  }
  const resp = input as SessionResponse | undefined;
  if (resp?.meta?.is_authenticated) return { kind: "authenticated", response: resp };
  return { kind: "error", fields: {} };
}

/** The message for a failed login or signup. allauth answers under the field an error is about
 *  (Django's password validators under `password`), so `__non_field__` alone reads as a bare
 *  "failed". */
export function authErrorMessage(
  fields: Record<string, string>,
  fallback: string,
): string {
  return fields.__non_field__ ?? fields.password ?? fields.email ?? fallback;
}

const PENDING_EMAIL_KEY = "jac-pending-email";

/** allauth's pending flow doesn't say which address it mailed, so the form that knows leaves it
 *  for the verify page. Per tab, and a convenience only: without it the page says "your inbox". */
export function rememberPendingEmail(email: string): void {
  try {
    sessionStorage.setItem(PENDING_EMAIL_KEY, email);
  } catch {
    // private window, blocked storage: the page copes without it
  }
}

export function pendingEmail(): string | null {
  try {
    return sessionStorage.getItem(PENDING_EMAIL_KEY);
  } catch {
    return null;
  }
}

export function forgetPendingEmail(): void {
  try {
    sessionStorage.removeItem(PENDING_EMAIL_KEY);
  } catch {
    // nothing to clean up, then
  }
}

const FLOW_GONE = "This code is no longer valid. Start over to get a new one.";

/** A wrong code answers under `key`. A 409 means the flow itself is gone: too many wrong codes, or
 *  the 15 minutes ran out. No code will work now, so saying "rejected" would keep them trying. */
export function verifyErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) return FLOW_GONE;
  const fields = allauthErrorsByField(error);
  return fields.key ?? fields.__non_field__ ?? "Code rejected";
}

/** 429 = one mail per address per 10 s, and the code they already have keeps working. 409 = the
 *  resends ran out, or the flow is gone. */
export function resendErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429)
      return "A code just went out. Give it a few seconds, then try again.";
    if (error.status === 409)
      return "We can't send another code here. Start over to try again.";
  }
  return "Could not send a new code";
}
