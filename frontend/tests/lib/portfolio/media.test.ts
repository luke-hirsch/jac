import { beforeAll, describe, expect, it } from "vitest";

/**
 * Guide 29, the Studio's media player: the arithmetic under its custom controls. The clock, a bar
 * that shows real progress or none, and the keys a slider answers.
 *
 * Lazy import, as in appearance.test.ts: collectible while `@/lib/portfolio/media` doesn't exist
 * yet. `Mod` is the guide's contract.
 */

type Mod = {
  SEEK_STEP_S: number;
  mediaClock(seconds: number): string;
  progressOf(current: number, duration: number): number;
  seekFor(key: string, current: number, duration: number): number | null;
  seekAt(x: number, width: number, duration: number): number;
  clockText(current: number, duration: number): string;
  isVideo(mediaType: string | null | undefined): boolean;
};

let m: Mod;
beforeAll(async () => {
  m = (await import("@/lib/portfolio/media")) as unknown as Mod;
});

describe("mediaClock", () => {
  it("writes minutes and seconds, and hours once there are some", () => {
    expect(m.mediaClock(0)).toBe("0:00");
    expect(m.mediaClock(7.9)).toBe("0:07");
    expect(m.mediaClock(2292)).toBe("38:12");
    expect(m.mediaClock(3723)).toBe("1:02:03");
  });

  it("shows 0:00 for a length that isn't known yet", () => {
    // preload="none": until the visitor presses play, duration is NaN.
    expect(m.mediaClock(Number.NaN)).toBe("0:00");
    expect(m.mediaClock(Number.POSITIVE_INFINITY)).toBe("0:00");
    expect(m.mediaClock(-3)).toBe("0:00");
  });
});

describe("progressOf", () => {
  it("fills the bar by how much has played", () => {
    expect(m.progressOf(30, 120)).toBe(0.25);
    expect(m.progressOf(120, 120)).toBe(1);
  });

  it("stays empty while the length is unknown: no pretend progress", () => {
    expect(m.progressOf(10, Number.NaN)).toBe(0);
    expect(m.progressOf(10, Number.POSITIVE_INFINITY)).toBe(0);
    expect(m.progressOf(10, 0)).toBe(0);
  });

  it("never overfills", () => {
    expect(m.progressOf(130, 120)).toBe(1);
  });
});

describe("seekFor", () => {
  it("moves five seconds on the arrow keys, either direction", () => {
    expect(m.SEEK_STEP_S).toBe(5);
    expect(m.seekFor("ArrowRight", 60, 600)).toBe(65);
    expect(m.seekFor("ArrowUp", 60, 600)).toBe(65);
    expect(m.seekFor("ArrowLeft", 60, 600)).toBe(55);
    expect(m.seekFor("ArrowDown", 60, 600)).toBe(55);
  });

  it("moves a tenth on Page Up and Page Down, and to the ends on Home and End", () => {
    expect(m.seekFor("PageUp", 60, 600)).toBe(120);
    expect(m.seekFor("PageDown", 60, 600)).toBe(0);
    expect(m.seekFor("Home", 60, 600)).toBe(0);
    expect(m.seekFor("End", 60, 600)).toBe(600);
  });

  it("stays inside the recording", () => {
    expect(m.seekFor("ArrowLeft", 2, 600)).toBe(0);
    expect(m.seekFor("ArrowRight", 598, 600)).toBe(600);
  });

  it("leaves other keys alone, and every key while the length is unknown", () => {
    expect(m.seekFor("Tab", 60, 600)).toBeNull();
    expect(m.seekFor(" ", 60, 600)).toBeNull();
    expect(m.seekFor("ArrowRight", 0, Number.NaN)).toBeNull();
  });
});

describe("seekAt", () => {
  it("turns a point on the bar into a time", () => {
    expect(m.seekAt(150, 300, 600)).toBe(300);
    expect(m.seekAt(0, 300, 600)).toBe(0);
  });

  it("clamps a drag that runs off either end", () => {
    expect(m.seekAt(-20, 300, 600)).toBe(0);
    expect(m.seekAt(400, 300, 600)).toBe(600);
  });

  it("goes nowhere on a bar without a width or a recording without a length", () => {
    expect(m.seekAt(10, 0, 600)).toBe(0);
    expect(m.seekAt(10, 300, Number.NaN)).toBe(0);
  });
});

describe("clockText", () => {
  it("tells a screen reader where it is and how long it runs", () => {
    expect(m.clockText(62, 2292)).toBe("1:02 of 38:12");
    expect(m.clockText(62, Number.NaN)).toBe("1:02");
  });
});

describe("isVideo", () => {
  it("draws a picture for video and nothing for audio", () => {
    expect(m.isVideo("video/mp4")).toBe(true);
    expect(m.isVideo("video/webm")).toBe(true);
    expect(m.isVideo("audio/mpeg")).toBe(false);
    expect(m.isVideo("audio/mp4")).toBe(false);
    expect(m.isVideo(null)).toBe(false);
    expect(m.isVideo(undefined)).toBe(false);
  });
});
