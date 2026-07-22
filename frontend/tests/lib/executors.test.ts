import { describe, it, expect } from "vitest";
import {
  defaultExecutorRow,
  defaultModeFor,
  defaultModelFor,
  executorDisabledReason,
  providerLabel,
  type ExecutorRow,
} from "@/lib/queries/llm";

/**
 * Red-first tests for the executors section of `llm.ts`
 * ([frontend]-model-first-generate-panel). They pin the endpoint-row → picker
 * contract: disabled-with-reason (never hidden), default preselection, and the
 * per-pick model/mode defaults. No DOM, no network.
 */

function hirschai(over: Partial<ExecutorRow> = {}): ExecutorRow {
  return {
    provider: "ollama",
    label: "HirschAI",
    self_hosted: true,
    configured: true,
    reachable: true,
    default: true,
    models: [],
    modes: ["standard"],
    knobs: {},
    ...over,
  };
}

function anthropic(over: Partial<ExecutorRow> = {}): ExecutorRow {
  return {
    provider: "anthropic",
    label: "Anthropic",
    self_hosted: false,
    configured: true,
    reachable: null,
    default: false,
    models: [
      { id: "claude-sonnet-5", label: "Claude Sonnet 5", default: true },
      { id: "claude-opus-4-8", label: "Claude Opus 4.8" },
    ],
    modes: ["standard", "high"],
    knobs: {},
    ...over,
  };
}

describe("executorDisabledReason", () => {
  it("HirschAI up and commercial-with-key are enabled", () => {
    expect(executorDisabledReason(hirschai())).toBeNull();
    expect(executorDisabledReason(anthropic())).toBeNull();
  });

  it("HirschAI down reads 'offline' — the reason doubles as status copy", () => {
    expect(executorDisabledReason(hirschai({ reachable: false }))).toBe(
      "offline",
    );
  });

  it("an unkeyed commercial row reads 'no API key'", () => {
    expect(executorDisabledReason(anthropic({ configured: false }))).toBe(
      "no API key",
    );
  });

  it("a commercial row is never 'offline' — reachable stays null, unprobed", () => {
    expect(executorDisabledReason(anthropic({ reachable: null }))).toBeNull();
  });
});

describe("defaultExecutorRow (picker preselect)", () => {
  it("prefers the backend's default row when usable", () => {
    const commercial = anthropic({ default: true });
    expect(defaultExecutorRow([hirschai({ default: false }), commercial])).toBe(
      commercial,
    );
  });

  it("skips a disabled default and falls to the first usable row", () => {
    const tower = hirschai({ default: false });
    expect(
      defaultExecutorRow([
        tower,
        anthropic({ default: true, configured: false }),
      ]),
    ).toBe(tower);
  });

  it("null when every row is disabled — the panel's offline state", () => {
    expect(
      defaultExecutorRow([
        hirschai({ reachable: false }),
        anthropic({ configured: false }),
      ]),
    ).toBeNull();
  });
});

describe("defaultModelFor", () => {
  it("takes the catalog default", () => {
    expect(defaultModelFor(anthropic())).toBe("claude-sonnet-5");
  });

  it("falls back to the first model without a default flag", () => {
    const row = anthropic({
      models: [
        { id: "gpt-5.6-terra", label: "Terra" },
        { id: "gpt-5.6-sol", label: "Sol" },
      ],
    });
    expect(defaultModelFor(row)).toBe("gpt-5.6-terra");
  });

  it("empty for HirschAI — the tower model is fixed server-side, never sent", () => {
    expect(defaultModelFor(hirschai())).toBe("");
  });
});

describe("defaultModeFor", () => {
  it("commercial rows start on high", () => {
    expect(defaultModeFor(anthropic())).toBe("high");
  });

  it("HirschAI starts (and stays) on standard", () => {
    expect(defaultModeFor(hirschai())).toBe("standard");
  });
});

describe("providerLabel (result badges)", () => {
  const rows = [hirschai(), anthropic()];

  it("maps the raw meta.provider key to the row label", () => {
    expect(providerLabel(rows, "ollama")).toBe("HirschAI");
    expect(providerLabel(rows, "anthropic")).toBe("Anthropic");
  });

  it("falls back to the raw key when the row vanished", () => {
    expect(providerLabel(rows, "openai")).toBe("openai");
    expect(providerLabel([], "ollama")).toBe("ollama");
  });
});
