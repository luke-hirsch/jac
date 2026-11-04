import { describe, it, expect } from "vitest";
import { ADDRESS_FORMS, registerLeaks } from "@/lib/register";

/**
 * [fullstack]-letter-register-de: the editor's copy of `jac/register.py`
 * `register_leaks`. The server only ever checked the body a run produced; this one
 * re-checks every edit and every AI rewrite. The cases mirror `RegisterLeakTests` in
 * `backend/jac/tests/test_pipeline.py` — keep the two in step.
 */

describe("registerLeaks (mirrors RegisterLeakTests)", () => {
  it("flags formal pronouns in an ihr letter", () => {
    const leaks = registerLeaks(
      "Ich freue mich, Sie und Ihr Team zu treffen.",
      "de",
      "ihr",
    );
    expect(leaks).toContain("Sie");
  });

  it("flags plural pronouns in a Sie letter", () => {
    expect(registerLeaks("Ich schreibe euch gerne.", "de", "sie")).toContain(
      "euch",
    );
  });

  it("flags du in both", () => {
    expect(registerLeaks("Ich schreibe dir.", "de", "ihr")).toEqual(["dir"]);
    expect(registerLeaks("Ich schreibe dir.", "de", "sie")).toEqual(["dir"]);
  });

  it("reports nothing for a clean letter", () => {
    expect(
      registerLeaks("Ich freue mich auf euch und eure Arbeit.", "de", "ihr"),
    ).toEqual([]);
  });

  it("never flags English or formless letters", () => {
    expect(registerLeaks("I look forward to meeting you.", "en", "")).toEqual(
      [],
    );
    expect(registerLeaks("Sie und Ihr Team.", "de", "")).toEqual([]);
    // A form left over from before the letter was switched to English.
    expect(registerLeaks("Sie und Ihr Team.", "en", "ihr")).toEqual([]);
  });

  it("dedupes repeated hits", () => {
    expect(registerLeaks("Sie, Sie und nochmals Sie.", "de", "ihr")).toEqual([
      "Sie",
    ]);
  });

  it("skips a sentence-initial Ihr — exactly what an ihr letter says", () => {
    expect(
      registerLeaks(
        "Ihr sucht jemanden, der anpackt. Ich freue mich auf euch.",
        "de",
        "ihr",
      ),
    ).toEqual([]);
    // …and at the start of a paragraph, after the salutation's line break.
    expect(
      registerLeaks("Hallo zusammen,\n\nIhr sucht jemanden.", "de", "ihr"),
    ).toEqual([]);
  });

  it("lists leaks in order of appearance", () => {
    expect(
      registerLeaks(
        "Ich schreibe dir, weil ich Sie treffen will.",
        "de",
        "ihr",
      ),
    ).toEqual(["dir", "Sie"]);
  });

  it("does not read EUR as the plural you", () => {
    expect(registerLeaks("Gehalt 60.000 EUR, gerne.", "de", "sie")).toEqual([]);
  });

  it("takes regional German codes", () => {
    expect(registerLeaks("Ich schreibe euch.", "de-AT", "sie")).toEqual([
      "euch",
    ]);
  });
});

describe("registerLeaks — word boundaries", () => {
  it("splits on umlauts the way Python's \\b does", () => {
    // JS \b is ASCII-only: "ü" would count as a boundary and "Südu…" style words
    // would leak a "du". Python's \b treats umlauts as letters.
    expect(
      registerLeaks("Die Düne und das Büdu-Projekt.", "de", "sie"),
    ).toEqual([]);
    expect(registerLeaks("Grüße für dich.", "de", "sie")).toEqual(["dich"]);
  });

  it("never matches inside a longer word", () => {
    expect(
      registerLeaks("Dirigent, Dusche, Dichtung, Siegel, Ihnenx.", "de", "ihr"),
    ).toEqual([]);
  });
});

describe("ADDRESS_FORMS", () => {
  it("is the server's set, in its order", () => {
    // register.py ADDRESS_FORMS — the views 400 on anything else.
    expect(ADDRESS_FORMS).toEqual(["sie", "ihr", "du"]);
  });
});
