import { describe, expect, it } from "vitest";
import { z, zodValidator } from "@/lib/form";

/**
 * `zodValidator` adapts a Zod schema into TanStack Form's `({ value }) => …`
 * shape: `undefined` when valid, else `{ fields: { <dotted-path>: message } }`.
 */
describe("zodValidator", () => {
  const schema = z.object({ name: z.string().min(2), age: z.number().min(18) });
  const validate = zodValidator(schema);

  it("returns undefined when the value is valid", () => {
    expect(validate({ value: { name: "ab", age: 21 } })).toBeUndefined();
  });

  it("reports a message per failing field", () => {
    const out = validate({ value: { name: "a", age: 5 } });
    expect(out).toBeDefined();
    expect(Object.keys(out!.fields).sort()).toEqual(["age", "name"]);
    expect(typeof out!.fields.name).toBe("string");
  });

  it("joins nested paths with a dot", () => {
    const nested = zodValidator(z.object({ user: z.object({ name: z.string().min(1) }) }));
    const out = nested({ value: { user: { name: "" } } });
    expect(out!.fields["user.name"]).toBeDefined();
  });
});
