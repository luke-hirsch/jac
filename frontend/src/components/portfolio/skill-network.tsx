import { useEffect, useMemo, useRef, useState } from "react";
import { SectionHeading } from "@/components/portfolio/section-heading";
import { Button } from "@/components/ui/button";
import { anchorId } from "@/lib/portfolio/content";
import {
  buildNetwork,
  fontSize,
  layoutNetwork,
  litIds,
  PILL_HEIGHT,
  PILL_PAD,
  skillRadius,
  type Network,
  type NetNode,
  type Placed,
} from "@/lib/portfolio/network";
import { scrollMotion } from "@/lib/portfolio/studio";
import type { PortfolioItem } from "@/lib/queries/portfolio";
import { useDebounced } from "@/lib/use-debounced";

/** The portfolio's skills as a network around the jobs and projects that used them; the logic is
 *  lib/portfolio/network.ts. Hover or tap lights a node and its neighbours, a domain chip lights
 *  its part; a list underneath says the same for screen readers and small screens. */

const MAX_CHIPS = 8;

/** The gradient's colour at `recency` (0 = the oldest anchor, 1 = the most recent). */
function tint(recency: number | null): string {
  if (recency === null) return "var(--muted-foreground)";
  const pct = Math.round(recency * 100);
  return `color-mix(in oklch, var(--net-new) ${pct}%, var(--net-old))`;
}

function span(item: PortfolioItem): string {
  const from = item.started?.slice(0, 4);
  if (!from) return "";
  return `${from} – ${item.ended ? item.ended.slice(0, 4) : "today"}`;
}

function neighbours(net: Network, id: string): NetNode[] {
  const ids = new Set(
    net.edges.flatMap((e) =>
      e.anchor === id ? [e.skill] : e.skill === id ? [e.anchor] : [],
    ),
  );
  return net.nodes.filter((n) => ids.has(n.id));
}

/** The container's width, measured, so the layout is made for it: text stays at its size and a
 *  narrow screen gets a taller drawing instead of a smaller one. */
function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.floor(entry.contentRect.width)),
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, useDebounced(width, 150)] as const;
}

function SkillDot({ n }: { n: Placed }) {
  const r = skillRadius(n);
  return (
    <>
      <circle
        r={r}
        fill={tint(n.recency)}
        fillOpacity={n.recency === null ? 0.55 : 1}
      />
      <text
        x={r + 4}
        dominantBaseline="central"
        fontSize={fontSize(n)}
        fontWeight={n.featured ? 600 : 400}
        fill="currentColor"
      >
        {n.label}
      </text>
    </>
  );
}

function AnchorPill({ n }: { n: Placed }) {
  const w = n.box.right - n.box.left;
  return (
    <>
      <rect
        x={n.box.left}
        y={-PILL_HEIGHT / 2}
        width={w}
        height={PILL_HEIGHT}
        rx={n.kind === "job" ? 6 : PILL_HEIGHT / 2}
        fill="var(--card)"
        stroke={tint(n.recency)}
        strokeWidth={n.featured ? 3 : 2}
      />
      <text
        x={n.box.left + PILL_PAD}
        dominantBaseline="central"
        fontSize={fontSize(n)}
        fontWeight={600}
        fill="currentColor"
      >
        {n.label}
      </text>
    </>
  );
}

function Detail({
  net,
  node,
  cards,
}: {
  net: Network;
  node: NetNode | null;
  cards: Set<string>;
}) {
  if (!node) {
    return (
      <span className="text-muted-foreground">
        Hover or tap a skill, a job or a project to see what it connects to.
      </span>
    );
  }
  const linked = neighbours(net, node.id).map((n) => n.label);
  if (node.kind === "skill") {
    const head = [node.item.title, node.item.subtitle]
      .filter(Boolean)
      .join(" · ");
    return (
      <span>
        <strong>{head}</strong>
        <span className="text-muted-foreground">
          {linked.length
            ? `: used in ${linked.join(", ")}`
            : ": not tied to a job or project on this page"}
        </span>
      </span>
    );
  }
  const head = [
    node.item.title,
    node.kind === "job" ? node.item.subtitle : "",
    span(node.item),
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-2">
      <strong>{head}</strong>
      <span className="text-muted-foreground">
        {linked.length} {linked.length === 1 ? "skill" : "skills"} on this page
      </span>
      {cards.has(node.id) ? (
        <a
          href={`#${anchorId(node.id)}`}
          className="underline underline-offset-2"
          onClick={(e) => {
            const el = document.getElementById(anchorId(node.id));
            if (!el) return;
            e.preventDefault();
            el.scrollIntoView({ behavior: scrollMotion(), block: "start" });
          }}
        >
          Jump to it ↓
        </a>
      ) : null}
    </span>
  );
}

export function SkillNetwork({
  featured,
  more,
  cards,
}: {
  featured: PortfolioItem[];
  more: PortfolioItem[];
  /** Ids that have a card on the page: an anchor's detail offers the jump. */
  cards: Set<string>;
}) {
  const net = useMemo(() => buildNetwork(featured, more), [featured, more]);
  const [ref, width] = useWidth();
  const layout = useMemo(
    () => (net && width > 0 ? layoutNetwork(net, width) : null),
    [net, width],
  );
  const [hover, setHover] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [domain, setDomain] = useState<string | null>(null);

  if (!net) return null;
  const focus = hover ?? picked;
  const lit = litIds(net, focus, domain);
  const focused = net.nodes.find((n) => n.id === focus) ?? null;
  const byId = new Map(layout?.nodes.map((n) => [n.id, n]));
  const chips = net.domains.slice(0, MAX_CHIPS);
  const anchors = net.nodes.filter((n) => n.kind !== "skill").length;
  const skills = net.nodes.length - anchors;

  return (
    <section className="space-y-5">
      <div className="space-y-2">
        <SectionHeading>Skills</SectionHeading>
        <p className="text-sm text-muted-foreground">
          How they connect to the jobs and projects on this page.
        </p>
      </div>

      {chips.length > 1 ? (
        <div className="flex flex-wrap gap-1.5" aria-label="Light up a domain">
          {[null, ...chips].map((d) => (
            <Button
              key={d ?? "all"}
              size="xs"
              variant={domain === d ? "default" : "outline"}
              aria-pressed={domain === d}
              onClick={() => {
                setDomain(d);
                setPicked(null);
              }}
            >
              {d ?? "All"}
            </Button>
          ))}
        </div>
      ) : null}

      {/* The inset is padding on the measured element: ResizeObserver reports the width inside
          it, so the layout is made for that. The detail line reaches the edges again. */}
      <div
        ref={ref}
        className="rounded-3xl border bg-muted px-2 pt-2 sm:px-4 sm:pt-4 lg:px-6 lg:pt-6"
      >
        {layout ? (
          <svg
            width={layout.width}
            height={layout.height}
            viewBox={`0 0 ${layout.width} ${layout.height}`}
            className="block max-w-full select-none text-foreground"
            role="img"
            aria-label={`Network of ${skills} skills and the ${anchors} jobs and projects that used them. The list below has the same.`}
            onClick={(e) => {
              if (e.target === e.currentTarget) setPicked(null);
            }}
          >
            <g>
              {net.edges.map((e) => {
                const a = byId.get(e.anchor);
                const s = byId.get(e.skill);
                if (!a || !s) return null;
                const on =
                  focus !== null && (e.anchor === focus || e.skill === focus);
                const dim =
                  lit !== null && !(lit.has(e.anchor) && lit.has(e.skill));
                return (
                  <line
                    key={`${e.anchor}-${e.skill}`}
                    x1={a.x}
                    y1={a.y}
                    x2={s.x}
                    y2={s.y}
                    stroke={tint(a.recency)}
                    strokeWidth={on ? 2 : 1.25}
                    strokeOpacity={dim ? 0.07 : on ? 0.95 : 0.45}
                    className="transition-[stroke-opacity] duration-150"
                  />
                );
              })}
            </g>
            <g>
              {layout.nodes.map((n) => (
                <g
                  key={n.id}
                  transform={`translate(${n.x},${n.y})`}
                  opacity={lit !== null && !lit.has(n.id) ? 0.18 : 1}
                  className="cursor-pointer transition-opacity duration-150"
                  onPointerEnter={(e) => {
                    if (e.pointerType === "mouse") setHover(n.id);
                  }}
                  onPointerLeave={() => setHover(null)}
                  onClick={() => setPicked((p) => (p === n.id ? null : n.id))}
                >
                  {n.kind === "skill" ? (
                    <SkillDot n={n} />
                  ) : (
                    <AnchorPill n={n} />
                  )}
                </g>
              ))}
            </g>
          </svg>
        ) : (
          <div className="h-64" />
        )}
        <div
          className="-mx-2 mt-2 sm:-mx-4 sm:mt-4 lg:-mx-6 lg:mt-6 min-h-12 border-t px-4 py-3 text-sm"
          aria-live="polite"
        >
          <Detail net={net} node={focused} cards={cards} />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-2">
          earlier
          <span
            className="h-2 w-16"
            style={{
              background:
                "linear-gradient(to right, var(--net-old), var(--net-new))",
            }}
          />
          recent
        </span>
        <span>Bigger: highlighted, or used in more places</span>
        <span>Lines: used in that job or project</span>
        {net.nodes.some((n) => n.kind === "skill" && n.degree === 0) ? (
          <span>Grey: not tied to a job or project on this page</span>
        ) : null}
      </div>

      <details className="text-sm">
        <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
          All skills as a list
        </summary>
        <ul className="mt-2 space-y-1">
          {net.nodes
            .filter((n) => n.kind === "skill")
            .sort((a, b) => a.item.title.localeCompare(b.item.title))
            .map((n) => {
              const used = neighbours(net, n.id).map((x) => x.label);
              return (
                <li key={n.id}>
                  <span className="font-medium">{n.item.title}</span>
                  {n.item.subtitle ? (
                    <span className="text-muted-foreground">
                      {" "}
                      · {n.item.subtitle}
                    </span>
                  ) : null}
                  {used.length ? (
                    <span className="text-muted-foreground">
                      , used in {used.join(", ")}
                    </span>
                  ) : null}
                </li>
              );
            })}
        </ul>
      </details>
    </section>
  );
}
