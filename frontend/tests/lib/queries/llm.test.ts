import { describe, it, expect } from "vitest";
import {
  configPayload,
  checkResultLabel,
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
});

describe("LLMConfigRow (five-field shape)", () => {
  it("compiles with exactly the server's fields — no alias/model/url/extra", () => {
    const row: LLMConfigRow = {
      id: 1,
      provider: "anthropic",
      default: true,
      has_api_key: true,
      created_at: "",
      updated_at: "",
    };
    expect(row.has_api_key).toBe(true);
    expect("alias" in row).toBe(false);
    expect("model" in row).toBe(false);
    expect("extra" in row).toBe(false);
  });
});

describe("checkResultLabel (per-row connectivity check)", () => {
  it("renders success as OK + latency", () => {
    expect(checkResultLabel({ ok: true, latency_ms: 812 })).toBe("OK · 812 ms");
  });

  it("renders failure as the raw error text", () => {
    expect(checkResultLabel({ ok: false, error: "connection refused" })).toBe(
      "connection refused",
    );
  });
});
