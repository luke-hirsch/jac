import { describe, expect, it } from "vitest";
import {
  decodeCreationOptions,
  decodeRequestOptions,
  encodeAssertion,
  encodeAttestation,
} from "@/lib/webauthn";

/**
 * The wire format is base64url (url-safe, unpadded); the WebAuthn APIs want
 * ArrayBuffers. Node's `Buffer.from(..., "base64url")` is used as an independent
 * oracle for the module's hand-rolled atob/btoa codec.
 */
const bytes = (b: number[]) => new Uint8Array(b);
const b64url = (b: number[]) => Buffer.from(b).toString("base64url");

describe("decodeCreationOptions", () => {
  it("decodes challenge, user.id and excludeCredentials into ArrayBuffers", () => {
    const opts = decodeCreationOptions({
      publicKey: {
        challenge: b64url([1, 2, 3, 4]),
        user: { id: b64url([9, 8, 7]), name: "a", displayName: "A" },
        excludeCredentials: [{ id: b64url([5, 6]), type: "public-key" }],
        rp: { name: "rp" },
        pubKeyCredParams: [],
      },
    } as never);

    const pk = opts.publicKey!;
    expect(new Uint8Array(pk.challenge as ArrayBuffer)).toEqual(bytes([1, 2, 3, 4]));
    expect(new Uint8Array(pk.user.id as ArrayBuffer)).toEqual(bytes([9, 8, 7]));
    expect(new Uint8Array(pk.excludeCredentials![0].id as ArrayBuffer)).toEqual(bytes([5, 6]));
  });

  it("tolerates a missing excludeCredentials list", () => {
    const opts = decodeCreationOptions({
      publicKey: {
        challenge: b64url([0]),
        user: { id: b64url([0]), name: "a", displayName: "A" },
        rp: { name: "rp" },
        pubKeyCredParams: [],
      },
    } as never);
    expect(opts.publicKey!.excludeCredentials).toEqual([]);
  });
});

describe("decodeRequestOptions", () => {
  it("decodes challenge and allowCredentials, including high bytes", () => {
    const opts = decodeRequestOptions({
      publicKey: {
        challenge: b64url([255, 0, 128]),
        allowCredentials: [{ id: b64url([1]), type: "public-key" }],
      },
    } as never);
    expect(new Uint8Array(opts.publicKey!.challenge as ArrayBuffer)).toEqual(bytes([255, 0, 128]));
    expect(new Uint8Array(opts.publicKey!.allowCredentials![0].id as ArrayBuffer)).toEqual(
      bytes([1]),
    );
  });
});

describe("encodeAttestation / encodeAssertion", () => {
  it("base64url-encodes the attestation buffers (url-safe, unpadded)", () => {
    const cred = {
      id: "cred-id",
      type: "public-key",
      rawId: bytes([1, 2, 3, 4]).buffer,
      response: {
        attestationObject: bytes([251, 255, 191]).buffer,
        clientDataJSON: bytes([10, 20]).buffer,
      },
    } as unknown as PublicKeyCredential;

    expect(encodeAttestation(cred)).toEqual({
      id: "cred-id",
      type: "public-key",
      rawId: b64url([1, 2, 3, 4]),
      response: {
        attestationObject: b64url([251, 255, 191]),
        clientDataJSON: b64url([10, 20]),
      },
    });
    // bytes that would map to + and / in standard base64 must come out url-safe
    expect(b64url([251, 255, 191])).toMatch(/^[A-Za-z0-9_-]+$/);
  });

  it("encodes an assertion and nulls a missing userHandle", () => {
    const base = { id: "c", type: "public-key", rawId: bytes([1]).buffer };
    const response = {
      authenticatorData: bytes([1]).buffer,
      clientDataJSON: bytes([2]).buffer,
      signature: bytes([3]).buffer,
    };

    const withHandle = encodeAssertion({
      ...base,
      response: { ...response, userHandle: bytes([4]).buffer },
    } as unknown as PublicKeyCredential);
    expect(withHandle.response.userHandle).toBe(b64url([4]));

    const noHandle = encodeAssertion({
      ...base,
      response: { ...response, userHandle: null },
    } as unknown as PublicKeyCredential);
    expect(noHandle.response.userHandle).toBeNull();
  });
});
