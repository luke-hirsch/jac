import { api } from "@/lib/api";
import { publicPaths } from "@/lib/queries/portfolio";

/** Ask for the model server ahead of the request that needs it: a cold start is about 3 minutes. For
 *  a person's action, never a page load: crawlers load pages, and a false wake costs about 20 minutes
 *  of tower time. Fire and forget: nothing waits on it, and nothing shows if it fails. The server holds
 *  the real limits (llm_connector/tasks.py); this only keeps typing from posting on every keystroke. */
const QUIET_MS = 5 * 60 * 1000;
const lastSent = new Map<string, number>();

function knock(url: string): void {
  const now = Date.now();
  const last = lastSent.get(url);
  if (last !== undefined && now - last < QUIET_MS) return;
  lastSent.set(url, now);
  api(url, { method: "POST" }).catch(() => {});
}

/** Someone signed in is about to edit or generate. The server skips users on their own key. */
export const prewarmForEditing = () => knock("/api/llm/prewarm/");

/** A visitor has started an owner's questionnaire: the rank and the AI intro are coming. */
export const prewarmForVisitor = (handle: string) =>
  knock(publicPaths.prewarm(handle));
