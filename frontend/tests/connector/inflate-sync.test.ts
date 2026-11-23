import { zlibSync } from "fflate";
import { describe, expect, it } from "vitest";
import { unzlib } from "@/connector/inflate-sync";

/** png-js's inflate in the connector build (src/connector/inflate-sync.ts): fflate's async
 *  callback contract, without the worker Claude's sandbox refuses. */

const inflate = (data: Uint8Array, withOpts = false) =>
  new Promise<{ err: Error | null; out: Uint8Array | null }>((resolve) => {
    const cb = (err: Error | null, out: Uint8Array | null) =>
      resolve({ err, out });
    if (withOpts) unzlib(data, {}, cb);
    else unzlib(data, cb);
  });

describe("inflating a PNG's data on this thread", () => {
  const raw = new TextEncoder().encode("IDAT ".repeat(500));

  it("answers through the callback, as fflate does", async () => {
    const { err, out } = await inflate(zlibSync(raw));
    expect(err).toBeNull();
    expect(out).toEqual(raw);
  });

  it("takes the options argument fflate allows before the callback", async () => {
    expect((await inflate(zlibSync(raw), true)).out).toEqual(raw);
  });

  it("hands a broken stream's error to the callback instead of throwing", async () => {
    const { err, out } = await inflate(new Uint8Array([1, 2, 3, 4]));
    expect(err).toBeInstanceOf(Error);
    expect(out).toBeNull();
  });

  it("answers after the call returns, never inside it", () => {
    let called = false;
    unzlib(zlibSync(raw), () => {
      called = true;
    });
    expect(called).toBe(false);
  });
});
