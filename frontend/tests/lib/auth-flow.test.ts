import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  authErrorMessage,
  forgetPendingEmail,
  pendingEmail,
  rememberPendingEmail,
  resendErrorMessage,
  resolveAuthOutcome,
  verifyErrorMessage,
} from "@/lib/auth-flow";

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

describe("authErrorMessage", () => {
  it("shows what the password validators said, not a bare failure", () => {
    const e = new ApiError(400, {
      errors: [
        {
          code: "password_too_common",
          param: "password",
          message: "Too common.",
        },
      ],
    });
    const outcome = resolveAuthOutcome(e);
    if (outcome.kind !== "error") throw new Error(outcome.kind);
    expect(authErrorMessage(outcome.fields, "Signup failed")).toBe(
      "Too common.",
    );
  });

  it("prefers the general message, then password, then email", () => {
    const all = { __non_field__: "general", password: "pw", email: "mail" };
    expect(authErrorMessage(all, "x")).toBe("general");
    expect(authErrorMessage({ password: "pw", email: "mail" }, "x")).toBe("pw");
    expect(authErrorMessage({ email: "mail" }, "x")).toBe("mail");
  });

  it("falls back when nothing came back", () => {
    expect(authErrorMessage({}, "Signup failed")).toBe("Signup failed");
  });
});

describe("the pending address", () => {
  afterEach(() => vi.unstubAllGlobals());

  function stubStorage() {
    const store = new Map<string, string>();
    vi.stubGlobal("sessionStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => store.set(k, v),
      removeItem: (k: string) => store.delete(k),
    });
  }

  it("survives until it's forgotten", () => {
    stubStorage();
    expect(pendingEmail()).toBeNull();
    rememberPendingEmail("a@b.c");
    expect(pendingEmail()).toBe("a@b.c");
    forgetPendingEmail();
    expect(pendingEmail()).toBeNull();
  });

  it("is only a convenience: blocked storage never throws", () => {
    const blocked = () => {
      throw new Error("denied");
    };
    vi.stubGlobal("sessionStorage", {
      getItem: blocked,
      setItem: blocked,
      removeItem: blocked,
    });
    expect(() => rememberPendingEmail("a@b.c")).not.toThrow();
    expect(pendingEmail()).toBeNull();
    expect(() => forgetPendingEmail()).not.toThrow();
  });
});

describe("verifyErrorMessage", () => {
  it("shows the field error for a wrong code", () => {
    const e = new ApiError(400, {
      errors: [
        { code: "incorrect_code", param: "key", message: "Incorrect code." },
      ],
    });
    expect(verifyErrorMessage(e)).toBe("Incorrect code.");
  });

  it("says start over when the flow is gone: no code can work anymore", () => {
    expect(verifyErrorMessage(new ApiError(409, {}))).toContain("Start over");
  });

  it("has a fallback", () => {
    expect(verifyErrorMessage(new ApiError(500, {}))).toBe("Code rejected");
  });
});

describe("resendErrorMessage", () => {
  it("asks for a moment on a 429", () => {
    expect(resendErrorMessage(new ApiError(429, {}))).toContain("few seconds");
  });

  it("points at starting over on a 409: the resends ran out", () => {
    expect(resendErrorMessage(new ApiError(409, {}))).toContain("Start over");
  });

  it("has a fallback", () => {
    expect(resendErrorMessage(new Error("offline"))).toBe(
      "Could not send a new code",
    );
  });
});
