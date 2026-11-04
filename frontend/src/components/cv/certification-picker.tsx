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
 * Single-select FK picker over the user's certifications — replaces the raw
 * numeric id input on the skill editor. No inline create: a certification needs
 * name + issuer (more than a combobox should ask), so create them on
 * `/cv/certifications`.
 */
export function CertificationPicker({
  value,
  onChange,
}: {
  value: number | null;
  onChange: (next: number | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const options = useList<CertificationRow>("certifications", { search });
  const base = useList<CertificationRow>("certifications", {});
  const rows = options.data?.results ?? [];
  const current = (base.data?.results ?? []).find((r) => r.id === value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline" className="justify-start w-full">
          {current
            ? `${current.name} — ${current.issuer}`
            : "Pick certification…"}
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
              {value !== null && (
                <CommandItem
                  onSelect={() => {
                    onChange(null);
                    setOpen(false);
                  }}
                >
                  <span className="text-muted-foreground">Clear</span>
                </CommandItem>
              )}
              {rows.map((r) => (
                <CommandItem
                  key={r.id}
                  onSelect={() => {
                    onChange(r.id);
                    setOpen(false);
                  }}
                >
                  <Check
                    className={
                      "size-4 mr-2 " +
                      (r.id === value ? "opacity-100" : "opacity-0")
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
