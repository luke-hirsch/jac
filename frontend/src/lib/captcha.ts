import { solveChallenge, type Challenge } from "altcha-lib";
import { deriveKey } from "altcha-lib/algorithms/web/pbkdf2";
import { ApiError, api } from "@/lib/api";

/** The proof-of-work check before anything that can wake the model server (spa/captcha.py,
 *  ALTCHA). The browser fetches a challenge and solves it here, on the main thread: PBKDF2 runs
 *  natively in crypto.subtle, and the solver yields to the page every 200 ms. No worker, no
 *  outside service. crypto.subtle needs a secure context: https, or localhost in dev. */

export const CAPTCHA_URL = "/api/spa/public/captcha/";

/** Fetch a challenge, solve it, and return what a request's `captcha` field carries: base64 of
 *  {challenge, solution}, the payload altcha.verify_solution reads. Rejects when the signal aborts,
 *  when no challenge comes (a 429, say), and when the solver gives up (90 s). */
export async function solveCaptcha(signal?: AbortSignal): Promise<string> {
  const challenge = await api<Challenge>(CAPTCHA_URL, { signal });
  // The solver takes a controller, not a signal: one follows the other.
  const controller = new AbortController();
  const stop = () => controller.abort();
  if (signal?.aborted) stop();
  signal?.addEventListener("abort", stop);
  try {
    const solution = await solveChallenge({ challenge, controller, deriveKey });
    if (!solution) throw new Error("The check wasn't solved.");
    return btoa(JSON.stringify({ challenge, solution }));
  } finally {
    signal?.removeEventListener("abort", stop);
  }
}

/** The server refused a request for its check: a 400 with a field error on `captcha`. Spent by an
 *  earlier request, expired, or missing. */
export function isCaptchaRefusal(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 400 &&
    typeof error.data === "object" &&
    error.data !== null &&
    "captcha" in error.data
  );
}
