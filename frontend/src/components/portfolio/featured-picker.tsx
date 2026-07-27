import { useMemo } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { useCvEntries } from "@/lib/queries/jac";
import { usePortfolioBlocks } from "@/lib/queries/portfolio";
import {
  candidates,
  moveFeatured,
  resolveFeatured,
  toggleFeatured,
  type FeaturedCandidate,
} from "@/lib/portfolio/link-form";

/** Two panes: the ordered featured list (reorder / remove) on the left, the full pool
 *  of career entries + blocks (toggle in/out) on the right. Pure ops from link-form.ts;
 *  this component only maps them to clicks. */
export function FeaturedPicker({
  featured,
  onChange,
}: {
  featured: string[];
  onChange: (next: string[]) => void;
}) {
  const db = useCvEntries();
  const blocks = usePortfolioBlocks();
  const pool = useMemo(
    () => candidates(db.data, blocks.data ?? []),
    [db.data, blocks.data],
  );
  const chosen = resolveFeatured(featured, pool);
  const chosenIds = new Set(featured);

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="space-y-2">
        <p className="text-sm font-medium">Featured ({chosen.length})</p>
        {chosen.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing featured yet — pick from the right. Empty = the public page falls
            back to favourites + blocks.
          </p>
        ) : (
          <ul className="space-y-1">
            {chosen.map((c, i) => (
              <li
                key={c.id}
                className="flex items-center gap-2 rounded-md border px-2 py-1 text-sm"
              >
                <span className="flex-1 truncate">{c.label}</span>
                <Badge variant="secondary">{c.type}</Badge>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={i === 0}
                  onClick={() => onChange(moveFeatured(featured, i, -1))}
                >
                  ↑
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={i === chosen.length - 1}
                  onClick={() => onChange(moveFeatured(featured, i, 1))}
                >
                  ↓
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onChange(toggleFeatured(featured, c.id))}
                >
                  ✕
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-1 max-h-80 overflow-y-auto pr-1">
        <p className="text-sm font-medium">All content</p>
        {pool.map((c: FeaturedCandidate) => (
          <label
            key={c.id}
            className="flex items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-muted"
          >
            <Checkbox
              checked={chosenIds.has(c.id)}
              onCheckedChange={() => onChange(toggleFeatured(featured, c.id))}
            />
            <span className="flex-1 truncate">{c.label}</span>
            <Badge variant="outline">{c.type}</Badge>
          </label>
        ))}
        {pool.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No career entries or blocks yet.
          </p>
        ) : null}
      </div>
    </div>
  );
}
