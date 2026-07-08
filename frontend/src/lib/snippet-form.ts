import { z } from "@/lib/form";
import type { ResumeSnippetRow } from "@/lib/queries/jac";

/** The five backend `ResumeSnippet.Kind` choices, in author-facing order. */
export const SNIPPET_KINDS = [
  { value: "intro", label: "Introduction" },
  { value: "achievement", label: "Achievement" },
  { value: "value_statement", label: "Value statement" },
  { value: "closing", label: "Closing" },
  { value: "other", label: "Other" },
] as const;

export type SnippetKind = (typeof SNIPPET_KINDS)[number]["value"];

export const snippetSchema = z.object({
  title: z.string().min(1, "Required").max(200),
  content: z.string().min(1, "Required"),
  kind: z.enum(["intro", "achievement", "value_statement", "closing", "other"]),
  language: z.string().min(2, "e.g. en, de").max(8),
  domains: z.array(z.number()),
  skills: z.array(z.number()),
  job: z.number().nullable(),
  project: z.number().nullable(),
  is_active: z.boolean(),
});

export type SnippetInput = z.infer<typeof snippetSchema>;

/** Fresh blank form state (new arrays each call — never share references). */
export function emptySnippetInput(): SnippetInput {
  return {
    title: "",
    content: "",
    kind: "other",
    language: "en",
    domains: [],
    skills: [],
    job: null,
    project: null,
    is_active: true,
  };
}

/** Seed form state from an existing row, or blank defaults when creating. */
export function snippetToInput(row: ResumeSnippetRow | null): SnippetInput {
  if (!row) return emptySnippetInput();
  return {
    title: row.title,
    content: row.content,
    kind: row.kind,
    language: row.language,
    domains: [...row.domains],
    skills: [...row.skills],
    job: row.job,
    project: row.project,
    is_active: row.is_active,
  };
}

export type SnippetPayload = {
  title: string;
  content: string;
  kind: SnippetKind;
  language: string;
  domains: number[];
  skills: number[];
  job: number | null;
  project: number | null;
  is_active: boolean;
};

/** Assemble the request body: trim title, normalise language, empty lang → "en". */
export function toSnippetPayload(input: SnippetInput): SnippetPayload {
  return {
    title: input.title.trim(),
    content: input.content,
    kind: input.kind,
    language: (input.language.trim() || "en").toLowerCase(),
    domains: input.domains,
    skills: input.skills,
    job: input.job,
    project: input.project,
    is_active: input.is_active,
  };
}
