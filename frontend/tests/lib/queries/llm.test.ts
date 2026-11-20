import { describe, it, expect } from "vitest";
import {
  configPayload,
  checkResultLabel,
  modelListLabel,
  type LLMConfigRow,
} from "@/lib/queries/llm";

/**
 * Red-first tests for the five-field config era ([fullstack]-llm-config-rework):
 * one credential per commercial provider, `api_key` write-only. The whole
 * alias-era mask vocabulary (PROVIDER_SPECS, buildStructuredExtra,
 * parseExtraJson, rowToState, switchProvider) is deleted, not tested.
 * The executors section is covered by tests/lib/executors.test.ts.
 */

describe("configPayload", () => {
  it("create: provider + trimmed key", () => {
    expect(configPayload({ provider: "anthropic", apiKey: "  sk-123  " })).toEqual({
      provider: "anthropic",
      api_key: "sk-123",
    });
  });

  it("omits a blank key — a PATCH without api_key keeps the stored key", () => {
    expect(configPayload({ provider: "anthropic" })).toEqual({
      provider: "anthropic",
    });
    expect(configPayload({ provider: "anthropic", apiKey: "   " })).toEqual({
      provider: "anthropic",
    });
  });

  it("default toggle travels without touching the key", () => {
    expect(configPayload({ provider: "openai", makeDefault: true })).toEqual({
      provider: "openai",
      default: true,
    });
    expect(configPayload({ provider: "openai", makeDefault: false })).toEqual({
      provider: "openai",
      default: false,
    });
  });

  it("consent travels only when the box is ticked", () => {
    expect(
      configPayload({ provider: "openai", apiKey: "k", transferConsent: true }),
    ).toEqual({ provider: "openai", api_key: "k", transfer_consent: true });
    expect(
      configPayload({
        provider: "openai",
        apiKey: "k",
        transferConsent: false,
      }),
    ).toEqual({ provider: "openai", api_key: "k" });
  });
});

describe("LLMConfigRow (five-field shape)", () => {
  it("compiles with exactly the server's fields — no alias/model/url/extra", () => {
    const row: LLMConfigRow = {
      id: 1,
      provider: "anthropic",
      default: true,
      has_api_key: true,
      transfer_consent_at: null,
      model_count: 0,
      models_checked_at: null,
      created_at: "",
      updated_at: "",
    };
    expect(row.has_api_key).toBe(true);
    expect("alias" in row).toBe(false);
    expect("model" in row).toBe(false);
    expect("extra" in row).toBe(false);
    // the list itself comes with the executors, a count here
    expect("models" in row).toBe(false);
  });
});

describe("checkResultLabel (per-row connectivity check)", () => {
  it("renders success as OK + latency + what the listing found", () => {
    expect(checkResultLabel({ ok: true, latency_ms: 812, models: 14 })).toBe(
      "OK · 812 ms · 14 models",
    );
    expect(checkResultLabel({ ok: true, latency_ms: 90, models: 1 })).toBe(
      "OK · 90 ms · 1 model",
    );
  });

  it("renders failure as the raw error text", () => {
    expect(
      checkResultLabel({ ok: false, error: "connection refused", models: 3 }),
    ).toBe("connection refused");
  });
});

describe("modelListLabel (what the key reaches)", () => {
  const when = (iso: string) => `<${iso}>`;

  it("says when it isn't listed yet", () => {
    expect(
      modelListLabel({ model_count: 0, models_checked_at: null }, when),
    ).toBe("No model list yet.");
  });

  it("names the count and when the provider listed it", () => {
    expect(
      modelListLabel(
        { model_count: 12, models_checked_at: "2026-11-20T12:00:00Z" },
        when,
      ),
    ).toBe("Reaches 12 models · checked <2026-11-20T12:00:00Z>.");
    expect(
      modelListLabel({ model_count: 1, models_checked_at: "x" }, when),
    ).toBe("Reaches 1 model · checked <x>.");
  });

  it("a listing that found nothing still counts as checked", () => {
    expect(
      modelListLabel({ model_count: 0, models_checked_at: "x" }, when),
    ).toBe("Reaches 0 models · checked <x>.");
  });
});
