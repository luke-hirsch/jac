import {
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import type { PortfolioItem } from "@/lib/queries/portfolio";

/** The portfolio's skills as a network: the jobs and projects on the page are the anchors, each
 *  skill hangs off the ones that used it (`item.skills`, spa.portfolio._link_skills), so clusters
 *  show what was used together. Time runs left to right and on a colour gradient, in the order the
 *  anchors ended (running ones last); a skill takes the colour of the most recent anchor that used
 *  it.
 *  Bigger: highlighted on this page (the owner's favourites, or a link's selection) or used more.
 *  Pure, so the tests/ regime covers it; the SVG lives in components/portfolio/skill-network. */

export type NetKind = "job" | "project" | "skill";

export type NetNode = {
  id: string;
  kind: NetKind;
  item: PortfolioItem;
  label: string;
  /** 0 (the anchor that ended first) … 1 (the most recent), evenly stepped; null: nothing to
   *  place it by. */
  recency: number | null;
  /** How many lines meet here. */
  degree: number;
  /** In the page's Highlights. */
  featured: boolean;
};

export type NetEdge = { anchor: string; skill: string };

export type Network = {
  nodes: NetNode[];
  edges: NetEdge[];
  /** Every domain on the network's nodes, the most common first. */
  domains: string[];
};

const MAX_LABEL = 28;

const clip = (s: string) =>
  s.length > MAX_LABEL ? `${s.slice(0, MAX_LABEL - 1).trimEnd()}…` : s;

/** When an anchor was last true: its end, today while it runs, null without dates. */
function lastUsed(item: PortfolioItem, today: number): number | null {
  if (item.ended) return Date.parse(item.ended);
  if (item.started) return today;
  return null;
}

/** A job reads best by its company; two jobs at one company also need the title. */
function anchorLabels(anchors: PortfolioItem[]): Map<string, string> {
  const company = (i: PortfolioItem) =>
    i.type === "job" ? i.subtitle || i.title : i.title;
  const seen = new Map<string, number>();
  for (const a of anchors)
    seen.set(company(a), (seen.get(company(a)) ?? 0) + 1);
  return new Map(
    anchors.map((a) => {
      const name = company(a);
      const label =
        a.type === "job" && (seen.get(name) ?? 0) > 1 && a.subtitle
          ? `${a.title} · ${a.subtitle}`
          : name;
      return [a.id, clip(label)];
    }),
  );
}

/** The network of a page, or null when it shows no skills. Nodes are sorted by id, so a ranking
 *  that reorders the page doesn't move the drawing. */
export function buildNetwork(
  featured: PortfolioItem[],
  more: PortfolioItem[],
  today: number = Date.now(),
): Network | null {
  const items = [...featured, ...more];
  const skills = items.filter((i) => i.type === "skill");
  if (skills.length === 0) return null;
  const skillIds = new Set(skills.map((s) => s.id));
  const highlighted = new Set(featured.map((i) => i.id));

  const anchors = items.filter(
    (i) =>
      (i.type === "job" || i.type === "project") &&
      (i.skills ?? []).some((s) => skillIds.has(s)),
  );
  const edges: NetEdge[] = anchors.flatMap((a) =>
    (a.skills ?? [])
      .filter((s) => skillIds.has(s))
      .map((s) => ({ anchor: a.id, skill: s })),
  );

  // By order, not by calendar: most careers bunch up at the recent end, and evenly spaced steps
  // keep both the left-to-right order and the colours apart. Ties share a step.
  const times = new Map(anchors.map((a) => [a.id, lastUsed(a, today)]));
  const steps = [
    ...new Set([...times.values()].filter((t): t is number => t !== null)),
  ].sort((a, b) => a - b);
  const recencyOf = (t: number | null) =>
    t === null
      ? null
      : steps.length > 1
        ? steps.indexOf(t) / (steps.length - 1)
        : 1;

  const degree = new Map<string, number>();
  const skillRecency = new Map<string, number>();
  for (const e of edges) {
    degree.set(e.anchor, (degree.get(e.anchor) ?? 0) + 1);
    degree.set(e.skill, (degree.get(e.skill) ?? 0) + 1);
    const r = recencyOf(times.get(e.anchor) ?? null);
    if (r !== null)
      skillRecency.set(e.skill, Math.max(r, skillRecency.get(e.skill) ?? 0));
  }

  const labels = anchorLabels(anchors);
  const nodes: NetNode[] = [
    ...anchors.map((a) => ({
      id: a.id,
      kind: a.type as NetKind,
      item: a,
      label: labels.get(a.id) ?? clip(a.title),
      recency: recencyOf(times.get(a.id) ?? null),
      degree: degree.get(a.id) ?? 0,
      featured: highlighted.has(a.id),
    })),
    ...skills.map((s) => ({
      id: s.id,
      kind: "skill" as const,
      item: s,
      label: clip(s.title),
      recency: skillRecency.get(s.id) ?? null,
      degree: degree.get(s.id) ?? 0,
      featured: highlighted.has(s.id),
    })),
  ].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));

  const count = new Map<string, number>();
  for (const n of nodes)
    for (const d of n.item.domains) count.set(d, (count.get(d) ?? 0) + 1);
  const domains = [...count.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([d]) => d);

  return { nodes, edges, domains };
}

/** What lights up: the focused node and its neighbours, or every node in a domain. Null: all of
 *  it (nothing focused, no domain picked). */
export function litIds(
  net: Network,
  focus: string | null,
  domain: string | null,
): Set<string> | null {
  if (focus) {
    const lit = new Set([focus]);
    for (const e of net.edges) {
      if (e.anchor === focus) lit.add(e.skill);
      if (e.skill === focus) lit.add(e.anchor);
    }
    return lit;
  }
  if (domain) {
    return new Set(
      net.nodes.filter((n) => n.item.domains.includes(domain)).map((n) => n.id),
    );
  }
  return null;
}

/* ---------- size + layout ---------- */

/** A node's footprint around its point: a dot with its label to the right for a skill, a pill
 *  centred on it for an anchor. */
export type Box = { left: number; right: number; top: number; bottom: number };

/** Roughly Noto Sans' average advance; the layout only needs it close. */
const textWidth = (s: string, size: number) => s.length * size * 0.56;

export function skillRadius(n: NetNode): number {
  return 3.5 + 1.5 * Math.min(n.degree, 4) + (n.featured ? 3.5 : 0);
}

export function fontSize(n: NetNode): number {
  if (n.kind !== "skill") return 12.5;
  return n.featured ? 13 : 11.5;
}

export const PILL_HEIGHT = 24;
export const PILL_PAD = 10;

export function nodeBox(n: NetNode): Box {
  const size = fontSize(n);
  if (n.kind === "skill") {
    const r = skillRadius(n);
    const half = Math.max(r, size * 0.7);
    return {
      left: -r,
      right: r + 4 + textWidth(n.label, size),
      top: -half,
      bottom: half,
    };
  }
  const w = textWidth(n.label, size) + 2 * PILL_PAD;
  return {
    left: -w / 2,
    right: w / 2,
    top: -PILL_HEIGHT / 2,
    bottom: PILL_HEIGHT / 2,
  };
}

export type Placed = NetNode & { x: number; y: number; box: Box };
export type Layout = { nodes: Placed[]; width: number; height: number };

type SimNode = SimulationNodeDatum & {
  id: string;
  box: Box;
  anchor: boolean;
  orphan: boolean;
  targetX: number;
};

const GAP = 3;
const MARGIN = 8;
const BAND_GAP = 28;

/** Push overlapping boxes apart along their shallower overlap. Positions, not velocities: run
 *  between ticks and again at the end, until nothing overlaps. */
function separate(nodes: SimNode[], strength: number): boolean {
  let moved = false;
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j];
      const ax = a.x!,
        ay = a.y!,
        bx = b.x!,
        by = b.y!;
      const ox =
        Math.min(ax + a.box.right, bx + b.box.right) -
        Math.max(ax + a.box.left, bx + b.box.left) +
        GAP;
      const oy =
        Math.min(ay + a.box.bottom, by + b.box.bottom) -
        Math.max(ay + a.box.top, by + b.box.top) +
        GAP;
      if (ox <= 0 || oy <= 0) continue;
      moved = true;
      const acx = ax + (a.box.left + a.box.right) / 2;
      const bcx = bx + (b.box.left + b.box.right) / 2;
      const acy = ay + (a.box.top + a.box.bottom) / 2;
      const bcy = by + (b.box.top + b.box.bottom) / 2;
      if (ox < oy) {
        const d = ((acx <= bcx ? -1 : 1) * ox * strength) / 2;
        a.x = ax + d;
        b.x = bx - d;
      } else {
        const d = ((acy <= bcy ? -1 : 1) * oy * strength) / 2;
        a.y = ay + d;
        b.y = by - d;
      }
    }
  }
  return moved;
}

function keepInside(nodes: SimNode[], width: number) {
  for (const n of nodes) {
    const minX = MARGIN - n.box.left;
    const maxX = width - MARGIN - n.box.right;
    n.x =
      maxX < minX ? (minX + maxX) / 2 : Math.min(maxX, Math.max(minX, n.x!));
  }
}

/** Place the network in a `width`-wide box; the height follows from how much there is to place.
 *  Deterministic: fixed starting points, and d3-force's own seeded jiggle. */
export function layoutNetwork(net: Network, width: number): Layout {
  const boxes = new Map(net.nodes.map((n) => [n.id, nodeBox(n)]));
  const area = net.nodes.reduce((sum, n) => {
    const b = boxes.get(n.id)!;
    return sum + (b.right - b.left + 2 * GAP) * (b.bottom - b.top + 2 * GAP);
  }, 0);
  const height = Math.round(
    Math.min(1600, Math.max(260, (area * 2.6) / width)),
  );
  const usable = width - 2 * MARGIN;
  const midY = height * 0.45;

  const linked = new Set(net.edges.map((e) => e.skill));
  const anchorX = new Map(
    net.nodes
      .filter((n) => n.kind !== "skill")
      .map((n) => [n.id, MARGIN + usable * (0.12 + 0.76 * (n.recency ?? 0.5))]),
  );
  const skillX = new Map<string, number>();
  for (const id of linked) {
    const xs = net.edges
      .filter((e) => e.skill === id)
      .map((e) => anchorX.get(e.anchor)!);
    skillX.set(id, xs.reduce((a, b) => a + b, 0) / xs.length);
  }

  const sim: SimNode[] = net.nodes.map((n, i) => {
    const anchor = n.kind !== "skill";
    const orphan = !anchor && !linked.has(n.id);
    const targetX = anchor
      ? anchorX.get(n.id)!
      : orphan
        ? MARGIN + usable * ((i * 0.618034) % 1)
        : skillX.get(n.id)!;
    // Start near where each belongs, spread a little so nothing starts on top of anything.
    const angle = i * 2.399963;
    const spread = 18 + (i % 7) * 6;
    return {
      id: n.id,
      box: boxes.get(n.id)!,
      anchor,
      orphan,
      targetX,
      x: targetX + Math.cos(angle) * spread,
      y: (orphan ? height * 0.88 : midY) + Math.sin(angle) * spread,
    };
  });

  const links: SimulationLinkDatum<SimNode>[] = net.edges.map((e) => ({
    source: e.anchor,
    target: e.skill,
  }));
  const simulation = forceSimulation(sim)
    .force(
      "link",
      forceLink<SimNode, SimulationLinkDatum<SimNode>>(links)
        .id((d) => d.id)
        .distance(46)
        .strength(0.35),
    )
    .force("charge", forceManyBody<SimNode>().strength(-55).distanceMax(220))
    .force(
      "x",
      forceX<SimNode>((d) => d.targetX).strength((d) =>
        d.anchor ? 0.35 : 0.04,
      ),
    )
    .force(
      "y",
      forceY<SimNode>((d) => (d.orphan ? height * 0.88 : midY)).strength((d) =>
        d.orphan ? 0.12 : 0.05,
      ),
    )
    .stop();

  for (let t = 0; t < 300; t++) {
    simulation.tick();
    separate(sim, 0.7);
    keepInside(sim, width);
  }
  for (let t = 0; t < 200; t++) {
    const moved = separate(sim, 1);
    keepInside(sim, width);
    if (!moved) break;
  }

  // The skills no anchor used settle as a band of their own: move it up (or down) to sit just
  // under the rest, whatever height the estimate gave them. All of it below the rest, so it
  // overlaps nothing there.
  const rest = sim.filter((n) => !n.orphan);
  const band = sim.filter((n) => n.orphan);
  if (rest.length && band.length) {
    const restBottom = Math.max(...rest.map((n) => n.y! + n.box.bottom));
    const bandTop = Math.min(...band.map((n) => n.y! + n.box.top));
    const dy = restBottom + BAND_GAP - bandTop;
    for (const n of band) n.y = n.y! + dy;
  }

  // The height is whatever it took: shift everything down to the margin, then measure.
  const top = Math.min(...sim.map((n) => n.y! + n.box.top));
  const bottom = Math.max(...sim.map((n) => n.y! + n.box.bottom));
  const shift = MARGIN - top;
  const byId = new Map(sim.map((n) => [n.id, n]));
  return {
    width,
    height: Math.ceil(bottom - top + 2 * MARGIN),
    nodes: net.nodes.map((n) => {
      const s = byId.get(n.id)!;
      return { ...n, x: s.x!, y: s.y! + shift, box: s.box };
    }),
  };
}
