import { useState } from "react";
import { Check, Plus, X } from "lucide-react";
import { useList, type SkillRow } from "@/lib/queries/jac";
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

/**
 * Multi-select M2M picker over the user's skills — used for the `skills`
 * relation on jobs/projects and for `related_skills` on the skill editor.
 *
 * `excludeId` hides a single skill (the row being edited) so a skill can't be
 * related to itself — the backend rejects self-reference anyway, this just keeps
 * it out of the options. Inline "create new skill" is intentionally absent: a
 * skill needs name + proficiency + category, which is more than a combobox
 * should ask for — create skills on `/cv/skills`.
 */
export function SkillPicker({
  value,
  onChange,
  excludeId,
}: {
  value: number[];
  onChange: (next: number[]) => void;
  excludeId?: number;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  // The searched page drives the dropdown; an unsearched page resolves the
  // selected badges' names (which may not be in the current search results).
  const options = useList<SkillRow>("skills", { search });
  const selectedList = useList<SkillRow>("skills", {});
  const rows = (options.data?.results ?? []).filter((r) => r.id !== excludeId);
  const selected = (selectedList.data?.results ?? []).filter((r) =>
    value.includes(r.id),
  );

  function toggle(id: number) {
    onChange(
      value.includes(id) ? value.filter((v) => v !== id) : [...value, id],
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1">
        {selected.map((s) => (
          <Badge key={s.id} variant="secondary" className="gap-1">
            {s.name}
            <button
              type="button"
              onClick={() => toggle(s.id)}
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
            <Plus className="size-4" /> Add skill
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-72 p-0">
          <Command shouldFilter={false}>
            <CommandInput
              placeholder="Search skills…"
              value={search}
              onValueChange={setSearch}
            />
            <CommandList>
              <CommandEmpty>No matches.</CommandEmpty>
              <CommandGroup>
                {rows.map((s) => (
                  <CommandItem key={s.id} onSelect={() => toggle(s.id)}>
                    <Check
                      className={
                        "size-4 mr-2 " +
                        (value.includes(s.id) ? "opacity-100" : "opacity-0")
                      }
                    />
                    {s.name}
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
    </div>
  );
}
