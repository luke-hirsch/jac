import { describe, expect, it } from "vitest";
import {
  buildNetwork,
  layoutNetwork,
  litIds,
  nodeBox,
  skillRadius,
  type Network,
} from "@/lib/portfolio/network";
import type { PortfolioItem } from "@/lib/queries/portfolio";

/**
 * The portfolio's skill network: jobs and projects as anchors, skills hung off the ones that used
 * them, time left to right and on a colour gradient, highlighted and much-used skills bigger.
 */

const TODAY = Date.parse("2026-11-24");

const item = (
  over: Partial<PortfolioItem> & { id: string },
): PortfolioItem => ({
  type: over.id.split(":")[0] as PortfolioItem["type"],
  title: over.id,
  domains: [],
  ...over,
});

const job = (id: string, skills: string[], over: Partial<PortfolioItem> = {}) =>
  item({ id, skills, title: `Dev ${id}`, subtitle: `Co ${id}`, ...over });
const skill = (id: string, domains: string[] = []) =>
  item({ id, title: `Skill ${id}`, domains });

const node = (net: Network, id: string) => {
  const n = net.nodes.find((x) => x.id === id);
  if (!n) throw new Error(`no node ${id}`);
  return n;
};

describe("buildNetwork", () => {
  it("is null without skills: there's nothing to draw", () => {
    expect(buildNetwork([job("job:1", [])], [], TODAY)).toBeNull();
  });

  it("draws a line only where both ends are on the page", () => {
    const net = buildNetwork(
      [],
      [job("job:1", ["skill:1", "skill:9"]), skill("skill:1")],
      TODAY,
    )!;
    expect(net.edges).toEqual([{ anchor: "job:1", skill: "skill:1" }]);
  });

  it("leaves out an anchor with no line: it would float alone", () => {
    const net = buildNetwork(
      [],
      [job("job:1", []), job("job:2", ["skill:1"]), skill("skill:1")],
      TODAY,
    )!;
    expect(net.nodes.map((n) => n.id)).toEqual(["job:2", "skill:1"]);
  });

  it("keeps a skill no anchor used, uncoloured", () => {
    const net = buildNetwork([], [skill("skill:1")], TODAY)!;
    expect(node(net, "skill:1")).toMatchObject({ degree: 0, recency: null });
  });

  it("runs time from the first anchor to end to today, and a skill takes its latest", () => {
    const net = buildNetwork(
      [],
      [
        job("job:1", ["skill:1"], {
          started: "2010-01-01",
          ended: "2012-01-01",
        }),
        job("job:2", ["skill:1", "skill:2"], { started: "2020-01-01" }),
        job("job:3", ["skill:2"], {
          started: "2014-01-01",
          ended: "2019-05-01",
        }),
        skill("skill:1"),
        skill("skill:2"),
      ],
      TODAY,
    )!;
    expect(node(net, "job:1").recency).toBe(0);
    expect(node(net, "job:2").recency).toBe(1); // still running
    expect(node(net, "job:3").recency).toBe(0.5); // evenly stepped, not by calendar
    expect(node(net, "skill:1").recency).toBe(1);
    expect(node(net, "skill:2").recency).toBe(1);
  });

  it("an undated anchor has no place in time", () => {
    const net = buildNetwork(
      [],
      [item({ id: "project:1", skills: ["skill:1"] }), skill("skill:1")],
      TODAY,
    )!;
    expect(node(net, "project:1").recency).toBeNull();
    expect(node(net, "skill:1").recency).toBeNull();
  });

  it("counts lines, and marks what's in the Highlights", () => {
    const net = buildNetwork(
      [skill("skill:1")],
      [
        job("job:1", ["skill:1", "skill:2"]),
        job("job:2", ["skill:1"]),
        skill("skill:2"),
      ],
      TODAY,
    )!;
    expect(node(net, "skill:1")).toMatchObject({ degree: 2, featured: true });
    expect(node(net, "skill:2")).toMatchObject({ degree: 1, featured: false });
    expect(node(net, "job:1").degree).toBe(2);
  });

  it("bigger: highlighted, then used more", () => {
    const net = buildNetwork(
      [skill("skill:1")],
      [
        job("job:1", ["skill:1", "skill:2", "skill:3"]),
        job("job:2", ["skill:2"]),
        skill("skill:2"),
        skill("skill:3"),
      ],
      TODAY,
    )!;
    const r = (id: string) => skillRadius(node(net, id));
    expect(r("skill:1")).toBeGreaterThan(r("skill:3"));
    expect(r("skill:2")).toBeGreaterThan(r("skill:3"));
  });

  it("names a job by its company, and by title too when the company repeats", () => {
    const net = buildNetwork(
      [],
      [
        job("job:1", ["skill:1"], { title: "Intern", subtitle: "Acme" }),
        job("job:2", ["skill:1"], { title: "Engineer", subtitle: "Acme" }),
        job("job:3", ["skill:1"], { title: "Lead", subtitle: "Initech" }),
        item({ id: "project:1", title: "jac", skills: ["skill:1"] }),
        skill("skill:1"),
      ],
      TODAY,
    )!;
    expect(node(net, "job:1").label).toBe("Intern · Acme");
    expect(node(net, "job:2").label).toBe("Engineer · Acme");
    expect(node(net, "job:3").label).toBe("Initech");
    expect(node(net, "project:1").label).toBe("jac");
  });

  it("clips a long label", () => {
    const net = buildNetwork(
      [],
      [item({ id: "skill:1", title: "x".repeat(60) })],
      TODAY,
    )!;
    expect(node(net, "skill:1").label.length).toBeLessThanOrEqual(28);
    expect(node(net, "skill:1").label.endsWith("…")).toBe(true);
  });

  it("lists the domains, most common first", () => {
    const net = buildNetwork(
      [],
      [
        skill("skill:1", ["web", "data"]),
        skill("skill:2", ["web"]),
        skill("skill:3", ["music"]),
      ],
      TODAY,
    )!;
    expect(net.domains).toEqual(["web", "data", "music"]);
  });

  it("doesn't move when a ranking reorders the page", () => {
    const a = buildNetwork(
      [],
      [job("job:1", ["skill:1"]), skill("skill:1"), skill("skill:2")],
      TODAY,
    )!;
    const b = buildNetwork(
      [],
      [skill("skill:2"), skill("skill:1"), job("job:1", ["skill:1"])],
      TODAY,
    )!;
    expect(b.nodes.map((n) => n.id)).toEqual(a.nodes.map((n) => n.id));
  });
});

describe("litIds", () => {
  const net = buildNetwork(
    [],
    [
      job("job:1", ["skill:1", "skill:2"]),
      job("job:2", ["skill:2"]),
      skill("skill:1", ["web"]),
      skill("skill:2"),
    ],
    TODAY,
  )!;

  it("everything, when nothing is focused", () => {
    expect(litIds(net, null, null)).toBeNull();
  });

  it("a skill lights its anchors", () => {
    expect(litIds(net, "skill:2", null)).toEqual(
      new Set(["skill:2", "job:1", "job:2"]),
    );
  });

  it("an anchor lights its skills", () => {
    expect(litIds(net, "job:1", null)).toEqual(
      new Set(["job:1", "skill:1", "skill:2"]),
    );
  });

  it("a domain lights what carries it; a focus wins over it", () => {
    expect(litIds(net, null, "web")).toEqual(new Set(["skill:1"]));
    expect(litIds(net, "job:2", "web")).toEqual(new Set(["job:2", "skill:2"]));
  });
});

/** A career shaped like a real one: a dozen anchors over fifteen years, sixty skills, one job
 *  that used twenty of them, some used nowhere. */
function bigNetwork(): Network {
  const items: PortfolioItem[] = [];
  for (let s = 1; s <= 60; s++) items.push(skill(`skill:${s}`));
  for (let a = 1; a <= 12; a++) {
    const used =
      a === 12
        ? Array.from({ length: 20 }, (_, k) => `skill:${k + 1}`)
        : [`skill:${((a * 3) % 45) + 1}`, `skill:${((a * 7) % 45) + 1}`];
    items.push(
      job(`job:${a}`, used, {
        started: `${2008 + a}-01-01`,
        ended: a === 12 ? null : `${2009 + a}-06-01`,
      }),
    );
  }
  return buildNetwork(items.slice(0, 5), items.slice(5), TODAY)!;
}

describe("layoutNetwork", () => {
  const net = bigNetwork();
  const layout = layoutNetwork(net, 860);

  const rect = (n: (typeof layout.nodes)[number]) => ({
    l: n.x + n.box.left,
    r: n.x + n.box.right,
    t: n.y + n.box.top,
    b: n.y + n.box.bottom,
  });

  it("places every node inside the box it reports", () => {
    for (const n of layout.nodes) {
      const r = rect(n);
      expect(r.l).toBeGreaterThanOrEqual(0);
      expect(r.r).toBeLessThanOrEqual(layout.width);
      expect(r.t).toBeGreaterThanOrEqual(0);
      expect(r.b).toBeLessThanOrEqual(layout.height);
    }
  });

  it("no label sits on another", () => {
    const rects = layout.nodes.map(rect);
    for (let i = 0; i < rects.length; i++)
      for (let j = i + 1; j < rects.length; j++) {
        const a = rects[i];
        const b = rects[j];
        const overlap =
          Math.min(a.r, b.r) - Math.max(a.l, b.l) > 0.5 &&
          Math.min(a.b, b.b) - Math.max(a.t, b.t) > 0.5;
        expect(overlap, `${layout.nodes[i].id} × ${layout.nodes[j].id}`).toBe(
          false,
        );
      }
  });

  it("runs time left to right", () => {
    const x = (id: string) => layout.nodes.find((n) => n.id === id)!.x;
    expect(x("job:1")).toBeLessThan(x("job:6"));
    expect(x("job:6")).toBeLessThan(x("job:12"));
  });

  it("the skills no job or project used sit as a band just under the rest", () => {
    const orphan = (id: string) =>
      !net.edges.some((e) => e.skill === id) && id.startsWith("skill:");
    for (const w of [860, 340]) {
      const l = layoutNetwork(net, w);
      const rest = l.nodes.filter((n) => !orphan(n.id));
      const band = l.nodes.filter((n) => orphan(n.id));
      expect(band.length).toBeGreaterThan(0);
      const restBottom = Math.max(...rest.map((n) => n.y + n.box.bottom));
      const bandTop = Math.min(...band.map((n) => n.y + n.box.top));
      expect(bandTop - restBottom).toBeGreaterThan(20);
      expect(bandTop - restBottom).toBeLessThan(40);
    }
  });

  it("is the same drawing every time", () => {
    const again = layoutNetwork(net, 860);
    expect(again.nodes.map((n) => [n.x, n.y])).toEqual(
      layout.nodes.map((n) => [n.x, n.y]),
    );
  });

  it("a phone's width makes it taller, not unreadable", () => {
    const narrow = layoutNetwork(net, 340);
    expect(narrow.height).toBeGreaterThan(layout.height);
    for (const n of narrow.nodes) {
      expect(n.x + n.box.left).toBeGreaterThanOrEqual(0);
      expect(n.x + n.box.right).toBeLessThanOrEqual(340);
    }
  });

  it("a skill's box holds its dot and its label", () => {
    const n = net.nodes.find((x) => x.kind === "skill")!;
    const b = nodeBox(n);
    expect(-b.left).toBe(skillRadius(n));
    expect(b.right).toBeGreaterThan(skillRadius(n) + n.label.length * 5);
  });
});
