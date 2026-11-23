import { useState } from "react";
import { Check, Settings2 } from "lucide-react";
import { useList, type DomainRow } from "@/lib/queries/jac";
import { filterAfterRemoval } from "@/lib/domains";
import { DomainManager } from "@/components/cv/domain-manager";
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
 *
 * Its footer opens the domain manager: every CV page with domains has this filter, and the
 * list it shows is where a typo or a duplicate gets noticed. Not in the editor's picker, whose
 * form would still hold the id of a domain merged away.
 */
export function DomainFilter({
  value,
  onChange,
}: {
  value: number | "";
  onChange: (next: number | "") => void;
}) {
  const [open, setOpen] = useState(false);
  const [managing, setManaging] = useState(false);
  const [search, setSearch] = useState("");
  const options = useList<DomainRow>("domains", { search });
  const base = useList<DomainRow>("domains", {});
  const rows = options.data?.results ?? [];
  const current = (base.data?.results ?? []).find((r) => r.id === value);

  return (
    <>
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
                      "size-4 mr-2 " +
                      (value === "" ? "opacity-100" : "opacity-0")
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
          <div className="border-t p-1">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="w-full justify-start"
              onClick={() => {
                setOpen(false);
                setManaging(true);
              }}
            >
              <Settings2 className="size-4" /> Manage domains
            </Button>
          </div>
        </PopoverContent>
      </Popover>
      <DomainManager
        open={managing}
        onOpenChange={setManaging}
        onRemoved={(id, replacement) => {
          const next = filterAfterRemoval(value, id, replacement);
          if (next !== value) onChange(next);
        }}
      />
    </>
  );
}
