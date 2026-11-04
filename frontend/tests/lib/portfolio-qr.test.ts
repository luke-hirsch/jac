import { describe, expect, it } from "vitest";
import { qrDataUrl } from "@/lib/portfolio/qr";

/**
 * Guide [fullstack]-portfolio-cv-qr: the raster QR helper react-pdf's <Image> consumes
 * (the qrcode.react SVG component can't be rendered by react-pdf). Pure node — the
 * `qrcode` lib runs headless.
 */
describe("qrDataUrl", () => {
  it("resolves to a base64 png data URL", async () => {
    const url = await qrDataUrl("https://lukehirsch.com/portfolio/acme-x7f3");
    expect(url.startsWith("data:image/png;base64,")).toBe(true);
    expect(url.length).toBeGreaterThan(100);
  });

  it("is deterministic for the same input", async () => {
    const a = await qrDataUrl("https://example.com/p/abcd");
    const b = await qrDataUrl("https://example.com/p/abcd");
    expect(a).toBe(b);
  });

  it("differs for different inputs", async () => {
    const a = await qrDataUrl("https://example.com/p/aaaa");
    const b = await qrDataUrl("https://example.com/p/bbbb");
    expect(a).not.toBe(b);
  });
});
