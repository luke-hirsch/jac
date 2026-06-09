import { useState } from "react";
import { Check, Plus } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useList, type LocationRow } from "@/lib/queries/jac";
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

export function LocationPicker({
  value,
  onChange,
}: {
  value: number | null;
  onChange: (next: number | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const qc = useQueryClient();
  const list = useList<LocationRow>("locations", { search });
  const rows = list.data?.results ?? [];
  const current = rows.find((r) => r.id === value);
  const exactMatch = rows.some(
    (r) => r.city.toLowerCase() === search.trim().toLowerCase(),
  );

  const create = useMutation({
    mutationFn: (city: string) =>
      api<LocationRow>("/api/jac/locations/", {
        method: "POST",
        body: JSON.stringify({ city }),
      }),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ["jac", "locations"] });
      onChange(created.id);
      setSearch("");
      setOpen(false);
    },
  });

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          className="justify-start w-full"
        >
          {current
            ? `${current.city}${current.country ? ", " + current.country : ""}`
            : "Pick location…"}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 p-0">
        <Command shouldFilter={false}>
          <CommandInput
            placeholder="Search cities…"
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
                  {r.city}
                  {r.country ? `, ${r.country}` : ""}
                </CommandItem>
              ))}
              {search.trim() && !exactMatch && (
                <CommandItem
                  onSelect={() => create.mutate(search.trim())}
                  disabled={create.isPending}
                >
                  <Plus className="size-4 mr-2" />
                  Create "{search.trim()}"
                </CommandItem>
              )}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
