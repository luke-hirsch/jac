import type { PortfolioItem } from "@/lib/queries/portfolio";
import { luminance, normalizeHex } from "@/lib/render/accent";

/** The public portfolio's "Studio" look: what of it is logic, so the tests/ regime covers it. The
 *  colours are index.css `.studio`, the drawing components/portfolio/. */

/* ---------- the accent ---------- */

/** The ground and the card the accent sits on as text. Keep them in step with index.css. */
export const STUDIO_GROUND = "#141210";
export const STUDIO_CARD = "#1f1c19";
/** A blank accent: the mock-up's apricot. */
export const STUDIO_ACCENT = "#f2a65a";
/** WCAG AA for normal text: the accent is the kicker, the figures, the link-list labels. */
export const MIN_ACCENT_CONTRAST = 4.5;

const rgbOf = (hex: string) =>
  [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const hexOf = (rgb: number[]) =>
  `#${rgb.map((c) => Math.round(c).toString(16).padStart(2, "0")).join("")}`;

/** The WCAG contrast ratio of two colours, either way round. */
export function contrastBetween(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * The owner's accent, readable on the dark ground. It's the same colour the CV uses
 * (`UserProfile.accent_color`), and a CV accent is picked dark, for white paper: here it's
 * lightened toward white until it clears AA on the cards, the lightest ground it sits on as
 * text. The ground is darker still, and the ink on an accent button is the ground colour, so
 * both of those pass too. Blank or junk: the Studio default. 40 steps of 8% reach near-white,
 * so the loop always ends; an accent that passes comes back as given.
 */
export function studioAccent(raw: string | null | undefined): string {
  const hex = normalizeHex(raw);
  if (!hex) return STUDIO_ACCENT;
  let rgb = rgbOf(hex);
  for (
    let i = 0;
    i < 40 && contrastBetween(hexOf(rgb), STUDIO_CARD) < MIN_ACCENT_CONTRAST;
    i++
  ) {
    rgb = rgb.map((c) => c + (255 - c) * 0.08);
  }
  return hexOf(rgb);
}

/* ---------- the page's sections ---------- */

const isBlock = (item: PortfolioItem, kind: string) =>
  item.type === "block" && item.kind === kind;

/** A call to action the page can act on: a label and somewhere to go. */
const isCta = (item: PortfolioItem) =>
  isBlock(item, "cta") &&
  Boolean(item.data?.label?.trim() && safeHref(item.data?.url));

const hasFigures = (item: PortfolioItem) =>
  isBlock(item, "figures") && (item.data?.figures?.length ?? 0) > 0;

export type StudioSections = {
  /** The hero's "now" card: the page's first call to action. */
  now: PortfolioItem | null;
  /** The tiles under the hero: the page's first key-figures block. */
  figures: PortfolioItem | null;
  /** The numbered cards: the page's highlights, without what the hero took. */
  highlights: PortfolioItem[];
  /** The rest, unnumbered. */
  more: PortfolioItem[];
  /** The big closing line: the last call to action. With only one it repeats the "now" card
   *  (`repeat`), which then shows its title and pitch only once, up top. */
  closing: { item: PortfolioItem; repeat: boolean } | null;
};

/** Split a page into the Studio's sections. Featured comes first, so "first" means the
 *  highlights before the rest. Skills leave the grids for the network, as before. A cta or
 *  figures block beyond the ones the hero and the closing take renders where it stands. */
export function studioSections(
  featured: PortfolioItem[],
  more: PortfolioItem[],
): StudioSections {
  const page = [...featured, ...more];
  const ctas = page.filter(isCta);
  const now = ctas[0] ?? null;
  const last = ctas.length > 1 ? ctas[ctas.length - 1] : null;
  const figures = page.find(hasFigures) ?? null;
  const taken = new Set(
    [now, last, figures].filter((i) => i !== null).map((i) => i.id),
  );
  const card = (i: PortfolioItem) => i.type !== "skill" && !taken.has(i.id);
  return {
    now,
    figures,
    highlights: featured.filter(card),
    more: more.filter(card),
    closing: last
      ? { item: last, repeat: false }
      : now
        ? { item: now, repeat: true }
        : null,
  };
}

/* ---------- small things the cards say ---------- */

/** "01", "02", … : a highlight's place in the owner's list. */
export function ordinal(n: number): string {
  return String(n).padStart(2, "0");
}

const TYPE_LABEL: Partial<Record<PortfolioItem["type"], string>> = {
  job: "Job",
  project: "Project",
  education: "Education",
  certification: "Certification",
  language: "Language",
};

/** The years an entry covers: "2021 – today", "2019 – 2021", one year when it starts and ends
 *  in the same one. A certification is a moment, its issue year. Nothing without a start. */
export function yearSpan(item: PortfolioItem): string {
  const from = item.started?.slice(0, 4);
  if (!from) return "";
  if (item.type === "certification") return from;
  const to = item.ended ? item.ended.slice(0, 4) : "today";
  return to === from ? from : `${from} – ${to}`;
}

/** The small line above a card's title: "Job · 2021 – today". Blocks have no type to name. */
export function metaLine(item: PortfolioItem): string {
  return [TYPE_LABEL[item.type], yearSpan(item)].filter(Boolean).join(" · ");
}

const OPENING_QUOTE = /^["“„«‘‚'»]/;

/** A quote in typographic marks, unless the owner already typed some. */
export function quoted(body: string): string {
  const text = body.trim();
  return OPENING_QUOTE.test(text) ? text : `“${text}”`;
}

/** Only addresses a visitor can follow without surprises. The backend checks these too; this
 *  keeps a bad row from ever becoming a `javascript:` link. */
export function safeHref(url: string | null | undefined): string | null {
  try {
    const u = new URL(url ?? "");
    return ["https:", "http:", "mailto:"].includes(u.protocol) ? u.href : null;
  } catch {
    return null;
  }
}

/** Where a link goes, in a word: the host without `www.`, the address of a mail link. */
export function hostLabel(url: string): string {
  try {
    const u = new URL(url);
    if (u.protocol === "mailto:") return decodeURIComponent(u.pathname);
    return u.hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

/** The closing line's address, written out: the mail address, or host and path. */
export function addressLabel(url: string): string {
  try {
    const u = new URL(url);
    if (u.protocol === "mailto:") return decodeURIComponent(u.pathname);
    const path = u.pathname === "/" ? "" : u.pathname.replace(/\/$/, "");
    return `${u.hostname.replace(/^www\./, "")}${path}`;
  } catch {
    return "";
  }
}

/** How an in-page jump scrolls: smoothly, unless the visitor asked for less motion. */
export function scrollMotion(): ScrollBehavior {
  return globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    ? "auto"
    : "smooth";
}
