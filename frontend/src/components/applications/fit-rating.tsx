import { StarIcon } from "lucide-react";
import { cn } from "@/lib/utils";

const SCALE = [1, 2, 3, 4, 5] as const;

/** How well the user fits the posting, as five stars. Clicking the current rating clears it. */
export function FitRating({
  value,
  onChange,
  disabled,
}: {
  value: number | null;
  onChange: (fit: number | null) => void;
  disabled?: boolean;
}) {
  return (
    <div role="group" aria-label="Fit" className="flex items-center">
      {SCALE.map((n) => (
        <button
          key={n}
          type="button"
          disabled={disabled}
          aria-label={`Fit ${n} of 5`}
          aria-pressed={value === n}
          title={value === n ? "Clear the rating" : `Fit ${n} of 5`}
          onClick={() => onChange(value === n ? null : n)}
          className="p-1 text-muted-foreground transition-colors hover:text-primary disabled:opacity-50"
        >
          <StarIcon
            className={cn(
              "size-5",
              value !== null && n <= value && "fill-primary text-primary",
            )}
          />
        </button>
      ))}
    </div>
  );
}

/** The rating as a compact read-only row of stars, for the applications list. */
export function FitStars({ value }: { value: number }) {
  return (
    <span
      className="inline-flex items-center"
      role="img"
      aria-label={`Fit ${value} of 5`}
      title={`Fit ${value} of 5`}
    >
      {SCALE.map((n) => (
        <StarIcon
          key={n}
          className={cn(
            "size-3",
            n <= value
              ? "fill-primary text-primary"
              : "text-muted-foreground/50",
          )}
        />
      ))}
    </span>
  );
}
