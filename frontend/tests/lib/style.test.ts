import { describe, it, expect } from "vitest";
import {
  CHECKS,
  NO_ANTITHESIS,
  NO_DASHES,
  antithesisFragments,
  dashFragments,
  sentences,
  styleChecks,
  styleWarnings,
} from "@/lib/style";

/**
 * The editor's copy of `jac/style.py`. The cases mirror `DashCheckTests`,
 * `AntithesisCheckTests` and `StyleWarningTests` in `backend/jac/tests/test_pipeline.py`:
 * keep the two in step.
 */

describe("dashFragments (mirrors DashCheckTests)", () => {
  it("flags every em dash", () => {
    for (const text of [
      "Ich bringe drei Jahre Erfahrung mit — und das mit Freude.",
      "I bring three years of experience—and curiosity.",
    ])
      expect(dashFragments(text)).toHaveLength(1);
  });

  it("flags a spaced en dash between clauses", () => {
    expect(dashFragments("Ich bringe Erfahrung mit – und Neugier.")).toEqual([
      "…bringe Erfahrung mit – und Neugier.",
    ]);
    expect(dashFragments("I build backends – and I enjoy it.")).not.toEqual([]);
  });

  it("flags a spaced hyphen between clauses", () => {
    expect(dashFragments("Ich mag Python - und Rust.")).not.toEqual([]);
    expect(dashFragments("I bring experience - and curiosity.")).not.toEqual(
      [],
    );
  });

  it("leaves ranges alone", () => {
    for (const text of [
      "Von 2020–2023 war ich dort.",
      "Siehe S. 4–6.",
      "From 2020 – 2023 and 10 - 12 every day.",
    ])
      expect(dashFragments(text), text).toEqual([]);
  });

  it("leaves compounds alone", () => {
    for (const text of [
      "Ich habe eine Django-App gebaut.",
      "Fünf Jahre im E-Commerce.",
      "Front- und Backend aus einer Hand.",
      "A well-tested, open-source tool.",
    ])
      expect(dashFragments(text), text).toEqual([]);
  });

  it("leaves bullets and minus signs alone", () => {
    expect(dashFragments("- Python\n– Rust\nUmsatz um –20 % gesenkt.")).toEqual(
      [],
    );
  });

  it("quotes the dash in its words", () => {
    const [quote] = dashFragments(
      "Ich bringe drei Jahre Erfahrung mit — und das mit Freude.",
    );
    expect(quote).toContain("Erfahrung mit — und das");
  });

  it("sees a no-break space as space", () => {
    expect(dashFragments("Erfahrung – und Neugier.")).toEqual([
      "Erfahrung – und Neugier.",
    ]);
  });
});

describe("antithesisFragments (mirrors AntithesisCheckTests)", () => {
  it("flags German antitheses", () => {
    for (const [text, quote] of [
      [
        "Ich schreibe nicht nur Code, sondern auch Dokumentation.",
        "nicht nur Code, sondern auch",
      ],
      [
        "Das ist keine Aufgabe, sondern eine Berufung.",
        "keine Aufgabe, sondern",
      ],
      ["Ich suche keinen Job, sondern eine Mission.", "keinen Job, sondern"],
      [
        "Es geht nicht um Tempo, sondern um Qualität.",
        "nicht um Tempo, sondern",
      ],
      [
        "NICHT NUR schnell, SONDERN AUCH sauber.",
        "NICHT NUR schnell, SONDERN AUCH",
      ],
    ]) {
      const found = antithesisFragments(text);
      expect(found, text).toHaveLength(1);
      expect(found[0]).toContain(quote);
    }
  });

  it("flags English antitheses", () => {
    for (const [text, quote] of [
      [
        "I do not just write code, I ship it, but I also run it.",
        "not just write",
      ],
      [
        "Not only did I build it, but I also ran it.",
        "Not only did I build it, but",
      ],
      ["This isn't a job for me, it's a calling.", "isn't a job for me, it's"],
      ["This isn’t a job, it’s a calling.", "isn’t a job, it’s"],
      ["It is not a job but a mission.", "not a job but a"],
      ["It's not about code; it's about people.", "It's not about code; it's"],
      ["That’s not a job – it’s a mission.", "That’s not a job – it’s"],
      ["This is not a job, it's a mission.", "is not a job, it's"],
    ]) {
      const found = antithesisFragments(text);
      expect(found, text).toHaveLength(1);
      expect(found[0]).toContain(quote);
    }
  });

  it("leaves sondern without a negation alone", () => {
    for (const text of [
      "Sondern auch das Team profitiert davon.",
      "Besonders reizt mich die Aufgabe.",
      "Wir wollen uns nicht absondern.",
    ])
      expect(antithesisFragments(text), text).toEqual([]);
  });

  it("leaves isn't and it's without a clause break alone", () => {
    for (const text of [
      "It isn't hard to see why it's a fit.",
      "It isn't easy, but it's worth it.",
      "Let's not wait, it's late.",
    ])
      expect(antithesisFragments(text), text).toEqual([]);
  });

  it("doesn't count a negation in another sentence", () => {
    expect(
      antithesisFragments(
        "Ich habe die Daten nicht gelöscht. Sondern archiviert.",
      ),
    ).toEqual([]);
    expect(
      antithesisFragments("I am not a fan of meetings. But I run good ones."),
    ).toEqual([]);
  });

  it("doesn't end a sentence at an abbreviation", () => {
    for (const text of [
      "Ich habe nicht z. B. Java gelernt, sondern Python.",
      "Ich habe nicht z.B. Java gelernt, sondern Python.",
      "Ich habe kein Java bzw. C# genutzt, sondern Python.",
      "Ich fange nicht am 1. März an, sondern im April.",
      "Ich habe nicht mit Dr. Meier gearbeitet, sondern allein.",
      "This isn't e.g. a job, it's a calling.",
    ])
      expect(antithesisFragments(text), text).toHaveLength(1);
  });

  it("still ends a sentence at a year", () => {
    expect(
      sentences("Ich bin seit 2023 dabei. Davor war ich z. B. in Berlin."),
    ).toEqual(["Ich bin seit 2023 dabei.", "Davor war ich z. B. in Berlin."]);
    expect(sentences("Das war 2023. Nicht lange.")).toEqual([
      "Das war 2023.",
      "Nicht lange.",
    ]);
  });

  it("quotes once per sentence", () => {
    expect(
      antithesisFragments(
        "Nicht nur Code, sondern auch Teams. Keine Phrase, sondern Substanz.",
      ),
    ).toHaveLength(2);
  });
});

describe("styleWarnings (mirrors StyleWarningTests)", () => {
  it("names the part, the rule and the fragment", () => {
    const [warning] = styleWarnings(
      { body: "Erfahrung — und Neugier." },
      CHECKS,
    );
    expect(warning).toBe(
      'The body breaks the no_dashes rule (no em or en dashes as punctuation): "Erfahrung — und Neugier."',
    );
  });

  it("checks the frame too", () => {
    const warnings = styleWarnings(
      { body: "Clean.", salutation: "Hallo – Team," },
      CHECKS,
    );
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain("The salutation");
  });

  it("keeps a check that is off quiet", () => {
    const body = "Erfahrung — und nicht nur Code, sondern auch Teams.";
    const warnings = styleWarnings({ body }, [NO_ANTITHESIS]);
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain(NO_ANTITHESIS);
    expect(styleWarnings({ body }, [])).toEqual([]);
  });
});

describe("styleChecks", () => {
  it("follows the profile's toggles", () => {
    expect(
      styleChecks({ style_no_dashes: true, style_no_antithesis: true }),
    ).toEqual([NO_DASHES, NO_ANTITHESIS]);
    expect(
      styleChecks({ style_no_dashes: false, style_no_antithesis: true }),
    ).toEqual([NO_ANTITHESIS]);
  });

  it("checks nothing until the profile has loaded", () => {
    expect(styleChecks(undefined)).toEqual([]);
  });
});
