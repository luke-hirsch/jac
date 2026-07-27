/** The native-visitor questionnaire. Hardcoded by design: ~4 nodes on a single-owner
 *  site don't earn a DB model, and a pure config is what the tests/ regime covers best.
 *
 *  ⚠ Domain names below must match the owner's jac Domain tags (case-insensitive —
 *  the backend joins forgivingly, but an unknown name silently widens to the full
 *  portfolio). Alignment checklist lives in this guide's Verification.
 */

export type QOption = {
  id: string;
  label: string;
  /** Chosen domains REPLACE the accumulated set — each step narrows, never unions. */
  domains?: string[];
  lucky?: true;
  /** Next node id; omitted = questionnaire done. */
  next?: string;
};

export type QNode = { id: string; prompt: string; options: QOption[] };

export const QUESTIONNAIRE: QNode[] = [
  {
    id: "start",
    prompt: "What do you want to learn about me?",
    options: [
      { id: "music", label: "Music", domains: ["music"], next: "music-angle" },
      {
        id: "software",
        label: "Software development",
        domains: ["software development", "IT", "web development"],
        next: "software-angle",
      },
      { id: "fashion", label: "Fashion", domains: ["fashion"] },
      { id: "lucky", label: "I feel lucky", lucky: true },
    ],
  },
  {
    id: "software-angle",
    prompt: "Which side of it?",
    options: [
      {
        id: "sw-all",
        label: "The whole picture",
        domains: ["software development", "IT", "web development"],
      },
      {
        id: "sw-web",
        label: "Web & product work",
        domains: ["web development"],
      },
      { id: "sw-ai", label: "AI & data", domains: ["AI"] },
    ],
  },
  {
    id: "music-angle",
    prompt: "Listening or making?",
    options: [
      { id: "mu-all", label: "Everything music", domains: ["music"] },
      { id: "mu-live", label: "On stage", domains: ["music", "performance"] },
    ],
  },
];

export type QuestState = { domains: string[]; lucky: boolean };

/** Deterministically replay `answers` (option ids, in order) through the config.
 *  Returns the accumulated state and the node still awaiting an answer (null = done).
 *  Unknown ids stop the walk — state so far survives. */
export function walk(
  nodes: QNode[],
  answers: string[],
): { state: QuestState; next: QNode | null } {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  let node: QNode | null = nodes[0] ?? null;
  const state: QuestState = { domains: [], lucky: false };
  for (const answer of answers) {
    if (!node) break;
    const opt = node.options.find((o) => o.id === answer);
    if (!opt) break;
    if (opt.lucky) state.lucky = true;
    if (opt.domains) state.domains = [...opt.domains];
    node = opt.next ? (byId.get(opt.next) ?? null) : null;
  }
  return { state, next: node };
}

/** /explore search params — also what a native stamp stores (+ the finale's q). */
export type ExploreSearch = { d?: string[]; lucky?: boolean; q?: string };

export function stateToSearch(
  state: QuestState,
  query?: string,
): ExploreSearch {
  const search: ExploreSearch = {};
  if (state.lucky) search.lucky = true;
  else if (state.domains.length) search.d = state.domains;
  const q = query?.trim();
  if (q) search.q = q;
  return search;
}

export function searchToState(search: ExploreSearch): QuestState {
  return { domains: search.d ?? [], lucky: search.lucky ?? false };
}
