import { readFileSync } from "node:fs";

/**
 * Runs index.html's inline pre-paint script, the hand-inlined mirror of lib/appearance.ts
 * themeClasses() and lib/brand.ts rememberedBrand(), against a stubbed browser, and reports what
 * it put on <html>. Not a test file: appearance.test.ts and brand.test.ts use it to keep the
 * mirror and the modules in step.
 */

const html = readFileSync(new URL("../../index.html", import.meta.url), "utf8");
const inline = /<script>([\s\S]*?)<\/script>/.exec(html);
if (!inline)
  throw new Error("index.html has no inline <script>: fix the test helper");
const SCRIPT = inline[1];

export type PrePaint = { classes: string[]; brand: string | null };

export function runPrePaint({
  stored = {},
  systemDark = false,
  storageThrows = false,
}: {
  /** localStorage, key → value. */
  stored?: Record<string, string>;
  systemDark?: boolean;
  /** A private window or blocked site data: every localStorage call throws. */
  storageThrows?: boolean;
}): PrePaint {
  const classes: string[] = [];
  const props = new Map<string, string>();
  const document = {
    documentElement: {
      classList: { add: (...names: string[]) => classes.push(...names) },
      style: {
        setProperty: (name: string, value: string) => props.set(name, value),
      },
    },
  };
  const localStorage = {
    getItem: (key: string) => {
      if (storageThrows) throw new Error("SecurityError");
      return key in stored ? stored[key] : null;
    },
  };
  const window = { matchMedia: () => ({ matches: systemDark }) };
  new Function("document", "localStorage", "window", SCRIPT)(
    document,
    localStorage,
    window,
  );
  return { classes, brand: props.get("--brand") ?? null };
}
