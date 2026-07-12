/**
 * Pure helpers behind the letter refinement UI ([fullstack]-letter-refine-chat):
 * the selection popover's preset rewrite styles, the standard+ alias gate for the
 * ephemeral chat, and the request/seed builders. Everything DOM-free and unit-tested;
 * the components stay thin.
 */
import type { RunSummary } from "@/lib/queries/applications";
import type { AliasInfo } from "@/lib/queries/llm";

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

/** Aliases capable of refinement chat — a light (1B) model can't chat usefully. */
export function chatAliases(aliases: AliasInfo[]): AliasInfo[] {
  return aliases.filter((a) => a.strength !== "light");
}

/**
 * Which model refinement defaults to: the latest DONE run's alias when it is configured
 * and standard+ (that's the model that wrote the letter — the old rewrite button silently
 * used "default" instead, which was the bug), else the first standard+ alias, else null
 * (chat hidden; the popover falls back to "default").
 */
export function preferredRefineAlias(
  aliases: AliasInfo[],
  runs: RunSummary[],
): string | null {
  const capable = chatAliases(aliases);
  for (const r of runs) {
    if (r.status !== "done") continue;
    if (capable.some((a) => a.alias === r.alias)) return r.alias;
  }
  return capable[0]?.alias ?? null;
}

/** Opening user message when a highlighted passage is handed to the chat. */
export function seedDiscussion(selection: string): ChatMessage {
  return {
    role: "user",
    content: `Let's discuss this passage of my letter:\n\n"${selection}"`,
  };
}

export type ChatPayload = {
  alias: string;
  body: string;
  messages: ChatMessage[];
};

/** POST body for /applications/<pk>/chat/ — the draft body travels along so the model
 *  sees unsaved edits; nothing is persisted server-side. */
export function chatPayload(
  alias: string,
  body: string,
  messages: ChatMessage[],
): ChatPayload {
  return { alias, body, messages };
}
