import { z } from "zod";

/** Search params a native stamp restores (mirrors /explore's validateSearch). */
const nativeSearchSchema = z.object({
  d: z.array(z.string()).optional(),
  lucky: z.boolean().optional(),
});

const stampSchema = z.union([
  z.object({ kind: z.literal("link"), slug: z.string().min(1) }),
  z.object({ kind: z.literal("native"), search: nativeSearchSchema }),
]);

export type Stamp = z.infer<typeof stampSchema>;

export const STAMP_KEY = "portfolio.stamp.v1";

/** Storage is injectable so tests never stub globals. Corrupt JSON, foreign shapes,
 *  and storage exceptions (Safari private mode) all read as "no stamp". */
export function readStamp(
  storage: Pick<Storage, "getItem"> = localStorage,
): Stamp | null {
  try {
    const raw = storage.getItem(STAMP_KEY);
    if (!raw) return null;
    const parsed = stampSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

export function writeStamp(
  stamp: Stamp,
  storage: Pick<Storage, "setItem"> = localStorage,
): void {
  try {
    storage.setItem(STAMP_KEY, JSON.stringify(stamp));
  } catch {
    /* storage unavailable — personalisation just won't persist */
  }
}

export function clearStamp(
  storage: Pick<Storage, "removeItem"> = localStorage,
): void {
  try {
    storage.removeItem(STAMP_KEY);
  } catch {
    /* ditto */
  }
}
