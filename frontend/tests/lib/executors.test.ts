import { describe, it, expect } from "vitest";
import {
  defaultExecutorRow,
  defaultModeFor,
  defaultModelFor,
  executorDisabledReason,
  keepKnobs,
  knobsFor,
  providerLabel,
  type ExecutorRow,
} from "@/lib/queries/llm";

/**
 * Red-first tests for the executors section of `llm.ts`
 * ([frontend]-model-first-generate-panel, per-model knobs since
 * [fullstack]-live-model-catalog). They pin the endpoint-row → picker contract:
 * disabled-with-reason (never hidden), default preselection, the per-pick
 * model/mode defaults, and knobs that belong to the picked model. No DOM, no network.
 */

const ALL_EFFORT = {
  effort: { choices: ["low", "medium", "high", "xhigh", "max"] },
};
const BUDGET_EFFORT = { effort: { choices: ["low", "medium", "high"] } };

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
    ...over,
  };
}

// The key's own list, newest first: the default pick is not the first entry.
function anthropic(over: Partial<ExecutorRow> = {}): ExecutorRow {
  return {
    provider: "anthropic",
    label: "Anthropic",
    self_hosted: false,
    configured: true,
    reachable: null,
    default: false,
    models: [
      { id: "claude-opus-4-8", label: "Claude Opus 4.8", knobs: ALL_EFFORT },
      {
        id: "claude-sonnet-5",
        label: "Claude Sonnet 5",
        default: true,
        knobs: ALL_EFFORT,
      },
      {
        id: "claude-haiku-4-5",
        label: "Claude Haiku 4.5",
        knobs: BUDGET_EFFORT,
      },
      { id: "claude-old", label: "Claude Old", knobs: {} },
    ],
    modes: ["standard", "high"],
    ...over,
  };
}

describe("executorDisabledReason", () => {
  it("HirschAI up and commercial-with-key are enabled", () => {
    expect(executorDisabledReason(hirschai())).toBeNull();
    expect(executorDisabledReason(anthropic())).toBeNull();
  });

  it("HirschAI asleep can still be picked: the run waits for it", () => {
    expect(executorDisabledReason(hirschai({ reachable: false }))).toBeNull();
    expect(executorDisabledReason(hirschai({ reachable: null }))).toBeNull();
  });

  it("an unkeyed commercial row reads 'no API key'", () => {
    expect(executorDisabledReason(anthropic({ configured: false }))).toBe(
      "no API key",
    );
  });

  it("a commercial row only needs its key", () => {
    expect(executorDisabledReason(anthropic({ reachable: null }))).toBeNull();
  });

  it("a key the provider hasn't listed yet has nothing to run on", () => {
    expect(executorDisabledReason(anthropic({ models: [] }))).toBe(
      "no model list yet",
    );
    // a missing key says so first
    expect(
      executorDisabledReason(anthropic({ configured: false, models: [] })),
    ).toBe("no API key");
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

  it("HirschAI asleep is still the fallback, never an empty panel", () => {
    const tower = hirschai({ reachable: false });
    expect(defaultExecutorRow([tower, anthropic({ configured: false })])).toBe(
      tower,
    );
  });

  it("a default key without a model list isn't preselected", () => {
    const tower = hirschai({ default: false });
    expect(
      defaultExecutorRow([tower, anthropic({ default: true, models: [] })]),
    ).toBe(tower);
  });

  it("null only for an empty list", () => {
    expect(defaultExecutorRow([])).toBeNull();
  });
});

describe("defaultModelFor", () => {
  it("takes the server's default pick, not the newest model", () => {
    expect(defaultModelFor(anthropic())).toBe("claude-sonnet-5");
  });

  it("falls back to the first model without a default flag", () => {
    const row = anthropic({
      models: [
        { id: "gpt-5.6-terra", label: "gpt-5.6-terra", knobs: {} },
        { id: "gpt-5.6-sol", label: "gpt-5.6-sol", knobs: {} },
      ],
    });
    expect(defaultModelFor(row)).toBe("gpt-5.6-terra");
  });

  it("empty for HirschAI — the tower model is fixed server-side, never sent", () => {
    expect(defaultModelFor(hirschai())).toBe("");
  });
});

describe("knobsFor (the picked model's controls)", () => {
  it("each model brings its own levels", () => {
    expect(knobsFor(anthropic(), "claude-opus-4-8")).toEqual(ALL_EFFORT);
    expect(knobsFor(anthropic(), "claude-haiku-4-5")).toEqual(BUDGET_EFFORT);
  });

  it("nothing for a model without knobs, an unlisted one, or HirschAI", () => {
    expect(knobsFor(anthropic(), "claude-old")).toEqual({});
    expect(knobsFor(anthropic(), "claude-gone")).toEqual({});
    expect(knobsFor(hirschai(), "")).toEqual({});
    expect(knobsFor(null, "claude-opus-4-8")).toEqual({});
  });
});

describe("keepKnobs (a model switch)", () => {
  it("keeps a value the new model offers too", () => {
    expect(keepKnobs({ effort: "high" }, BUDGET_EFFORT)).toEqual({
      effort: "high",
    });
  });

  it("drops a level the new model doesn't offer — the server would 400 on it", () => {
    expect(keepKnobs({ effort: "max" }, BUDGET_EFFORT)).toEqual({});
    expect(keepKnobs({ effort: "high" }, {})).toEqual({});
  });

  it("drops blanks and knobs the new model doesn't have at all", () => {
    expect(keepKnobs({ effort: "", temperature: "0.3" }, ALL_EFFORT)).toEqual(
      {},
    );
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
