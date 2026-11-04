import { useState } from "react";
import { Check } from "lucide-react";
import { useList, type JobRow } from "@/lib/queries/jac";
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
 * Single-select FK picker over the user's jobs — used to link a project (or a
 * resume snippet) to the job it was done at. Modelled on `CertificationPicker`.
 * No inline create: a job needs title + company + start date, so create them on
 * `/cv/jobs`. The current selection resolves from an unsearched base list so it
 * still shows while you type a non-matching search.
 */
export function JobPicker({
  value,
  onChange,
}: {
  value: number | null;
  onChange: (next: number | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const options = useList<JobRow>("jobs", { search });
  const base = useList<JobRow>("jobs", {});
  const rows = options.data?.results ?? [];
  const current = (base.data?.results ?? []).find((r) => r.id === value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline" className="justify-start w-full">
          {current ? `${current.title} — ${current.company}` : "Pick job…"}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 p-0">
        <Command shouldFilter={false}>
          <CommandInput
            placeholder="Search jobs…"
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
                  {r.title} — {r.company}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
