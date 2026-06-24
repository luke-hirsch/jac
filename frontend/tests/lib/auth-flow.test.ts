import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api";
import { resolveAuthOutcome } from "@/lib/auth-flow";

/**
 * `resolveAuthOutcome` is the pure decision tree that collapses an allauth
 * success body or a thrown ApiError into one outcome the UI switches on.
 */
describe("resolveAuthOutcome", () => {
  it("treats a 409 as already authenticated", () => {
    expect(resolveAuthOutcome(new ApiError(409, {}))).toEqual({
      kind: "already_authenticated",
    });
  });

  it("routes a 401 to whichever flow is pending", () => {
    const verify = new ApiError(401, {
      data: { flows: [{ id: "verify_email", is_pending: true }] },
    });
    expect(resolveAuthOutcome(verify)).toEqual({ kind: "verify_email" });

    const mfa = new ApiError(401, {
      data: { flows: [{ id: "mfa_authenticate", is_pending: true }] },
    });
    expect(resolveAuthOutcome(mfa)).toEqual({ kind: "mfa_authenticate" });
  });

  it("falls back to the first flow when none is flagged pending", () => {
    const e = new ApiError(401, { data: { flows: [{ id: "mfa_authenticate" }] } });
    expect(resolveAuthOutcome(e)).toEqual({ kind: "mfa_authenticate" });
  });

  it("returns field errors for a 401 whose flow it can't route", () => {
    const e = new ApiError(401, {
      errors: [{ code: "invalid", param: "email", message: "bad" }],
    });
    expect(resolveAuthOutcome(e)).toEqual({ kind: "error", fields: { email: "bad" } });
  });

  it("maps other ApiErrors to field errors", () => {
    const e = new ApiError(400, { errors: [{ code: "x", message: "boom" }] });
    expect(resolveAuthOutcome(e)).toEqual({
      kind: "error",
      fields: { __non_field__: "boom" },
    });
  });

  it("reads an authenticated session body as success and passes it through", () => {
    const resp = {
      status: 200,
      meta: { is_authenticated: true },
      data: { user: { id: 1, email: "a@b.c" } },
    };
    expect(resolveAuthOutcome(resp)).toEqual({ kind: "authenticated", response: resp });
  });

  it("treats an unauthenticated body as a blank error", () => {
    expect(resolveAuthOutcome({ status: 200, meta: { is_authenticated: false } })).toEqual({
      kind: "error",
      fields: {},
    });
  });
});
