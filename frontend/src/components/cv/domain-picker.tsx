import { useState } from "react";
import { Check, Plus, X } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useList, type DomainRow } from "@/lib/queries/jac";
import { Badge } from "@/components/ui/badge";
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

export function DomainPicker({
  value,
  onChange,
  allowCreate = true,
}: {
  value: number[];
  onChange: (next: number[]) => void;
  allowCreate?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const qc = useQueryClient();
  const list = useList<DomainRow>("domains", { search });
  const rows = list.data?.results ?? [];

  const create = useMutation({
    mutationFn: (name: string) =>
      api<DomainRow>("/api/jac/domains/", {
        method: "POST",
        body: JSON.stringify({ name, description: "" }),
      }),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ["jac", "domains"] });
      onChange([...value, created.id]);
      setSearch("");
    },
  });

  const selected = rows.filter((r) => value.includes(r.id));
  const exactMatch = rows.some(
    (r) => r.name.toLowerCase() === search.trim().toLowerCase(),
  );

  function toggle(id: number) {
    onChange(
      value.includes(id) ? value.filter((v) => v !== id) : [...value, id],
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1">
        {selected.map((d) => (
          <Badge key={d.id} variant="secondary" className="gap-1">
            {d.name}
            <button
              type="button"
              onClick={() => toggle(d.id)}
              className="hover:text-destructive"
            >
              <X className="size-3" />
            </button>
          </Badge>
        ))}
      </div>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button type="button" variant="outline" size="sm">
            <Plus className="size-4" /> Add domain
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-72 p-0">
          <Command shouldFilter={false}>
            <CommandInput
              placeholder="Search domains…"
              value={search}
              onValueChange={setSearch}
            />
            <CommandList>
              <CommandEmpty>No matches.</CommandEmpty>
              <CommandGroup>
                {rows.map((d) => (
                  <CommandItem key={d.id} onSelect={() => toggle(d.id)}>
                    <Check
                      className={
                        "size-4 mr-2 " +
                        (value.includes(d.id) ? "opacity-100" : "opacity-0")
                      }
                    />
                    {d.name}
                  </CommandItem>
                ))}
                {allowCreate && search.trim() && !exactMatch && (
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
    </div>
  );
}
