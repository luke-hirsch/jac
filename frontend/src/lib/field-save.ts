/**
 * Pure logic behind line-by-line ("per-field") saving in the CRUD editors.
 *
 * Editing an existing row PATCHes each field as you leave it, instead of one
 * whole-form save at the end — a bad input is flagged on the field that caused
 * it, the moment it happened. Creation still submits the whole form (the row
 * has to exist before it can be patched).
 */
import { ApiError } from "@/lib/api";

export type FieldSaveState =
  | { state: "saving" }
  | { state: "saved" }
  | { state: "error"; message: string };

/**
 * The DRF error message for one field, from an ApiError body shaped
 * `{field: ["msg", …]}`. Falls back to `non_field_errors`/`detail`, then to a
 * generic message — never null: a failed save must always say something.
 */
export function drfFieldError(err: unknown, field: string): string {
  if (err instanceof ApiError && err.data && typeof err.data === "object") {
    const data = err.data as Record<string, unknown>;
    for (const key of [field, "non_field_errors", "detail"]) {
      const val = data[key];
      const msg = Array.isArray(val) ? val[0] : val;
      if (typeof msg === "string" && msg) return msg;
    }
  }
  return "Save failed";
}

/**
 * Should a blur/change actually PATCH? Not when the client-side validator
 * already rejects the value (the zod error is showing — sending it would just
 * duplicate the noise), and not when the value never changed since the last
 * successful send (blur happens constantly while tabbing through a form).
 */
export function shouldSend(
  value: unknown,
  lastSent: unknown,
  clientErrors: Array<unknown>,
): boolean {
  if (clientErrors.some((e) => e != null && e !== false)) return false;
  return JSON.stringify(value) !== JSON.stringify(lastSent);
}
