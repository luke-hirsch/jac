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
