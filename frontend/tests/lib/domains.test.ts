import { describe, expect, it } from "vitest";
import {
  deletePrompt,
  filterAfterRemoval,
  mergePrompt,
  mergeTargets,
  ownDomains,
  usesLabel,
} from "@/lib/domains";
import type { DomainRow } from "@/lib/queries/jac";

/**
 * The domain manager's lists and words: only the user's own domains are edited, the defaults
 * are merge targets, and every confirm says what happens to the entries.
 */

const domain = (
  id: number,
  name: string,
  uses = 0,
  is_default = false,
): DomainRow => ({ id, name, description: "", is_default, uses });

const fahio = domain(1, "fahio", 1);
const fashion = domain(2, "Fashion", 3);
const audio = domain(3, "audio", 0);
const music = domain(4, "music", 0, true);
const it_ = domain(5, "IT", 0, true);
const ROWS = [music, fashion, it_, fahio, audio];

describe("ownDomains", () => {
  it("leaves the defaults out and sorts by name, whatever the case", () => {
    expect(ownDomains(ROWS).map((d) => d.name)).toEqual([
      "audio",
      "fahio",
      "Fashion",
    ]);
  });
});

describe("mergeTargets", () => {
  it("offers every other domain, the user's own apart from the defaults", () => {
    const { own, defaults } = mergeTargets(ROWS, fahio.id);
    expect(own.map((d) => d.name)).toEqual(["audio", "Fashion"]);
    expect(defaults.map((d) => d.name)).toEqual(["IT", "music"]);
  });

  it("never offers the domain itself", () => {
    const { own, defaults } = mergeTargets(ROWS, fahio.id);
    expect([...own, ...defaults]).not.toContain(fahio);
  });
});

describe("usesLabel", () => {
  it("counts entries, and says so when there are none", () => {
    expect(usesLabel(0)).toBe("Not used");
    expect(usesLabel(1)).toBe("1 entry");
    expect(usesLabel(3)).toBe("3 entries");
  });
});

describe("deletePrompt", () => {
  it("says how many entries lose the tag", () => {
    expect(deletePrompt(fahio)).toBe('Delete "fahio"? 1 entry loses it.');
    expect(deletePrompt(fashion)).toBe('Delete "Fashion"? 3 entries lose it.');
    expect(deletePrompt(audio)).toBe('Delete "audio"? No entry uses it.');
  });
});

describe("mergePrompt", () => {
  it("says where the entries go and that the source is deleted", () => {
    expect(mergePrompt(fashion, music)).toBe(
      'Merge "Fashion" into "music"? Its 3 entries get "music". Then "Fashion" is deleted.',
    );
    expect(mergePrompt(audio, music)).toBe(
      'Merge "audio" into "music"? No entry uses "audio". Then "audio" is deleted.',
    );
  });

  it("has no em dash", () => {
    expect(mergePrompt(fahio, fashion)).not.toContain("—");
    expect(deletePrompt(fahio)).not.toContain("—");
  });
});

describe("filterAfterRemoval", () => {
  it("follows a merge to the target and clears a deleted one", () => {
    expect(filterAfterRemoval(fahio.id, fahio.id, fashion.id)).toBe(fashion.id);
    expect(filterAfterRemoval(fahio.id, fahio.id, null)).toBe("");
  });

  it("leaves a filter on another domain, or none, as it is", () => {
    expect(filterAfterRemoval(music.id, fahio.id, fashion.id)).toBe(music.id);
    expect(filterAfterRemoval("", fahio.id, null)).toBe("");
  });
});
