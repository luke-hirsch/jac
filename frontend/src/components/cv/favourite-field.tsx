import { Star } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";

/**
 * Favourite toggle for a CV entry editor. Favourites get a small ranking nudge in the
 * CV pipeline; the backend caps how many entries per type may be flagged (see
 * `CvEntry.FAVOURITE_LIMIT`), so `hint` is a good place to surface that cap.
 */
export function FavouriteField({
  checked,
  onChange,
  hint,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  hint?: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <Checkbox
        id="favourite"
        checked={checked}
        onCheckedChange={(v) => onChange(!!v)}
      />
      <Label htmlFor="favourite" className="flex items-center gap-1">
        <Star className="size-4" />
        Favourite
      </Label>
      {hint ? (
        <span className="text-xs text-muted-foreground">{hint}</span>
      ) : null}
    </div>
  );
}
