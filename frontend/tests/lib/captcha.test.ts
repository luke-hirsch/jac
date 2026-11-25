import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createChallenge, verifySolution, type Challenge } from "altcha-lib";
import { deriveKey } from "altcha-lib/algorithms/web/pbkdf2";
import { ApiError } from "@/lib/api";
import { CAPTCHA_URL, isCaptchaRefusal, solveCaptcha } from "@/lib/captcha";

/**
 * The proof-of-work check before anything that wakes the model server (spa/captcha.py): fetch a
 * challenge, solve it in the browser, hand back the payload a request's `captcha` field carries.
 * A real solve, tiny: node has the same crypto.subtle the browser uses.
 */

const SECRET = "test-secret";

const json = (body: unknown, status = 200) =>
  ({
    ok: status < 400,
    status,
    headers: { get: () => "application/json" },
    json: async () => body,
  }) as unknown as Response;

/** A challenge as the server makes it (PBKDF2, deterministic mode), cheap enough for a test. */
const challengeAt = (counter: number, cost = 10): Promise<Challenge> =>
  createChallenge({
    algorithm: "PBKDF2/SHA-256",
    cost,
    counter,
    deriveKey,
    hmacSignatureSecret: SECRET,
  });

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("document", { cookie: "" });
});

afterEach(() => vi.unstubAllGlobals());

describe("solveCaptcha", () => {
  it("asks the public endpoint for a challenge", async () => {
    fetchMock.mockResolvedValueOnce(json(await challengeAt(2)));
    await solveCaptcha();
    const [[url, init]] = fetchMock.mock.calls;
    expect(url).toBe("/api/spa/public/captcha/");
    expect(CAPTCHA_URL).toBe(url);
    expect(init.method).toBe("GET");
  });

  it("solves it and hands back the payload the server verifies", async () => {
    const challenge = await challengeAt(3);
    fetchMock.mockResolvedValueOnce(json(challenge));
    const payload = await solveCaptcha();
    // The `captcha` field: base64 of {challenge, solution}, what altcha.verify_solution reads.
    const sent = JSON.parse(atob(payload));
    expect(sent.challenge).toEqual(challenge);
    expect(sent.solution.counter).toBe(3);
    const result = await verifySolution({
      challenge: sent.challenge,
      solution: sent.solution,
      deriveKey,
      hmacSignatureSecret: SECRET,
    });
    expect(result.verified).toBe(true);
  });

  it("stops when the signal aborts, mid-solve", async () => {
    // Out of reach: the solve runs until the abort.
    fetchMock.mockResolvedValueOnce(json(await challengeAt(5_000_000, 1)));
    const controller = new AbortController();
    const solving = solveCaptcha(controller.signal);
    setTimeout(() => controller.abort(), 50);
    await expect(solving).rejects.toThrow();
  });

  it("an aborted signal never starts the work", async () => {
    fetchMock.mockResolvedValueOnce(json(await challengeAt(5_000_000, 1)));
    const controller = new AbortController();
    controller.abort();
    await expect(solveCaptcha(controller.signal)).rejects.toThrow();
  });

  it("no challenge is an error, never a payload", async () => {
    fetchMock.mockResolvedValueOnce(json({ detail: "slow down" }, 429));
    await expect(solveCaptcha()).rejects.toBeInstanceOf(ApiError);
  });
});

describe("isCaptchaRefusal", () => {
  it("is the server's 400 on the check", () => {
    expect(
      isCaptchaRefusal(
        new ApiError(400, { captcha: ["The check is used already."] }),
      ),
    ).toBe(true);
  });

  it("is nothing else", () => {
    for (const error of [
      new ApiError(400, { query: ["Too long."] }),
      new ApiError(400, null),
      new ApiError(400, "captcha"),
      new ApiError(429, { captcha: ["?"] }),
      new Error("offline"),
      null,
      undefined,
    ]) {
      expect(isCaptchaRefusal(error)).toBe(false);
    }
  });
});
