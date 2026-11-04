import type { FieldSaveState } from "@/lib/field-save";

/** Tiny per-field feedback line for line-by-line saving: saving… / saved ✓ /
 *  the server's rejection for exactly this field. Nothing while untouched. */
export function LineSaveHint({ s }: { s?: FieldSaveState }) {
  if (!s) return null;
  if (s.state === "saving")
    return <p className="text-xs text-muted-foreground">saving…</p>;
  if (s.state === "saved")
    return <p className="text-xs text-emerald-600">saved ✓</p>;
  return <p className="text-xs text-destructive">{s.message}</p>;
}
