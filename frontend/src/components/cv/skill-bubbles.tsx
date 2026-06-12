import { useEffect, useMemo, useRef, useState } from "react";
import {
  hierarchy,
  pack,
  type HierarchyCircularNode,
} from "d3-hierarchy";
import type { SkillRow } from "@/lib/queries/jac";

/**
 * Packed-circle "bubble cloud" of the user's skills, clustered into one circle
 * per category. Bubble area encodes proficiency (always populated, unlike
 * years_of_experience); color encodes category. Clicking a bubble opens the
 * same editor sheet the table uses.
 *
 * Layout is d3-hierarchy's `pack` over a two-level tree (root → category →
 * skill leaf); we render the result as plain SVG so clicks/labels stay in React.
 */

const PROF_RANK: Record<SkillRow["proficiency"], number> = {
  beginner: 1,
  intermediate: 2,
  advanced: 3,
  expert: 4,
};

const CATEGORY_META: Record<
  SkillRow["category"],
  { label: string; fill: string; stroke: string }
> = {
  technical: { label: "Technical", fill: "#3b82f6", stroke: "#1d4ed8" },
  soft: { label: "Soft", fill: "#10b981", stroke: "#047857" },
  domain: { label: "Domain", fill: "#8b5cf6", stroke: "#6d28d9" },
  other: { label: "Other", fill: "#64748b", stroke: "#475569" },
};

const CATEGORY_ORDER: SkillRow["category"][] = [
  "technical",
  "soft",
  "domain",
  "other",
];

type Datum = {
  name: string;
  value?: number;
  skill?: SkillRow;
  category?: SkillRow["category"];
  children?: Datum[];
};

const HEIGHT = 560;

export function SkillBubbles({
  skills,
  loading,
  onSelect,
}: {
  skills: SkillRow[];
  loading: boolean;
  onSelect: (skill: SkillRow) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);

  // d3-pack needs a concrete pixel width; track the container's.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) =>
      setWidth(entry.contentRect.width),
    );
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const root = useMemo(() => {
    if (!width || skills.length === 0) return null;
    const groups: Datum[] = CATEGORY_ORDER.map((cat) => ({
      name: cat,
      category: cat,
      children: skills
        .filter((s) => s.category === cat)
        .map((s) => ({
          name: s.name,
          value: PROF_RANK[s.proficiency],
          skill: s,
          category: cat,
        })),
    })).filter((g) => (g.children?.length ?? 0) > 0);

    const h = hierarchy<Datum>({ name: "root", children: groups }).sum(
      (d) => d.value ?? 0,
    );
    return pack<Datum>()
      .size([width, HEIGHT])
      .padding((node) => (node.depth === 1 ? 16 : 3))(h);
  }, [width, skills]);

  return (
    <div ref={ref} className="rounded-md border bg-muted/10">
      <div className="flex flex-wrap gap-3 border-b px-3 py-2 text-xs text-muted-foreground">
        {CATEGORY_ORDER.map((cat) => (
          <span key={cat} className="flex items-center gap-1.5">
            <span
              className="inline-block size-2.5 rounded-full"
              style={{ background: CATEGORY_META[cat].fill }}
            />
            {CATEGORY_META[cat].label}
          </span>
        ))}
        <span className="ml-auto">bubble size = proficiency</span>
      </div>

      {loading && skills.length === 0 ? (
        <div className="flex h-140 items-center justify-center text-sm text-muted-foreground">
          Loading…
        </div>
      ) : skills.length === 0 ? (
        <div className="flex h-140 items-center justify-center text-sm text-muted-foreground">
          No skills to chart — adjust filters or add some.
        </div>
      ) : (
        <svg
          width="100%"
          height={HEIGHT}
          viewBox={`0 0 ${width} ${HEIGHT}`}
          className="block"
        >
          {root?.descendants().map((node) => {
            if (node.depth === 1) return <CategoryCircle key={categoryKey(node)} node={node} />;
            if (node.depth === 2) {
              const skill = node.data.skill!;
              return (
                <SkillCircle
                  key={skill.id}
                  node={node}
                  skill={skill}
                  onSelect={onSelect}
                />
              );
            }
            return null;
          })}
        </svg>
      )}
    </div>
  );
}

function categoryKey(node: HierarchyCircularNode<Datum>) {
  return `cat-${node.data.category}`;
}

function CategoryCircle({ node }: { node: HierarchyCircularNode<Datum> }) {
  const meta = CATEGORY_META[node.data.category ?? "other"];
  return (
    <g pointerEvents="none">
      <circle
        cx={node.x}
        cy={node.y}
        r={node.r}
        fill="none"
        stroke={meta.stroke}
        strokeOpacity={0.25}
        strokeDasharray="4 4"
      />
      <text
        x={node.x}
        y={node.y - node.r + 14}
        textAnchor="middle"
        className="fill-muted-foreground text-[11px] font-medium uppercase tracking-wide"
      >
        {meta.label}
      </text>
    </g>
  );
}

function SkillCircle({
  node,
  skill,
  onSelect,
}: {
  node: HierarchyCircularNode<Datum>;
  skill: SkillRow;
  onSelect: (skill: SkillRow) => void;
}) {
  const meta = CATEGORY_META[skill.category];
  const showLabel = node.r >= 18;
  const fontSize = Math.min(node.r / 2.6, 14);
  const exp = skill.years_of_experience;
  return (
    <g
      className="cursor-pointer"
      onClick={() => onSelect(skill)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(skill);
        }
      }}
    >
      <title>
        {skill.name} · {skill.proficiency}
        {exp != null ? ` · ${exp}y` : ""}
      </title>
      <circle
        cx={node.x}
        cy={node.y}
        r={node.r}
        fill={meta.fill}
        stroke={meta.stroke}
        className="[fill-opacity:0.82] transition-[fill-opacity] hover:[fill-opacity:1]"
      />
      {showLabel && (
        <text
          x={node.x}
          y={node.y}
          textAnchor="middle"
          dominantBaseline="central"
          fill="white"
          fontSize={fontSize}
          pointerEvents="none"
        >
          {truncate(skill.name, node.r, fontSize)}
        </text>
      )}
    </g>
  );
}

// Rough char budget for the bubble's diameter so labels don't overflow.
function truncate(name: string, r: number, fontSize: number) {
  const max = Math.max(2, Math.floor((r * 2) / (fontSize * 0.6)));
  return name.length > max ? `${name.slice(0, max - 1)}…` : name;
}
