import { z } from "zod";
import {
  exploreSearchSchema,
  type ExploreSearch,
} from "@/lib/portfolio/questionnaire";

const stampSchema = z.union([
  z.object({ kind: z.literal("link"), slug: z.string().min(1) }),
  z.object({ kind: z.literal("native"), search: exploreSearchSchema }),
]);

export type Stamp = z.infer<typeof stampSchema>;

/** Every portfolio shares one origin, so one localStorage: the key names the owner, or two
 *  portfolios in one browser would overwrite each other's stamp. The privacy page names it so. */
export function stampKey(handle: string): string {
  return `portfolio.stamp.v1.${handle.toLowerCase()}`;
}

/** Storage is injectable so tests never stub globals. Corrupt JSON, foreign shapes,
 *  and storage exceptions (Safari private mode) all read as "no stamp". */
export function readStamp(
  handle: string,
  storage: Pick<Storage, "getItem"> = localStorage,
): Stamp | null {
  try {
    const raw = storage.getItem(stampKey(handle));
    if (!raw) return null;
    const parsed = stampSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

export function writeStamp(
  handle: string,
  stamp: Stamp,
  storage: Pick<Storage, "setItem"> = localStorage,
): void {
  try {
    storage.setItem(stampKey(handle), JSON.stringify(stamp));
  } catch {
    /* storage unavailable — personalisation just won't persist */
  }
}

export function clearStamp(
  handle: string,
  storage: Pick<Storage, "removeItem"> = localStorage,
): void {
  try {
    storage.removeItem(stampKey(handle));
  } catch {
    /* ditto */
  }
}

export function nativeStamp(search: ExploreSearch): Stamp {
  return {
    kind: "native",
    search: {
      d: search.d,
      lucky: search.lucky,
      focus: search.focus,
      tone: search.tone,
    },
  };
}
