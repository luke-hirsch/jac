/** The block editor's pure half: a block row as the form's state, the form as the request body
 * per kind, and the server's limits checked before anything goes out (a 50 MB upload that's
 * refused after a minute is worse than a toast now). The rules themselves live in
 * spa/blocks.py and the serializer; this mirrors them, and the server still decides. */

import { ApiError } from "@/lib/api";
import type {
  BlockData,
  BlockFigure,
  BlockKind,
  BlockLinkItem,
  PortfolioBlockRow,
} from "@/lib/queries/portfolio";

export const BLOCK_KINDS: { value: BlockKind; label: string }[] = [
  { value: "text", label: "Text" },
  { value: "image", label: "Image" },
  { value: "quote", label: "Quote" },
  { value: "figures", label: "Key figures" },
  { value: "cta", label: "Call to action" },
  { value: "linklist", label: "Link list" },
  { value: "media", label: "Audio or video" },
];

export function kindLabel(kind: BlockKind): string {
  return BLOCK_KINDS.find((k) => k.value === kind)?.label ?? kind;
}

const MB = 1024 * 1024;

/** spa/blocks.py and PortfolioBlockSerializer, in one place on this side. */
export const BLOCK_LIMITS = {
  by: 100,
  role: 150,
  figuresMin: 2,
  figuresMax: 4,
  figureValue: 12,
  figureLabel: 60,
  ctaLabel: 40,
  itemsMin: 1,
  itemsMax: 12,
  itemTitle: 120,
  itemNote: 200,
  url: 500,
  mediaBytes: 50 * MB,
  imageBytes: 5 * MB,
} as const;

/** What the file picker offers, and the file names the editor lets through. The server sniffs
 *  the bytes either way; this only spares a long upload of a `.mov` it would refuse. */
export const MEDIA_ACCEPT =
  "video/mp4,video/webm,audio/mpeg,audio/mp4,audio/ogg,.mp4,.m4v,.webm,.mp3,.m4a,.ogg,.oga,.opus";
const MEDIA_EXTENSIONS = MEDIA_ACCEPT.split(",").filter((a) =>
  a.startsWith("."),
);

/** Which files a kind takes (spa.blocks.FILE_FIELDS). A media block's image is its poster. */
export function takesFile(kind: BlockKind, field: "image" | "media"): boolean {
  if (field === "media") return kind === "media";
  return kind === "image" || kind === "media";
}

/** The stored files the chosen kind won't show. The server keeps them (a kind switch loses
 *  nothing), so the editor says they're still there. */
export function unseenFiles(
  block: PortfolioBlockRow | null,
  kind: BlockKind,
): ("image" | "media")[] {
  const out: ("image" | "media")[] = [];
  if (block?.image && !takesFile(kind, "image")) out.push("image");
  if (block?.media && !takesFile(kind, "media")) out.push("media");
  return out;
}

/** The editor's state. Every kind's own fields are kept side by side, so switching the kind
 *  back and forth while editing loses nothing; only the chosen kind's fields are sent. */
export type BlockForm = {
  kind: BlockKind;
  title: string;
  body: string;
  alt_text: string;
  domains: number[];
  links: string[];
  favourite: boolean;
  order: number;
  is_active: boolean;
  by: string;
  role: string;
  figures: BlockFigure[];
  label: string;
  url: string;
  items: Required<BlockLinkItem>[];
};

export type BlockFiles = { image?: File | null; media?: File | null };

export const emptyFigure = (): BlockFigure => ({ value: "", label: "" });
export const emptyItem = (): Required<BlockLinkItem> => ({
  title: "",
  url: "",
  note: "",
});

/** Pad a list with blank rows up to `min`, so a new figures block opens with two rows to fill. */
function padded<T>(rows: T[], min: number, blank: () => T): T[] {
  return [
    ...rows,
    ...Array.from({ length: Math.max(0, min - rows.length) }, blank),
  ];
}

export function formFromBlock(block: PortfolioBlockRow | null): BlockForm {
  const data: BlockData = block?.data ?? {};
  return {
    kind: block?.kind ?? "text",
    title: block?.title ?? "",
    body: block?.body ?? "",
    alt_text: block?.alt_text ?? "",
    domains: block?.domains ?? [],
    links: block?.links ?? [],
    favourite: block?.favourite ?? false,
    order: block?.order ?? 0,
    is_active: block?.is_active ?? true,
    by: data.by ?? "",
    role: data.role ?? "",
    figures: padded(
      (data.figures ?? []).map((f) => ({ ...f })),
      BLOCK_LIMITS.figuresMin,
      emptyFigure,
    ),
    label: data.label ?? "",
    url: data.url ?? "",
    items: padded(
      (data.items ?? []).map((i) => ({ ...i, note: i.note ?? "" })),
      BLOCK_LIMITS.itemsMin,
      emptyItem,
    ),
  };
}

/** The kind's `data`, trimmed, without the blank rows the editor keeps around for typing. */
export function blockData(form: BlockForm): BlockData {
  switch (form.kind) {
    case "quote":
      return { by: form.by.trim(), role: form.role.trim() };
    case "figures":
      return {
        figures: form.figures
          .map((f) => ({ value: f.value.trim(), label: f.label.trim() }))
          .filter((f) => f.value || f.label),
      };
    case "cta":
      return { label: form.label.trim(), url: form.url.trim() };
    case "linklist":
      return {
        items: form.items
          .map((i) => ({
            title: i.title.trim(),
            url: i.url.trim(),
            note: i.note.trim(),
          }))
          .filter((i) => i.title || i.url || i.note),
      };
    default:
      return {};
  }
}

const isWebUrl = (url: string, schemes: string[]) => {
  const scheme = url.slice(0, url.indexOf(":")).toLowerCase();
  if (!schemes.includes(scheme)) return false;
  if (scheme === "mailto")
    return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(url.slice(7).split("?")[0]);
  try {
    return Boolean(new URL(url).hostname);
  } catch {
    return false;
  }
};

const tooLong = (value: string, max: number) => value.length > max;

function sizeText(bytes: number): string {
  return `${Math.round(bytes / MB)} MB`;
}

/** What's wrong before saving, in the order the form shows it. Empty = good to go. `stored`
 *  says which files the block already has, so an edit needn't upload them again. */
export function blockProblems(
  form: BlockForm,
  files: BlockFiles,
  stored: { image: boolean; media: boolean } = { image: false, media: false },
): string[] {
  const out: string[] = [];
  const body = form.body.trim();
  const data = blockData(form);
  const L = BLOCK_LIMITS;
  switch (form.kind) {
    case "text":
      if (!body) out.push("A text block needs a body.");
      break;
    case "image":
      if (!files.image && !stored.image)
        out.push("An image block needs an image.");
      break;
    case "quote":
      if (!body) out.push("A quote block needs the quote.");
      if (!data.by) out.push("Say who said it.");
      else if (tooLong(data.by, L.by))
        out.push(`Who said it: at most ${L.by} characters.`);
      if (tooLong(data.role ?? "", L.role))
        out.push(`Their role: at most ${L.role} characters.`);
      break;
    case "figures": {
      const rows = data.figures ?? [];
      if (rows.length < L.figuresMin || rows.length > L.figuresMax)
        out.push(`Key figures take ${L.figuresMin} to ${L.figuresMax} rows.`);
      rows.forEach((f, i) => {
        if (!f.value || !f.label)
          out.push(`Figure ${i + 1} needs a value and a label.`);
        if (tooLong(f.value, L.figureValue))
          out.push(
            `Figure ${i + 1}: the value is at most ${L.figureValue} characters.`,
          );
        if (tooLong(f.label, L.figureLabel))
          out.push(
            `Figure ${i + 1}: the label is at most ${L.figureLabel} characters.`,
          );
      });
      break;
    }
    case "cta":
      if (!data.label) out.push("The button needs a label.");
      else if (tooLong(data.label, L.ctaLabel))
        out.push(`The button label is at most ${L.ctaLabel} characters.`);
      if (!data.url) out.push("The button needs a link.");
      else if (
        tooLong(data.url, L.url) ||
        !isWebUrl(data.url, ["https", "mailto"])
      )
        out.push("The button's link must start with https:// or mailto:.");
      break;
    case "linklist": {
      const rows = data.items ?? [];
      if (rows.length < L.itemsMin || rows.length > L.itemsMax)
        out.push(`A link list takes ${L.itemsMin} to ${L.itemsMax} links.`);
      rows.forEach((item, i) => {
        if (!item.title) out.push(`Link ${i + 1} needs a title.`);
        else if (tooLong(item.title, L.itemTitle))
          out.push(
            `Link ${i + 1}: the title is at most ${L.itemTitle} characters.`,
          );
        if (tooLong(item.url, L.url) || !isWebUrl(item.url, ["https", "http"]))
          out.push(`Link ${i + 1} must start with https:// or http://.`);
        if (tooLong(item.note ?? "", L.itemNote))
          out.push(
            `Link ${i + 1}: the note is at most ${L.itemNote} characters.`,
          );
      });
      break;
    }
    case "media":
      if (!files.media && !stored.media)
        out.push("A media block needs an audio or video file.");
      break;
  }
  const media = takesFile(form.kind, "media") ? files.media : null;
  if (media) {
    const name = media.name.toLowerCase();
    if (!MEDIA_EXTENSIONS.some((ext) => name.endsWith(ext)))
      out.push("Only MP4 or WebM video and MP3, M4A or Ogg audio.");
    if (media.size > L.mediaBytes)
      out.push(`The file is over ${sizeText(L.mediaBytes)}.`);
  }
  const image = takesFile(form.kind, "image") ? files.image : null;
  if (image && image.size > L.imageBytes)
    out.push(`The image is over ${sizeText(L.imageBytes)}.`);
  return out;
}

/** The request body. JSON when no file goes along; multipart when one does, with `data` as
 *  JSON text (DRF's JSONField reads both). Only the files the kind takes are sent.
 *  Caveat: multipart can't say "no domains" or "no links", so clearing them in the same save
 *  as a file upload isn't possible; save the tag change on its own. */
export function blockRequestBody(
  form: BlockForm,
  files: BlockFiles = {},
): string | FormData {
  const fields = {
    kind: form.kind,
    title: form.title,
    body: form.body,
    alt_text: form.alt_text,
    domains: form.domains,
    links: form.links,
    favourite: form.favourite,
    order: form.order,
    is_active: form.is_active,
    data: blockData(form),
  };
  const image = takesFile(form.kind, "image") ? files.image : null;
  const media = takesFile(form.kind, "media") ? files.media : null;
  if (!image && !media) return JSON.stringify(fields);

  const fd = new FormData();
  fd.set("kind", fields.kind);
  fd.set("title", fields.title);
  fd.set("body", fields.body);
  fd.set("alt_text", fields.alt_text);
  fd.set("favourite", String(fields.favourite));
  fd.set("order", String(fields.order));
  fd.set("is_active", String(fields.is_active));
  fd.set("data", JSON.stringify(fields.data));
  for (const d of fields.domains) fd.append("domains", String(d));
  for (const l of fields.links) fd.append("links", l);
  if (image) fd.set("image", image);
  if (media) fd.set("media", media);
  return fd;
}

/** What a failed save says. The server's own message where there is one (its 400s name the
 *  field), and plain words for what nginx or the throttle answer instead. */
export function saveErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 413)
      return `The upload is too big: audio and video up to ${sizeText(BLOCK_LIMITS.mediaBytes)}, images up to ${sizeText(BLOCK_LIMITS.imageBytes)}.`;
    if (error.status === 429)
      return "That's today's upload limit. Try again tomorrow.";
    if (error.status === 400 && error.data && typeof error.data === "object") {
      for (const value of Object.values(
        error.data as Record<string, unknown>,
      )) {
        const message = Array.isArray(value) ? value[0] : value;
        if (typeof message === "string" && message) return message;
      }
    }
  }
  return "Couldn't save the block.";
}

/** "video" or "audio", from the stored file's extension (the server names it by its bytes). */
export function mediaKind(
  url: string | null | undefined,
): "video" | "audio" | null {
  const ext = (url ?? "").split("?")[0].split(".").pop()?.toLowerCase();
  if (ext === "mp4" || ext === "webm") return "video";
  if (ext === "mp3" || ext === "m4a" || ext === "ogg") return "audio";
  return null;
}

/** One line for the blocks list, so a block without a body still says what it holds. */
export function blockSummary(block: PortfolioBlockRow): string {
  const data = block.data ?? {};
  switch (block.kind) {
    case "quote":
      return data.by ? `“${block.body}” (${data.by})` : block.body;
    case "figures":
      return (data.figures ?? [])
        .map((f) => `${f.value} ${f.label}`)
        .join(" · ");
    case "cta":
      return data.label ? `${data.label} → ${data.url ?? ""}` : block.body;
    case "linklist": {
      const items = data.items ?? [];
      const n = items.length;
      return `${n} ${n === 1 ? "link" : "links"}: ${items.map((i) => i.title).join(", ")}`;
    }
    case "media": {
      const kind = mediaKind(block.media);
      const ext = (block.media ?? "")
        .split("?")[0]
        .split(".")
        .pop()
        ?.toUpperCase();
      const what = kind
        ? `${kind === "video" ? "Video" : "Audio"} (${ext})`
        : "No file";
      return block.body ? `${what}: ${block.body}` : what;
    }
    default:
      return block.body;
  }
}

/** Replace row `i` with `patch` merged in. */
export function updateAt<T>(rows: T[], i: number, patch: Partial<T>): T[] {
  return rows.map((row, n) => (n === i ? { ...row, ...patch } : row));
}

export function removeAt<T>(rows: T[], i: number): T[] {
  return rows.filter((_, n) => n !== i);
}
