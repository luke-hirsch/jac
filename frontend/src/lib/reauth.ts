import { api, ApiError } from "@/lib/api";

/**
 * Run `fn`. If allauth says we need a fresh password, prompt for one and retry.
 *
 * Returns the result of `fn` on success, or throws on cancellation / unrelated errors.
 */
export async function withReauth<T>(
  fn: () => Promise<T>,
  prompt = "Confirm your password",
): Promise<T> {
  try {
    return await fn();
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 401) throw e;
    const body = e.data as {
      data?: { flows?: { id: string; is_pending?: boolean }[] };
    };
    // allauth lists `reauthenticate` as an *available* flow (no is_pending
    // field) on any 401 that a fresh password would resolve. The pending
    // flag only shows up when a multi-step stage is mid-flight, so we
    // can't gate on it. See allauth.account.internal.flows.reauthentication.
    const needsReauth = body.data?.flows?.some(
      (f) => f.id === "reauthenticate",
    );
    if (!needsReauth) throw e;

    const password = window.prompt(prompt);
    if (!password) throw new Error("Reauth cancelled", { cause: e });
    await api("/_allauth/browser/v1/auth/reauthenticate", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    return await fn();
  }
}
