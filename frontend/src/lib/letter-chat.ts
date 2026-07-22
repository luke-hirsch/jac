/**
 * Pure helpers behind the letter refinement UI ([fullstack]-letter-refine-chat):
 * the selection popover's preset rewrite styles and the request/seed builders.
 * Chat-assistant-rework: the endpoint streams SSE deltas and the request carries
 * an explicit executor pick again (blank = the user's default executor). Everything
 * DOM-free and unit-tested; the components stay thin.
 */

export type ChatRole = "user" | "assistant";
export type ChatMessage = { role: ChatRole; content: string };

export type RewriteStyle = { key: string; label: string; instruction: string };

/** The popover's preset styles — Apple-Pages-style writing tools for a cover letter.
 *  Instructions ride the same fabrication rule as ParagraphRewrite: reshape, never
 *  add facts. */
export const REWRITE_STYLES: RewriteStyle[] = [
  {
    key: "shorter",
    label: "Shorter",
    instruction:
      "Make it shorter and punchier without losing any factual claim.",
  },
  {
    key: "formal",
    label: "More formal",
    instruction:
      "Make the tone more formal and professional; keep every factual claim.",
  },
  {
    key: "natural",
    label: "More natural",
    instruction:
      "Make it sound more natural and human, less stiff; keep every factual claim.",
  },
];

/** Opening user message when a highlighted passage is handed to the chat. */
export function seedDiscussion(selection: string): ChatMessage {
  return {
    role: "user",
    content: `Let's discuss this passage of my letter:\n\n"${selection}"`,
  };
}

export type ChatPayload = {
  body: string;
  messages: ChatMessage[];
  provider?: string;
  model?: string;
};

/** POST body for /applications/<pk>/chat/ — the draft body travels along so the model
 *  sees unsaved edits; nothing is persisted server-side. An explicit executor pick is
 *  optional — omitted (or blank) means the server resolves the user's default. */
export function chatPayload(
  body: string,
  messages: ChatMessage[],
  pick: { provider: string; model: string } | null = null,
): ChatPayload {
  const p: ChatPayload = { body, messages };
  if (pick?.provider) p.provider = pick.provider;
  if (pick?.model) p.model = pick.model;
  return p;
}

export type SseEvent = { delta?: string; done?: boolean; error?: string };

/** One SSE line → event. Non-data lines (blank keepalives, comments) and broken
 *  JSON both come back null so the caller can just skip them. */
export function parseSseLine(line: string): SseEvent | null {
  if (!line.startsWith("data:")) return null;
  try {
    return JSON.parse(line.slice("data:".length).trim()) as SseEvent;
  } catch {
    return null;
  }
}

/** Client-side twin of the old server split: line-anchored 'REVISED BODY:', with
 *  same-line content accepted. A mid-line mention (not at line start) never splits. */
export function splitRevision(text: string): {
  reply: string;
  revision: string | null;
} {
  const m = /^[ \t]*REVISED BODY:[ \t]*\n?/m.exec(text);
  if (!m) return { reply: text.trim(), revision: null };
  return {
    reply: text.slice(0, m.index).trim(),
    revision: text.slice(m.index + m[0].length).trim() || null,
  };
}
