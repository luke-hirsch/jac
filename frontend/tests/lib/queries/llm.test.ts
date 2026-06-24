import { describe, it, expect } from "vitest";
import {
  PROVIDER_SPECS,
  buildStructuredExtra,
  parseExtraJson,
  toPayload,
  rowToState,
  switchProvider,
  type ConfigFormState,
  type LLMConfigRow,
  type Provider,
} from "@/lib/queries/llm";

/**
 * Red-first unit tests for the pure form<->payload helpers in `llm.ts`.
 * They drive the provider-mask logic the LLM-config tab relies on; no DOM,
 * no network — just the assembly/validation contract.
 */

const PROVIDERS: Provider[] = ["anthropic", "openai", "google", "custom", "ollama"];

function baseState(over: Partial<ConfigFormState> = {}): ConfigFormState {
  return {
    alias: "reasoning",
    provider: "anthropic",
    model: "claude-opus-4-8",
    url: "",
    max_tokens: "",
    api_key: "",
    extraJson: "",
    extraFields: {},
    ...over,
  };
}

describe("PROVIDER_SPECS", () => {
  it("covers exactly the five backend providers", () => {
    expect(Object.keys(PROVIDER_SPECS).sort()).toEqual([...PROVIDERS].sort());
  });

  it("marks custom/ollama as json+required-url, commercial as structured", () => {
    expect(PROVIDER_SPECS.custom.extra).toBe("json");
    expect(PROVIDER_SPECS.ollama.extra).toBe("json");
    expect(PROVIDER_SPECS.custom.url).toBe("required");
    expect(PROVIDER_SPECS.ollama.url).toBe("required");
    expect(PROVIDER_SPECS.anthropic.extra).toBe("structured");
    expect(PROVIDER_SPECS.openai.extra).toBe("structured");
    expect(PROVIDER_SPECS.google.extra).toBe("structured");
  });

  it("requires an api key for the commercial providers only", () => {
    expect(PROVIDER_SPECS.anthropic.apiKey).toBe("required");
    expect(PROVIDER_SPECS.openai.apiKey).toBe("required");
    expect(PROVIDER_SPECS.google.apiKey).toBe("required");
    expect(PROVIDER_SPECS.custom.apiKey).toBe("optional");
    expect(PROVIDER_SPECS.ollama.apiKey).toBe("optional");
  });
});

describe("buildStructuredExtra", () => {
  it("drops empty values and coerces number fields", () => {
    const extra = buildStructuredExtra(PROVIDER_SPECS.anthropic, { max_uses: "3" });
    expect(extra).toEqual({ max_uses: 3 });
  });

  it("omits a field left blank", () => {
    expect(buildStructuredExtra(PROVIDER_SPECS.anthropic, { max_uses: "" })).toEqual({});
    expect(buildStructuredExtra(PROVIDER_SPECS.openai, { reasoning_effort: "" })).toEqual({});
  });

  it("keeps select values as strings", () => {
    expect(
      buildStructuredExtra(PROVIDER_SPECS.openai, { reasoning_effort: "high" }),
    ).toEqual({ reasoning_effort: "high" });
  });
});

describe("parseExtraJson", () => {
  it("treats blank as an empty object", () => {
    expect(parseExtraJson("")).toEqual({});
    expect(parseExtraJson("   ")).toEqual({});
  });

  it("parses a JSON object", () => {
    expect(parseExtraJson('{"think": false, "timeout": 120}')).toEqual({
      think: false,
      timeout: 120,
    });
  });

  it("throws on invalid JSON", () => {
    expect(() => parseExtraJson("{bad")).toThrow(/valid JSON/i);
  });

  it("throws on non-object JSON (array / null / primitive)", () => {
    expect(() => parseExtraJson("[1,2]")).toThrow(/object/i);
    expect(() => parseExtraJson("null")).toThrow(/object/i);
    expect(() => parseExtraJson("42")).toThrow(/object/i);
  });
});

describe("toPayload", () => {
  it("builds extra from structured fields and blanks a hidden url", () => {
    const p = toPayload(
      baseState({ provider: "anthropic", url: "ignored", extraFields: { max_uses: "5" } }),
    );
    expect(p.url).toBe(""); // anthropic url is hidden
    expect(p.extra).toEqual({ max_uses: 5 });
  });

  it("parses the JSON textarea for json providers", () => {
    const p = toPayload(
      baseState({
        provider: "ollama",
        model: "llama3.2:1b",
        url: "http://localhost:11434",
        extraJson: '{"think": false}',
      }),
    );
    expect(p.url).toBe("http://localhost:11434");
    expect(p.extra).toEqual({ think: false });
  });

  it("parses max_tokens or leaves it null", () => {
    expect(toPayload(baseState({ max_tokens: "4096" })).max_tokens).toBe(4096);
    expect(toPayload(baseState({ max_tokens: "" })).max_tokens).toBeNull();
  });

  it("includes api_key only when entered", () => {
    expect(toPayload(baseState({ api_key: "" }))).not.toHaveProperty("api_key");
    expect(toPayload(baseState({ api_key: "  " }))).not.toHaveProperty("api_key");
    expect(toPayload(baseState({ api_key: "sk-123" })).api_key).toBe("sk-123");
  });

  it("propagates a JSON parse error", () => {
    expect(() => toPayload(baseState({ provider: "custom", extraJson: "{oops" }))).toThrow();
  });
});

describe("rowToState", () => {
  const row: LLMConfigRow = {
    id: 1,
    alias: "writer",
    provider: "ollama",
    model: "llama3.2:1b",
    url: "http://localhost:11434",
    max_tokens: 512,
    extra: { think: false, embed_model: "qwen3-embedding:0.6b" },
    has_api_key: true,
    created_at: "",
    updated_at: "",
  };

  it("pretty-prints json-provider extra into the textarea", () => {
    const s = rowToState(row);
    expect(JSON.parse(s.extraJson)).toEqual(row.extra);
    expect(s.max_tokens).toBe("512");
    expect(s.api_key).toBe(""); // never seeded — write-only
  });

  it("seeds structured extra fields by key", () => {
    const s = rowToState({
      ...row,
      provider: "openai",
      extra: { reasoning_effort: "high" },
    });
    expect(s.extraFields.reasoning_effort).toBe("high");
    expect(s.extraJson).toBe("");
  });

  it("gives empty defaults with no row", () => {
    const s = rowToState();
    expect(s.alias).toBe("");
    expect(s.provider).toBe("anthropic");
    expect(s.max_tokens).toBe("");
  });
});

describe("switchProvider", () => {
  it("clears the previous provider's extra inputs and hides url when appropriate", () => {
    const start = baseState({
      provider: "ollama",
      url: "http://localhost:11434",
      extraJson: '{"think": false}',
    });
    const next = switchProvider(start, "anthropic");
    expect(next.provider).toBe("anthropic");
    expect(next.extraJson).toBe("");
    expect(next.url).toBe(""); // anthropic hides url
    expect(next.alias).toBe(start.alias); // common fields preserved
    expect(next.model).toBe(start.model);
  });

  it("seeds empty structured field keys for the new provider", () => {
    const next = switchProvider(baseState(), "openai");
    expect(next.extraFields).toEqual({ reasoning_effort: "" });
  });
});
