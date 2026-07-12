import { describe, expect, it } from "vitest";
import {
  REWRITE_STYLES,
  chatAliases,
  chatPayload,
  preferredRefineAlias,
  seedDiscussion,
} from "@/lib/letter-chat";
import type { RunSummary } from "@/lib/queries/applications";
import type { AliasInfo } from "@/lib/queries/llm";

/**
 * Pure helpers behind the letter refine popover + chat ([fullstack]-letter-refine-chat):
 * the preset rewrite styles, the standard+ alias gate, the "which model should refinement
 * default to" pick, and the payload/seed builders. No DOM, no network.
 */

function alias(name: string, strength: AliasInfo["strength"]): AliasInfo {
  return {
    alias: name,
    provider: "ollama",
    model: "m",
    strength,
    supports_embed: false,
    supports_web_search: false,
  };
}

function run(aliasName: string, status: RunSummary["status"]): RunSummary {
  return {
    id: 1,
    status,
    stage: "",
    grade: "",
    alias: aliasName,
    created_at: "2026-07-11T10:00:00Z",
  };
}

describe("REWRITE_STYLES", () => {
  it("offers exactly the three preset styles", () => {
    expect(REWRITE_STYLES).toHaveLength(3);
    expect(REWRITE_STYLES.map((s) => s.key)).toEqual([
      "shorter",
      "formal",
      "natural",
    ]);
  });
  it("each style carries a label and a non-empty instruction", () => {
    for (const s of REWRITE_STYLES) {
      expect(s.label.length).toBeGreaterThan(0);
      expect(s.instruction.length).toBeGreaterThan(10);
    }
  });
});

describe("chatAliases", () => {
  it("filters light aliases out — a 1B model cannot chat usefully", () => {
    const rows = [alias("default", "light"), alias("writer", "standard")];
    expect(chatAliases(rows).map((a) => a.alias)).toEqual(["writer"]);
  });
  it("empty when nothing standard+ is configured (chat hidden)", () => {
    expect(chatAliases([alias("default", "light")])).toEqual([]);
  });
});

describe("preferredRefineAlias", () => {
  const configured = [
    alias("default", "light"),
    alias("writer", "standard"),
    alias("big", "strong"),
  ];

  it("prefers the latest done run's alias when it is standard+", () => {
    const runs = [run("big", "done"), run("writer", "done")];
    expect(preferredRefineAlias(configured, runs)).toBe("big");
  });
  it("skips a done run whose alias is light or unconfigured", () => {
    const runs = [run("default", "done"), run("gone", "done"), run("writer", "done")];
    expect(preferredRefineAlias(configured, runs)).toBe("writer");
  });
  it("ignores non-done runs", () => {
    const runs = [run("big", "failed"), run("big", "pending")];
    expect(preferredRefineAlias(configured, runs)).toBe("writer");
  });
  it("falls back to the first standard+ alias without usable runs", () => {
    expect(preferredRefineAlias(configured, [])).toBe("writer");
  });
  it("null when only light models exist", () => {
    expect(preferredRefineAlias([alias("default", "light")], [])).toBeNull();
  });
});

describe("seedDiscussion", () => {
  it("wraps the highlighted passage into an opening user message", () => {
    const m = seedDiscussion("I shipped the billing service.");
    expect(m.role).toBe("user");
    expect(m.content).toContain("I shipped the billing service.");
  });
});

describe("chatPayload", () => {
  it("pins the request contract: alias + draft body + transcript", () => {
    const messages = [{ role: "user" as const, content: "Make it warmer." }];
    expect(chatPayload("writer", "Body.", messages)).toEqual({
      alias: "writer",
      body: "Body.",
      messages,
    });
  });
});
