import type { ReactNode } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";

/**
 * A date field that can be intentionally empty. Native `<input type="date">`
 * makes clearing a value awkward (no obvious affordance once a date is set), so
 * we pair it with a checkbox: checking it blanks the value, unchecking seeds
 * today's date so there's something valid to edit. Empty string == "no date".
 */
export function OptionalDateField({
  id,
  label,
  noneLabel,
  value,
  onChange,
  error,
}: {
  id: string;
  label: string;
  /** Checkbox label for the "no date" state, e.g. "Current" or "Never expires". */
  noneLabel: string;
  value: string;
  onChange: (value: string) => void;
  error?: ReactNode;
}) {
  const none = value === "";
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="date"
        value={value}
        disabled={none}
        onChange={(e) => onChange(e.target.value)}
      />
      <label className="flex items-center gap-2 text-sm text-muted-foreground">
        <Checkbox
          checked={none}
          onCheckedChange={(checked) =>
            onChange(checked ? "" : new Date().toISOString().slice(0, 10))
          }
        />
        {noneLabel}
      </label>
      {error}
    </div>
  );
}
