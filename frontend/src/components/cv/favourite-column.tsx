import { Star } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";
import { cn } from "@/lib/utils";

/**
 * A sortable "favourite" star column for the CV entry tables. The header toggles
 * server-side `-favourite` ordering (favourites first); the cell shows a filled star
 * for flagged rows. `active` reflects whether the favourites-first sort is currently on.
 */
export function favouriteColumn<T extends { favourite: boolean }>(opts: {
  active: boolean;
  onToggle: () => void;
}): ColumnDef<T, unknown> {
  return {
    id: "favourite",
    size: 40,
    header: () => (
      <button
        type="button"
        onClick={opts.onToggle}
        title="Sort favourites first"
        className="flex items-center hover:text-foreground"
      >
        <Star
          className={cn(
            "size-4",
            opts.active ? "fill-yellow-400 text-yellow-400" : "opacity-50",
          )}
        />
      </button>
    ),
    cell: ({ row }) => (
      <Star
        className={cn(
          "size-4",
          row.original.favourite
            ? "fill-yellow-400 text-yellow-400"
            : "opacity-20",
        )}
      />
    ),
  };
}
