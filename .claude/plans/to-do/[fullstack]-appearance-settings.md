# [fullstack] Appearance settings — theme, contrast, CV accent

> Roadmap: **UI polish phase, items 3 + 4** — "could we add a dynamic colour to the cv … this could
> live in the user profile" and "dark and high contrast mode are part of the profile settings, but
> for now have no effect".
> Branch: `fullstack/appearance-settings`

## Context / goal

The profile page already offers **Theme** (`system`/`light`/`dark`) and **Contrast**
(`normal`/`high`), the serializer already accepts them (`spa/serializers.py:71–72`) and the model
already stores them (`spa/models.py:80–81`). They do **nothing**: no code ever puts `.dark` on
`<html>`, and `.contrast-high` does not exist in `index.css` at all. Three settings that save
successfully and change nothing is worse than not having them.

The CV accent is the same story from the other end: `LayoutSpec.colors.accent` exists and drives
the name, section titles and rules (`templates.tsx:94, 109`), but it is baked into the layout JSON
(`#1a5fb4`), so changing it means uploading a new layout template.

This guide makes all three real:

- an **appearance provider** that resolves `theme`/`contrast` into root classes, follows the system
  preference live when `theme = system`, and applies **before first paint** (a React effect alone
  flashes light on every load);
- a `.contrast-high` variable block that composes with `.dark`;
- `UserProfile.accent_color`, a swatch picker, and a **contrast clamp** so a pale accent can't make
  section titles unreadable — or vanish when a recruiter prints the CV in greyscale.

Two things deliberately **not** in scope: the Django landing page already handles
`prefers-color-scheme` itself (`spa/templates/spa/landing.html:23`), and there is no header toggle —
the profile is the control surface, anonymous portfolio visitors follow their system preference.

## Affected files

| path | why |
| --- | --- |
| `backend/spa/models.py` | `UserProfile.accent_color` + hex validator. |
| `backend/spa/migrations/0008_userprofile_accent_color.py` | **new** — the migration. |
| `backend/spa/serializers.py` | expose `accent_color`. |
| `frontend/src/lib/appearance.ts` | **new** — pure theme/contrast resolution + storage shape. |
| `frontend/src/lib/render/accent.ts` | **new** — pure hex validation, WCAG contrast, the clamp. |
| `frontend/src/components/appearance-provider.tsx` | **new** — applies classes, follows the system, syncs the profile. |
| `frontend/index.html` | pre-paint class script. |
| `frontend/src/index.css` | `.contrast-high` / `.dark.contrast-high` variable blocks + variant. |
| `frontend/src/routes/__root.tsx` | mount the provider. |
| `frontend/src/lib/queries/profile.ts` | `ProfileRow` gains `theme` / `contrast` / `accent_color`. |
| `frontend/src/routes/_authenticated/account/profile.tsx` | the accent picker. |
| `frontend/src/lib/render/spec.ts` | `applyAccent` + `useRenderSpec`. |
| `frontend/src/components/applications/export-card.tsx` | use `useRenderSpec`. |
| `frontend/src/components/applications/content-card.tsx` | use `useRenderSpec`. |

## The code

### 1. `backend/spa/models.py`

Add the import at the top (next to the other django imports):

```python
from django.core.validators import RegexValidator
```

and the field, right after `contrast` (line 81):

```python
    # The CV/letter accent (LayoutSpec.colors.accent). Blank = keep whatever the layout
    # template ships, which is the boring blue — a user picks a colour, they don't lose one.
    accent_color = models.CharField(
        max_length=7,
        blank=True,
        validators=[
            RegexValidator(
                r"^#[0-9a-fA-F]{6}$",
                "Use a 6-digit hex colour, e.g. #1a5fb4.",
            )
        ],
    )
```

### 2. `backend/spa/migrations/0008_userprofile_accent_color.py` (new)

```bash
cd backend && python manage.py makemigrations spa -n userprofile_accent_color
```

Expected: one `AddField` on `userprofile`. Don't hand-write it — but do read it before applying.

### 3. `backend/spa/serializers.py`

Add `"accent_color"` to the `fields` tuple (after `"contrast"`, line 72). Nothing else: the model
validator runs through DRF automatically, so a bad hex 400s with the field error.

### 4. `frontend/src/lib/appearance.ts` (new)

```ts
/**
 * Theme + contrast resolution. Pure on purpose: the same logic runs in three places —
 * the React provider, the pre-paint script in index.html (hand-inlined, keep them in
 * sync!), and the tests.
 */
export type Theme = "system" | "light" | "dark";
export type Contrast = "normal" | "high";
export type Appearance = { theme: Theme; contrast: Contrast };

export const APPEARANCE_KEY = "appearance";
export const DEFAULT_APPEARANCE: Appearance = {
  theme: "system",
  contrast: "normal",
};

/** `system` follows the OS; anything else is an explicit override. */
export function resolveTheme(theme: Theme, systemDark: boolean): "light" | "dark" {
  if (theme === "dark") return "dark";
  if (theme === "light") return "light";
  return systemDark ? "dark" : "light";
}

/**
 * The classes that belong on <html>. `.contrast-high` is additive — the CSS has a
 * `.dark.contrast-high` block for the combination, so both classes stay on together.
 */
export function themeClasses(a: Appearance, systemDark: boolean): string[] {
  const out: string[] = [];
  if (resolveTheme(a.theme, systemDark) === "dark") out.push("dark");
  if (a.contrast === "high") out.push("contrast-high");
  return out;
}

/** Whatever is in localStorage, defaulted field by field — a half-written or hand-edited
 *  value must never leave the app themeless. */
export function readAppearance(raw: string | null): Appearance {
  try {
    const v = JSON.parse(raw ?? "{}") as Partial<Appearance>;
    return {
      theme:
        v.theme === "dark" || v.theme === "light" || v.theme === "system"
          ? v.theme
          : DEFAULT_APPEARANCE.theme,
      contrast: v.contrast === "high" ? "high" : DEFAULT_APPEARANCE.contrast,
    };
  } catch {
    return { ...DEFAULT_APPEARANCE };
  }
}

/** All classes this module may ever set — the provider removes these before adding, so a
 *  setting change never leaves a stale class behind. */
export const ALL_APPEARANCE_CLASSES = ["dark", "contrast-high"] as const;
```

### 5. `frontend/src/lib/render/accent.ts` (new)

```ts
/**
 * The CV accent colour, kept legible. A user-chosen accent lands on section titles, the
 * name, and the section rules — a pale one turns those into invisible ink on white, and
 * dies completely when the CV is printed in greyscale. So the accent is clamped to a WCAG
 * AA contrast ratio against the page before it ever reaches the renderer.
 */
import type { LayoutSpec } from "./spec";

/** WCAG AA for normal text. Section titles are 1.2 × base — still "normal" size at 9pt. */
export const MIN_CONTRAST = 4.5;

/** `#abc` / `#aabbcc` / `aabbcc` → `#aabbcc`; anything else → null (fall back to the layout). */
export function normalizeHex(raw: string | null | undefined): string | null {
  const s = (raw ?? "").trim().replace(/^#/, "");
  if (/^[0-9a-fA-F]{3}$/.test(s))
    return `#${s[0]}${s[0]}${s[1]}${s[1]}${s[2]}${s[2]}`.toLowerCase();
  if (/^[0-9a-fA-F]{6}$/.test(s)) return `#${s}`.toLowerCase();
  return null;
}

function channels(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function toHex(rgb: [number, number, number]): string {
  return `#${rgb.map((c) => Math.round(c).toString(16).padStart(2, "0")).join("")}`;
}

/** WCAG relative luminance. */
export function luminance(hex: string): number {
  const [r, g, b] = channels(hex).map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** Contrast ratio against white — the CV page is white, always. */
export function contrastRatio(hex: string): number {
  return 1.05 / (luminance(hex) + 0.05);
}

/**
 * Darken until the accent clears MIN_CONTRAST on white. Multiplicative steps keep the hue
 * and saturation (a lemon yellow stays yellow, just olive enough to read); 40 steps of
 * 0.94 reach black, so the loop always terminates.
 */
export function readableAccent(raw: string, min = MIN_CONTRAST): string {
  const hex = normalizeHex(raw);
  if (!hex) return raw;
  let rgb = channels(hex) as [number, number, number];
  for (let i = 0; i < 40 && contrastRatio(toHex(rgb)) < min; i++) {
    rgb = [rgb[0] * 0.94, rgb[1] * 0.94, rgb[2] * 0.94];
  }
  return toHex(rgb);
}

/** The user's accent over the layout's, clamped. A blank/invalid accent changes nothing. */
export function applyAccent(spec: LayoutSpec, accent: string | undefined): LayoutSpec {
  const hex = normalizeHex(accent);
  if (!hex) return spec;
  return { ...spec, colors: { ...spec.colors, accent: readableAccent(hex) } };
}

/** Sober defaults for the picker — a CV is not a place to discover neon. */
export const ACCENT_PRESETS = [
  "#1a5fb4", // the layout default
  "#1c1c1c",
  "#26619c",
  "#1a7f56",
  "#8f4700",
  "#7a1f6b",
] as const;
```

### 6. `frontend/src/components/appearance-provider.tsx` (new)

```tsx
/**
 * Applies theme + contrast to <html>. Three sources, in priority order:
 *
 *  1. the signed-in user's profile (authoritative once it loads),
 *  2. localStorage (what the pre-paint script in index.html already applied),
 *  3. the system preference.
 *
 * The profile query only runs on the app host: on a `<handle>.` portfolio origin the
 * visitor is anonymous, and firing a 403 on every public page view is noise. Anonymous
 * visitors get the system preference, which is what they want anyway.
 */
import { useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { siteHost } from "@/lib/host";
import {
  ALL_APPEARANCE_CLASSES,
  APPEARANCE_KEY,
  readAppearance,
  themeClasses,
  type Appearance,
} from "@/lib/appearance";
import type { ProfileRow } from "@/lib/queries/profile";

const DARK_QUERY = "(prefers-color-scheme: dark)";

export function AppearanceProvider({ children }: { children: ReactNode }) {
  const onAppHost = siteHost().kind === "app";
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia(DARK_QUERY).matches,
  );
  const [appearance, setAppearance] = useState<Appearance>(() =>
    readAppearance(localStorage.getItem(APPEARANCE_KEY)),
  );

  // `theme: system` must keep following the OS while the tab is open, not just at load.
  useEffect(() => {
    const mq = window.matchMedia(DARK_QUERY);
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  // Same query key as the account page and useProfile() — one cache entry per session.
  const profile = useQuery({
    queryKey: ["profile"],
    queryFn: () => api<ProfileRow>("/api/spa/profile/"),
    enabled: onAppHost,
    retry: false,
  });

  const stored = profile.data
    ? { theme: profile.data.theme, contrast: profile.data.contrast }
    : null;

  // The profile wins once it arrives, and is mirrored into localStorage so the NEXT load
  // paints correctly on the first frame instead of flashing and correcting.
  useEffect(() => {
    if (!stored) return;
    setAppearance(stored);
    localStorage.setItem(APPEARANCE_KEY, JSON.stringify(stored));
  }, [stored?.theme, stored?.contrast]);

  useEffect(() => {
    const el = document.documentElement;
    el.classList.remove(...ALL_APPEARANCE_CLASSES);
    el.classList.add(...themeClasses(appearance, systemDark));
  }, [appearance, systemDark]);

  return children;
}
```

### 7. `frontend/index.html`

Inside `<head>`, **after** the `<title>`:

```html
    <script>
      // Pre-paint: a React effect runs after the first frame, which is a white flash on
      // every load in dark mode. Hand-inlined mirror of lib/appearance.ts themeClasses() —
      // change one, change the other.
      (function () {
        try {
          var a = JSON.parse(localStorage.getItem("appearance") || "{}");
          var dark =
            a.theme === "dark" ||
            (a.theme !== "light" &&
              window.matchMedia("(prefers-color-scheme: dark)").matches);
          if (dark) document.documentElement.classList.add("dark");
          if (a.contrast === "high")
            document.documentElement.classList.add("contrast-high");
        } catch (e) {}
      })();
    </script>
```

### 8. `frontend/src/index.css`

Next to the existing dark variant (line 7):

```css
@custom-variant contrast-high (&:is(.contrast-high *));
```

and, **after** the closing brace of the `.dark { … }` block (order matters — `.dark` and
`.contrast-high` have equal specificity, so source order decides which wins for a shared
variable; the combined `.dark.contrast-high` selector outranks both):

```css
/* High contrast: pure black on pure white, hard borders, no decorative greys. Composes
   with .dark via the combined selector below. */
.contrast-high {
  --background: oklch(1 0 0);
  --foreground: oklch(0 0 0);
  --card: oklch(1 0 0);
  --card-foreground: oklch(0 0 0);
  --popover: oklch(1 0 0);
  --popover-foreground: oklch(0 0 0);
  --primary: oklch(0 0 0);
  --primary-foreground: oklch(1 0 0);
  --secondary: oklch(0.94 0 0);
  --secondary-foreground: oklch(0 0 0);
  --muted: oklch(0.94 0 0);
  --muted-foreground: oklch(0.25 0 0);
  --accent: oklch(0.9 0 0);
  --accent-foreground: oklch(0 0 0);
  --destructive: oklch(0.44 0.24 27.3);
  --border: oklch(0 0 0);
  --input: oklch(0 0 0);
  --ring: oklch(0 0 0);
  --sidebar: oklch(1 0 0);
  --sidebar-foreground: oklch(0 0 0);
  --sidebar-border: oklch(0 0 0);
}

.dark.contrast-high {
  --background: oklch(0 0 0);
  --foreground: oklch(1 0 0);
  --card: oklch(0 0 0);
  --card-foreground: oklch(1 0 0);
  --popover: oklch(0 0 0);
  --popover-foreground: oklch(1 0 0);
  --primary: oklch(1 0 0);
  --primary-foreground: oklch(0 0 0);
  --secondary: oklch(0.18 0 0);
  --secondary-foreground: oklch(1 0 0);
  --muted: oklch(0.18 0 0);
  --muted-foreground: oklch(0.85 0 0);
  --accent: oklch(0.22 0 0);
  --accent-foreground: oklch(1 0 0);
  --destructive: oklch(0.75 0.2 22.2);
  --border: oklch(1 0 0);
  --input: oklch(1 0 0);
  --ring: oklch(1 0 0);
  --sidebar: oklch(0 0 0);
  --sidebar-foreground: oklch(1 0 0);
  --sidebar-border: oklch(1 0 0);
}
```

### 9. `frontend/src/routes/__root.tsx`

```tsx
import { HeadContent, Outlet, createRootRoute } from "@tanstack/react-router";
import { AppearanceProvider } from "@/components/appearance-provider";
import { Toaster } from "@/components/ui/sonner";

export const Route = createRootRoute({
  component: () => (
    <AppearanceProvider>
      <div className="min-h-screen">
        <HeadContent />
        <Outlet />
        <Toaster richColors position="top-right" />
      </div>
    </AppearanceProvider>
  ),
});
```

### 10. `frontend/src/lib/queries/profile.ts`

Add to `ProfileRow` (it is the shape the provider and the renderer both read):

```ts
  theme: "system" | "light" | "dark";
  contrast: "normal" | "high";
  accent_color: string; // "" = keep the layout's
```

### 11. `frontend/src/routes/_authenticated/account/profile.tsx`

**a.** the local `Profile` type gains `accent_color: string;` (after `contrast`, line 45), and the
zod schema gains `accent_color: z.string().regex(/^#[0-9a-fA-F]{6}$/).or(z.literal("")),`
(after `contrast`, line 68).

**b.** `initial` gains `accent_color: p.accent_color,` (after `contrast: p.contrast,`, line 123).

**c.** the picker, right after the `contrast` `<form.Field>` block (line 336):

```tsx
      <form.Field name="accent_color">
        {(field) => (
          <div className="space-y-1">
            <Label>CV accent colour</Label>
            <div className="flex flex-wrap items-center gap-2">
              {ACCENT_PRESETS.map((hex) => (
                <button
                  key={hex}
                  type="button"
                  title={hex}
                  aria-label={`accent ${hex}`}
                  onClick={() => {
                    field.handleChange(hex);
                    save("accent_color", hex);
                  }}
                  className={
                    "size-6 rounded-full ring-offset-2 " +
                    (field.state.value === hex ? "ring-2 ring-foreground" : "")
                  }
                  style={{ backgroundColor: hex }}
                />
              ))}
              <input
                type="color"
                className="size-6 cursor-pointer border-0 bg-transparent p-0"
                value={field.state.value || ACCENT_PRESETS[0]}
                onChange={(e) => field.handleChange(e.target.value)}
                onBlur={() => save("accent_color", field.state.value)}
              />
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  field.handleChange("");
                  save("accent_color", "");
                }}
              >
                Reset
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Used on the CV headline and section titles. Pale colours are darkened
              automatically so they stay readable — including in greyscale print.
            </p>
            <LineSaveHint s={fieldStates.accent_color} />
          </div>
        )}
      </form.Field>
```

with `import { ACCENT_PRESETS } from "@/lib/render/accent";` added to the imports.

### 12. `frontend/src/lib/render/spec.ts`

Append (and import `useMemo` from react, `useProfile` from `@/lib/queries/profile`, `applyAccent`
from `./accent`):

```ts
/**
 * The spec the renderer actually gets: the layout template with the user's accent applied
 * (clamped for contrast). Kept separate from `useLayoutSpec` so the *template* stays
 * cached under its own key while the accent — a per-user preference, not part of the
 * template — is layered on top.
 */
export function useRenderSpec(layout: LayoutRow | undefined): {
  data: LayoutSpec | undefined;
  isLoading: boolean;
} {
  const spec = useLayoutSpec(layout);
  const profile = useProfile();
  const accent = profile.data?.accent_color ?? "";
  const data = useMemo(
    () => (spec.data ? applyAccent(spec.data, accent) : undefined),
    [spec.data, accent],
  );
  return { data, isLoading: spec.isLoading || profile.isLoading };
}
```

### 13. the two consumers

In `export-card.tsx` (line 68) and `content-card.tsx` (line 75), swap `useLayoutSpec` for
`useRenderSpec` — same import path, same `.data` usage, no other change:

```tsx
  const spec = useRenderSpec(layout);
```

## Tests

**Step 0 — unskip.** This is not the active guide, so its tests land `.skip`-marked. Delete every
`.skip` in the three files below (and the `@skip` decorator in the Django test) before you code.

| file | covers |
| --- | --- |
| `frontend/tests/lib/appearance.test.ts` | `resolveTheme` (explicit beats system, `system` follows both ways), `themeClasses` (dark alone, contrast alone, both together, neither), `readAppearance` (missing key, malformed JSON, partial object, junk values → defaults). |
| `frontend/tests/lib/render-accent.test.ts` | `normalizeHex` (3-digit, 6-digit, with/without `#`, junk → null), `luminance`/`contrastRatio` sanity (white 1.0, black 21), `readableAccent` (the default blue passes through untouched, yellow is darkened until it clears 4.5, black stays black, invalid input is returned verbatim), `applyAccent` (blank/invalid = identity, valid = clamped accent, other spec fields untouched). |
| `backend/spa/tests/test_auth.py` | `UserProfileViewTests`: `accent_color` round-trips through PATCH; a malformed hex 400s with a field error; blank is allowed (means "use the layout's"). |

```bash
cd frontend && npx vitest run tests/lib/appearance.test.ts tests/lib/render-accent.test.ts
cd backend && python manage.py test spa.tests.test_auth.UserProfileViewTests
```

The provider itself is not unit-tested — components + hooks are still deferred until styling
settles (see memory `frontend-test-layout`); its logic lives in `lib/appearance.ts`, which is.

## Verification

1. `cd backend && python manage.py makemigrations spa -n userprofile_accent_color && python manage.py migrate`.
2. Both test commands above: red → green.
3. `cd frontend && npx tsc -b`.
4. Account → Profile → **Theme: Dark**. The page turns dark *immediately* (no reload). Reload:
   it comes up dark with **no white flash** — that's the pre-paint script. Check
   `localStorage.appearance` in devtools: `{"theme":"dark","contrast":"normal"}`.
5. **Theme: Follow system**, then flip macOS appearance in System Settings while the tab is open —
   the app follows without a reload.
6. **Contrast: High**, in both light and dark: borders become solid, muted text darkens/lightens,
   nothing becomes unreadable. Walk one form, one card, one dialog, and the sidebar.
7. Log out and open a `<handle>.localhost` portfolio page: no `/api/spa/profile/` request in the
   network tab (the host gate), and the page follows the system preference.
8. Pick a **CV accent** (try `#f4d03f`, a pale yellow) → Preview PDF: the name and section titles
   are visibly darkened, not pale-on-white. Print-preview in greyscale: still readable.
9. **Reset** the accent → the CV goes back to the layout's blue.
10. `PATCH /api/spa/profile/ {"accent_color": "red"}` via curl → 400 with a field error, not a 500.

## Results

<!-- human: raw test output, observed issues, what works -->
