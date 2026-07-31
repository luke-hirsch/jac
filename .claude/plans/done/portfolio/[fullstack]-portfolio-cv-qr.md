# [fullstack] portfolio CV QR — the link in the CV header

> **Portfolio phase, guide 4 of 5.** Roadmap: #1 portfolio generator (plan:
> `~/.claude/plans/fizzy-cooking-sparrow.md`). Requires guides 1–3 merged. Queued behind the
> active SPA-phase stack.
>
> **Step 0 — activation pass (AI):** cut branch `fullstack/portfolio-cv-qr` off `main`,
> re-verify anchors, land the red tests listed in **Tests**.

## Context / goal

Scenario 2 completes: an application gets its portfolio link from the export card, and the CV
header carries **"scan for the online portfolio"** — a QR image plus the plain URL in the
contact line (so the text layer / ATS keeps the link even where images are stripped). The link
is minted lazily (guide 1's idempotent `POST /api/jac/applications/<pk>/portfolio-link/`), the
URL is server-built from `FRONTEND_URL`, and the page freezes at the `sent` transition — all
already landed; this guide is the render/UX rung.

Two QR facts drive the code:

- `qrcode.react`'s `QRCodeSVG` (already used in `security/totp-panel.tsx`) is a **React DOM SVG
  component — react-pdf cannot render it**. The PDF needs a raster data-URL → new `qrcode`
  dependency, rendered at 512px and placed at ~18mm (crisp at print DPI). The SVG component
  still serves the on-screen preview.
- The QR `View` is **absolutely positioned** (top-right, inside the page margins) as the Page's
  first child: zero layout impact — page counts and the `fitCv` loop are untouched (same
  invariance argument as `HiddenInk`, templates.tsx:35-47) — and a non-fixed absolute element
  anchored at the top renders on page 1 only.

## Affected files

| file | why |
| --- | --- |
| `frontend/package.json` | + `qrcode` (raster data-URLs), + dev `@types/qrcode` |
| `frontend/src/lib/portfolio/qr.ts` | **new** — `qrDataUrl` helper |
| `frontend/src/lib/letter-doc.ts` | `contactLine` learns `portfolioUrl` (text-layer belt) |
| `frontend/src/lib/render/templates.tsx` | `Image` import, `cvStyles` QR styles, `CvPages` `portfolio` prop (flows into `CvDocument`/`ApplicationDocument` via `CvDocProps`) |
| `frontend/src/lib/queries/portfolio.ts` | + `PortfolioLinkRow` type, `createApplicationLink`, `revokePortfolioLink` |
| `frontend/src/components/applications/portfolio-link-section.tsx` | **new** — export-card section: create/copy/preview/revoke + include-toggle |
| `frontend/src/components/applications/export-card.tsx` | link/include state, thread URL+QR through `buildPdf` |

## The code

### 1. Dependency

```bash
cd frontend && npm i qrcode && npm i -D @types/qrcode
```

### 2. `frontend/src/lib/portfolio/qr.ts`

```ts
import QRCode from "qrcode";

/** Raster QR for react-pdf — <Image> can't take the qrcode.react SVG component.
 *  512px source drawn at ~18mm keeps modules crisp on print. */
export function qrDataUrl(url: string): Promise<string> {
  return QRCode.toDataURL(url, { margin: 0, width: 512 });
}
```

### 3. `frontend/src/lib/letter-doc.ts` — contact line

Replace `contactLine` (L81-88); existing callers pass `{ socials }` and are untouched:

```ts
export function contactLine(
  sender: Record<string, string>,
  opts: { socials: boolean; portfolioUrl?: string },
): string {
  const parts = [sender.email, sender.phone];
  if (opts.socials) parts.push(sender.website, sender.linkedin, sender.github);
  // Text-layer belt for the QR: ATS parsers and image-stripping viewers keep the link.
  if (opts.portfolioUrl) parts.push(opts.portfolioUrl);
  return parts.filter(Boolean).join(" · ");
}
```

### 4. `frontend/src/lib/render/templates.tsx`

Add `Image` to the react-pdf import (L12-20):

```tsx
import {
  Document,
  Image,
  Page,
  StyleSheet,
  Text,
  View,
  pdf,
  type DocumentProps,
} from "@react-pdf/renderer";
```

In `cvStyles` (L79), append to the `StyleSheet.create` object:

```tsx
    // Portfolio QR: absolute (zero layout impact — the fit loop and page counts are
    // invariant, same argument as HiddenInk) inside the page margins, top-right.
    qr: {
      position: "absolute",
      top: spec.page.margin[0],
      right: spec.page.margin[1],
      alignItems: "center",
    },
    qrImage: { width: mm(18), height: mm(18) },
    qrCaption: { fontSize: small * 0.9, color: spec.colors.muted, marginTop: 2 },
```

`CvPages` (L171-217) — new optional prop; being `Parameters<typeof CvPages>[0]`, it flows into
`CvDocument` and `ApplicationDocument.cv` with no further changes:

```tsx
export function CvPages({
  spec,
  name,
  content,
  db,
  contact,
  summary,
  hidden,
  portfolio,
}: {
  spec: LayoutSpec;
  name: string;
  content: CvContent;
  db: CvEntriesResponse | undefined;
  contact?: string;
  summary?: string;
  hidden?: string;
  portfolio?: { qr: string };
}) {
  const styles = cvStyles(spec);
  return (
    <Page size={spec.page.size} style={styles.page} wrap>
      {portfolio ? (
        <View style={styles.qr}>
          <Image src={portfolio.qr} style={styles.qrImage} />
          <Text style={styles.qrCaption}>online portfolio</Text>
        </View>
      ) : null}
      {/* Header order: name → bio → contact (Lukas's call, 2026-07-11). */}
      <Text style={styles.name}>{name}</Text>
      …(rest unchanged)…
```

(A very long name could run under the QR — the name spans the full width. Accept for now; the
layout knob would be a `paddingRight` on `styles.name` when `portfolio` is set.)

### 5. `frontend/src/lib/queries/portfolio.ts` — link row + calls

```ts
/** Mirrors spa PortfolioLinkSerializer (owner-side). */
export type PortfolioLinkRow = {
  id: number;
  slug: string;
  kind: "manual" | "application";
  title: string;
  intro: string;
  application: number | null;
  content: { featured: string[]; domains: string[]; hide_explore: boolean };
  revoked_at: string | null;
  url: string; // absolute, FRONTEND_URL-based — the QR encodes exactly this
  visits: number;
  created_at: string;
  updated_at: string;
};

/** Idempotent get-or-create — safe to call again after a reload. */
export function createApplicationLink(applicationId: number) {
  return api<PortfolioLinkRow>(
    `/api/jac/applications/${applicationId}/portfolio-link/`,
    { method: "POST" },
  );
}

export function revokePortfolioLink(id: number) {
  return api<PortfolioLinkRow>(
    `/api/spa/portfolio/manage/links/${id}/revoke/`,
    { method: "POST" },
  );
}
```

### 6. `frontend/src/components/applications/portfolio-link-section.tsx`

```tsx
import { useMutation } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { type ApplicationRow } from "@/lib/queries/applications";
import {
  createApplicationLink,
  revokePortfolioLink,
  type PortfolioLinkRow,
} from "@/lib/queries/portfolio";

/** Export-card section for the application's portfolio link. Link state lives in the
 *  parent (buildPdf needs it). Client-only state: after a reload the toggle starts
 *  off — enabling it again just returns the same link (server get-or-create). */
export function PortfolioLinkSection({
  app,
  link,
  onLink,
  includeQr,
  onIncludeQr,
}: {
  app: ApplicationRow;
  link: PortfolioLinkRow | null;
  onLink: (link: PortfolioLinkRow | null) => void;
  includeQr: boolean;
  onIncludeQr: (on: boolean) => void;
}) {
  const create = useMutation({
    mutationFn: () => createApplicationLink(app.id),
    onSuccess: (row) => {
      onLink(row);
      onIncludeQr(true);
    },
    onError: () => toast.error("Couldn't create the portfolio link"),
  });
  const revoke = useMutation({
    mutationFn: () => revokePortfolioLink(link!.id),
    onSuccess: () => {
      onLink(null);
      onIncludeQr(false);
      toast.success("Portfolio link revoked");
    },
    onError: () => toast.error("Couldn't revoke the link"),
  });

  if (!link) {
    return (
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Add a personalised portfolio link + QR to the CV header.
        </p>
        <Button
          variant="outline"
          size="sm"
          disabled={create.isPending}
          onClick={() => create.mutate()}
        >
          Add portfolio link
        </Button>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-4">
      <div className="rounded-md border p-2 bg-white">
        <QRCodeSVG value={link.url} size={72} />
      </div>
      <div className="flex-1 space-y-2 text-sm">
        <div className="flex items-center gap-2">
          <code className="truncate">{link.url}</code>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              navigator.clipboard.writeText(link.url);
              toast.success("Link copied");
            }}
          >
            Copy
          </Button>
        </div>
        <div className="flex items-center gap-2">
          <Checkbox
            id={`qr-${app.id}`}
            checked={includeQr}
            onCheckedChange={(v) => onIncludeQr(v === true)}
          />
          <Label htmlFor={`qr-${app.id}`}>Include QR in the CV header</Label>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="text-destructive"
          disabled={revoke.isPending}
          onClick={() => revoke.mutate()}
        >
          Revoke link
        </Button>
      </div>
    </div>
  );
}
```

(If `components/ui/checkbox.tsx` isn't in the shadcn set yet: `npx shadcn@latest add checkbox`.)

### 7. `frontend/src/components/applications/export-card.tsx`

New imports:

```tsx
import { PortfolioLinkSection } from "@/components/applications/portfolio-link-section";
import { qrDataUrl } from "@/lib/portfolio/qr";
import { type PortfolioLinkRow } from "@/lib/queries/portfolio";
```

State, beside the existing `scope`/`busy` (L65-66):

```tsx
  const [link, setLink] = useState<PortfolioLinkRow | null>(null);
  const [includeQr, setIncludeQr] = useState(false);
```

In `buildPdf` (L84-167), three touches:

```tsx
    const portfolioUrl = includeQr && link ? link.url : undefined;
    const contact = contactLine(meta.sender, { socials, portfolioUrl });
    …
    const portfolio = portfolioUrl
      ? { qr: await qrDataUrl(portfolioUrl) }
      : undefined;
```

then pass `portfolio={portfolio}` wherever `contact` already goes: the fit-measuring
`<CvDocument …/>` (L105-112 — included for strict measure/export parity, though the absolute
block is layout-invariant), the `scope === "cv"` `<CvDocument …/>` (L141-150), and the
`ApplicationDocument`'s `cv={{ …, portfolio }}` (L162).

Render the section in the card's JSX (beside the scope selector, before the export buttons):

```tsx
        <PortfolioLinkSection
          app={app}
          link={link}
          onLink={setLink}
          includeQr={includeQr}
          onIncludeQr={setIncludeQr}
        />
```

## Tests

Landed **red at activation** (step 0). Distributed into existing topic files:

- `frontend/tests/lib/letter-doc.test.ts` — additions: `contactLine` appends `portfolioUrl`
  after socials; omitted/blank URL → unchanged output; URL present with `socials: false`.
- `frontend/tests/lib/render-templates.test.ts` — additions (using the existing
  `tests/lib/_pdf-text.ts` extractor): a `CvDocument` with `portfolio` renders; the contact
  line's URL lands in the PDF text layer; **page count identical with and without the QR
  block** (the layout-invariance guarantee, mirroring the hidden-ink test).
- `frontend/tests/lib/portfolio-qr.test.ts` — **new**: `qrDataUrl` resolves to a
  `data:image/png;base64,` string; same input → same output.
- `backend/spa/tests/test_portfolio.py` — addition (if guide 1's set didn't already pin it):
  serializer `url` == `settings.FRONTEND_URL + "/portfolio/" + slug` (absolute, no trailing
  surprises).

Run: `cd frontend && npx vitest run tests/lib/letter-doc.test.ts tests/lib/render-templates.test.ts tests/lib/portfolio-qr.test.ts`

## Verification

1. Application detail → export card → **Add portfolio link** → URL + QR preview appear; the
   toggle is on. Copy → paste in a private window → the tailored portfolio renders (live
   `cv_content` while the application is draft).
2. Export PDF (cv + complete scopes): QR top-right on page 1 only, caption under it; the URL in
   the contact line; page count unchanged vs. toggle off. Scan the printed QR with a phone —
   it must resolve (uses `FRONTEND_URL`, so on the dev LAN use your machine's address).
3. Toggle off → export → no QR, no URL. Reload the page → toggle starts off; **Add portfolio
   link** again → same slug (idempotent).
4. Transition the application to sent → edit the CV content → the public page does NOT change
   (frozen). Revoke from the export card → public 404.
5. `npx vitest run` + `npx tsc -b` — clean.

## Results

_(human fills after testing)_
