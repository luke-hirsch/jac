import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { useCvEntries, useFullList, type DomainRow } from "@/lib/queries/jac";
import { usePortfolioBlocks, useSaveBlock } from "@/lib/queries/portfolio";
import {
  candidates,
  filterCandidates,
  moveFeatured,
  resolveFeatured,
  toggleFeatured,
  type FeaturedCandidate,
} from "@/lib/portfolio/link-form";

/** Canonical facet order + labels (candidate.type is the career *singular* or "block"). */
const TYPE_FACETS: { type: string; label: string }[] = [
  { type: "job", label: "Jobs" },
  { type: "project", label: "Projects" },
  { type: "skill", label: "Skills" },
  { type: "education", label: "Education" },
  { type: "certification", label: "Certifications" },
  { type: "language", label: "Languages" },
  { type: "block", label: "Blocks" },
];

/** A dismissable filter chip that shows a count and toggles active styling. */
function FacetChip({
  active,
  count,
  onClick,
  children,
}: {
  active: boolean;
  count: number;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={count === 0 && !active}
      className={
        "rounded-full border px-2.5 py-0.5 text-xs transition disabled:opacity-40 " +
        (active
          ? "border-primary bg-primary text-primary-foreground"
          : "hover:bg-muted")
      }
    >
      {children}
      <span className="ml-1 opacity-70">{count}</span>
    </button>
  );
}

/** Two panes: the ordered chosen list (reorder / remove) on the left; a searchable,
 *  facet-filtered pool (toggle in/out) on the right. Pure ops from link-form.ts. Reused
 *  by the manual-link editor (featured content) and — guide 2 — the block editor (links). */
export function ContentPicker({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const db = useCvEntries();
  const blocks = usePortfolioBlocks();
  const domains = useFullList<DomainRow>("domains");
  const saveBlock = useSaveBlock();

  const pool = useMemo(
    () => candidates(db.data, blocks.data ?? []),
    [db.data, blocks.data],
  );
  const chosen = resolveFeatured(selected, pool);
  const chosenIds = new Set(selected);

  const [search, setSearch] = useState("");
  const [types, setTypes] = useState<string[]>([]);
  const [domainIds, setDomainIds] = useState<number[]>([]);

  // Facet counts reflect *the other* active filters (shop convention), so toggling one
  // facet never zeroes the siblings you might switch to next.
  const typeCounts = useMemo(() => {
    const base = filterCandidates(pool, { search, domainIds });
    const m: Record<string, number> = {};
    for (const c of base) m[c.type] = (m[c.type] ?? 0) + 1;
    return m;
  }, [pool, search, domainIds]);

  const domainCounts = useMemo(() => {
    const base = filterCandidates(pool, { search, types });
    const m: Record<number, number> = {};
    for (const c of base) for (const d of c.domainIds) m[d] = (m[d] ?? 0) + 1;
    return m;
  }, [pool, search, types]);

  // Only offer domain facets that actually tag something in the pool.
  const domainFacets = useMemo(() => {
    const used = new Set(pool.flatMap((c) => c.domainIds));
    return (domains.data ?? []).filter((d) => used.has(d.id));
  }, [pool, domains.data]);

  const visible = useMemo(
    () => filterCandidates(pool, { search, types, domainIds }),
    [pool, search, types, domainIds],
  );

  function toggleType(t: string) {
    setTypes((cur) =>
      cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t],
    );
  }
  function toggleDomain(id: number) {
    setDomainIds((cur) =>
      cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id],
    );
  }

  function addPlaceholder() {
    // A hidden draft block: valid payload (clean() needs a non-empty body), is_active
    // false so the public renderer skips it until it's finished in the Blocks tab.
    saveBlock.mutate(
      {
        input: {
          kind: "text",
          title: "Untitled block",
          body: "(placeholder — edit later)",
          alt_text: "",
          domains: [],
          links: [],
          favourite: false,
          order: 0,
          is_active: false,
        },
      },
      {
        onSuccess: (row) => {
          onChange([...selected, `block:${row.id}`]);
          toast.success("Placeholder added — write it in the Blocks tab");
        },
        onError: () => toast.error("Couldn't add a placeholder block"),
      },
    );
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {/* chosen (ordered) */}
      <div className="space-y-2">
        <p className="text-sm font-medium">Selected ({chosen.length})</p>
        {chosen.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing selected yet — search and pick from the right.
          </p>
        ) : (
          <ul className="space-y-1">
            {chosen.map((c, i) => (
              <li
                key={c.id}
                className="flex items-center gap-2 rounded-md border px-2 py-1 text-sm"
              >
                <span className="flex-1 truncate">{c.label}</span>
                <Badge variant="secondary">{c.type}</Badge>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={i === 0}
                  onClick={() => onChange(moveFeatured(selected, i, -1))}
                >
                  ↑
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={i === chosen.length - 1}
                  onClick={() => onChange(moveFeatured(selected, i, 1))}
                >
                  ↓
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onChange(toggleFeatured(selected, c.id))}
                >
                  ✕
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* searchable + faceted pool */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <Input
            placeholder="Search content…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8"
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={saveBlock.isPending}
            onClick={addPlaceholder}
          >
            + Placeholder
          </Button>
        </div>

        <div className="space-y-1">
          <div className="flex flex-wrap gap-1">
            {TYPE_FACETS.map((f) => (
              <FacetChip
                key={f.type}
                active={types.includes(f.type)}
                count={typeCounts[f.type] ?? 0}
                onClick={() => toggleType(f.type)}
              >
                {f.label}
              </FacetChip>
            ))}
          </div>
          {domainFacets.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {domainFacets.map((d) => (
                <FacetChip
                  key={d.id}
                  active={domainIds.includes(d.id)}
                  count={domainCounts[d.id] ?? 0}
                  onClick={() => toggleDomain(d.id)}
                >
                  {d.name}
                </FacetChip>
              ))}
            </div>
          )}
        </div>

        <div className="max-h-72 space-y-1 overflow-y-auto pr-1">
          {visible.map((c: FeaturedCandidate) => (
            <label
              key={c.id}
              className="flex items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-muted"
            >
              <Checkbox
                checked={chosenIds.has(c.id)}
                onCheckedChange={() => onChange(toggleFeatured(selected, c.id))}
              />
              <span className="flex-1 truncate">{c.label}</span>
              <Badge variant="outline">{c.type}</Badge>
            </label>
          ))}
          {visible.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {pool.length === 0
                ? "No career entries or blocks yet."
                : "No content matches these filters."}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
