import { useState } from "react";
import { Check } from "lucide-react";
import { useList, type DomainRow } from "@/lib/queries/jac";
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
 * Single-select domain filter for the section list toolbars. Returns the chosen
 * domain id, or "" for "no filter". A searchable combobox (not a plain Select)
 * because the domain taxonomy — system defaults + the user's own — is long. The
 * current selection resolves from an unsearched base list so its name shows even
 * while you type a non-matching search.
 */
export function DomainFilter({
  value,
  onChange,
}: {
  value: number | "";
  onChange: (next: number | "") => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const options = useList<DomainRow>("domains", { search });
  const base = useList<DomainRow>("domains", {});
  const rows = options.data?.results ?? [];
  const current = (base.data?.results ?? []).find((r) => r.id === value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          className="w-44 justify-start font-normal"
        >
          {current ? current.name : "All domains"}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-64 p-0">
        <Command shouldFilter={false}>
          <CommandInput
            placeholder="Filter by domain…"
            value={search}
            onValueChange={setSearch}
          />
          <CommandList>
            <CommandEmpty>No matches.</CommandEmpty>
            <CommandGroup>
              <CommandItem
                onSelect={() => {
                  onChange("");
                  setOpen(false);
                }}
              >
                <Check
                  className={
                    "size-4 mr-2 " + (value === "" ? "opacity-100" : "opacity-0")
                  }
                />
                All domains
              </CommandItem>
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
                  {r.name}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
