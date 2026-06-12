import { useMemo, useState } from "react";
import { Check, Plus, X } from "lucide-react";
import { toast } from "sonner";
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
 *
 * `autoAddPrerequisites` pulls in a skill's transitive `builds_on` chain when you
 * add it (tag Django → Python comes along; DRF → Django + Python). Add-only: it
 * never removes prerequisites, since once they're in we can't tell auto-added
 * from deliberate. Enable it where you *tag* skills onto an entry (jobs/projects/
 * education/certs); leave it off where you author the graph itself (`builds_on`,
 * `related_skills`).
 */
export function SkillPicker({
  value,
  onChange,
  excludeId,
  autoAddPrerequisites = false,
}: {
  value: number[];
  onChange: (next: number[]) => void;
  excludeId?: number;
  autoAddPrerequisites?: boolean;
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

  // id → SkillRow over every row we've seen (base list + current search), so the
  // prerequisite walk and the toast can resolve builds_on / names without a fetch.
  const skillById = useMemo(() => {
    const m = new Map<number, SkillRow>();
    for (const r of selectedList.data?.results ?? []) m.set(r.id, r);
    for (const r of options.data?.results ?? []) m.set(r.id, r);
    return m;
  }, [selectedList.data, options.data]);

  // Transitive builds_on closure of `startId`, cycle-guarded (the backend doesn't
  // forbid builds_on cycles). Limited to skills present in `skillById` — a
  // prerequisite off page 1 of an unsearched 50-row list won't resolve.
  function prerequisitesOf(startId: number): number[] {
    const out = new Set<number>();
    const stack = [startId];
    while (stack.length) {
      const cur = stack.pop()!;
      for (const pre of skillById.get(cur)?.builds_on ?? []) {
        if (pre !== excludeId && !out.has(pre)) {
          out.add(pre);
          stack.push(pre);
        }
      }
    }
    return [...out];
  }

  function toggle(id: number) {
    if (value.includes(id)) {
      onChange(value.filter((v) => v !== id)); // remove: never cascades
      return;
    }
    if (!autoAddPrerequisites) {
      onChange([...value, id]);
      return;
    }
    const prereqs = prerequisitesOf(id);
    const next = [...new Set([...value, id, ...prereqs])];
    onChange(next);
    const added = prereqs.filter((p) => !value.includes(p));
    if (added.length) {
      const names = added
        .map((p) => skillById.get(p)?.name)
        .filter(Boolean)
        .join(", ");
      toast(`Also added prerequisite${added.length > 1 ? "s" : ""}: ${names}`);
    }
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
