function b64urlToBuf(s: string): ArrayBuffer {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const b64 = (s + pad).replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(b64);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return buf.buffer;
}

function bufToB64url(buf: ArrayBuffer | Uint8Array): string {
  const bytes = buf instanceof ArrayBuffer ? new Uint8Array(buf) : buf;
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

type EncodedDescriptor = Omit<PublicKeyCredentialDescriptor, "id"> & {
  id: string;
};

export type EncodedCreationOptions = {
  publicKey: Omit<
    PublicKeyCredentialCreationOptions,
    "challenge" | "user" | "excludeCredentials"
  > & {
    challenge: string;
    user: Omit<PublicKeyCredentialUserEntity, "id"> & { id: string };
    excludeCredentials?: EncodedDescriptor[];
  };
};

export type EncodedRequestOptions = {
  publicKey: Omit<
    PublicKeyCredentialRequestOptions,
    "challenge" | "allowCredentials"
  > & {
    challenge: string;
    allowCredentials?: EncodedDescriptor[];
  };
};

export function decodeCreationOptions(
  opts: EncodedCreationOptions,
): CredentialCreationOptions {
  const pk = opts.publicKey;
  return {
    publicKey: {
      ...pk,
      challenge: b64urlToBuf(pk.challenge),
      user: { ...pk.user, id: b64urlToBuf(pk.user.id) },
      excludeCredentials: (pk.excludeCredentials ?? []).map((c) => ({
        ...c,
        id: b64urlToBuf(c.id),
      })),
    },
  };
}

export function decodeRequestOptions(
  opts: EncodedRequestOptions,
): CredentialRequestOptions {
  const pk = opts.publicKey;
  return {
    publicKey: {
      ...pk,
      challenge: b64urlToBuf(pk.challenge),
      allowCredentials: (pk.allowCredentials ?? []).map((c) => ({
        ...c,
        id: b64urlToBuf(c.id),
      })),
    },
  };
}

export function encodeAttestation(cred: PublicKeyCredential) {
  const r = cred.response as AuthenticatorAttestationResponse;
  return {
    id: cred.id,
    rawId: bufToB64url(cred.rawId),
    type: cred.type,
    response: {
      attestationObject: bufToB64url(r.attestationObject),
      clientDataJSON: bufToB64url(r.clientDataJSON),
    },
  };
}

export function encodeAssertion(cred: PublicKeyCredential) {
  const r = cred.response as AuthenticatorAssertionResponse;
  return {
    id: cred.id,
    rawId: bufToB64url(cred.rawId),
    type: cred.type,
    response: {
      authenticatorData: bufToB64url(r.authenticatorData),
      clientDataJSON: bufToB64url(r.clientDataJSON),
      signature: bufToB64url(r.signature),
      userHandle: r.userHandle ? bufToB64url(r.userHandle) : null,
    },
  };
}
