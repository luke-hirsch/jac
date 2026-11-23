import { useState } from "react";
import { Check } from "lucide-react";
import { useList, type CertificationRow } from "@/lib/queries/jac";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

/**
 * Multi-select picker over the user's certifications, for the skill editor. It
 * writes the skill's `certifications`: the same link as a certification's
 * `skills`, edited from the skill's side. No inline create: a certification
 * needs name + issuer (more than a combobox should ask), so create them on
 * `/cv/certifications`.
 */
export function CertificationPicker({
  value,
  onChange,
}: {
  value: number[];
  onChange: (next: number[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const options = useList<CertificationRow>("certifications", { search });
  const base = useList<CertificationRow>("certifications", {});
  const rows = options.data?.results ?? [];
  const chosen = (base.data?.results ?? []).filter((r) => value.includes(r.id));

  const toggle = (id: number) =>
    onChange(
      value.includes(id) ? value.filter((v) => v !== id) : [...value, id],
    );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          className="h-auto min-h-9 w-full justify-start whitespace-normal text-left"
        >
          {chosen.length
            ? chosen.map((r) => r.name).join(", ")
            : "Pick certifications…"}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 p-0">
        <Command shouldFilter={false}>
          <CommandInput
            placeholder="Search certifications…"
            value={search}
            onValueChange={setSearch}
          />
          <CommandList>
            <CommandEmpty>No matches.</CommandEmpty>
            <CommandGroup>
              {rows.map((r) => (
                <CommandItem key={r.id} onSelect={() => toggle(r.id)}>
                  <Check
                    className={
                      "size-4 mr-2 " +
                      (value.includes(r.id) ? "opacity-100" : "opacity-0")
                    }
                  />
                  {r.name} — {r.issuer}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
