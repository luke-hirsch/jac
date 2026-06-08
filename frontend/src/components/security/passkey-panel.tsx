import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { withReauth } from "@/lib/reauth";
import {
  decodeCreationOptions,
  encodeAttestation,
  type EncodedCreationOptions,
} from "@/lib/webauthn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type Authenticator = {
  type: "totp" | "webauthn" | "recovery_codes";
  id?: number;
  name?: string;
  created_at: number;
};
type Passkey = { id: number; name: string; created_at: number };
const KEY = ["mfa", "webauthn"] as const;

export function PasskeyPanel() {
  const qc = useQueryClient();
  const [newName, setNewName] = useState("");

  // List all authenticators and filter to webauthn. The webauthn-specific
  // endpoint is *register*, not *list* — calling it without a name in the
  // body just burns a session challenge and requires reauth.
  const list = useQuery({
    queryKey: KEY,
    queryFn: () =>
      api<{ data: Authenticator[] }>(
        "/_allauth/browser/v1/account/authenticators",
      ).then(
        (r) => r.data.filter((a): a is Passkey & { type: "webauthn" } =>
          a.type === "webauthn",
        ),
      ),
  });

  const register = useMutation({
    mutationFn: async (name: string) => {
      const opts = await withReauth(() =>
        api<{ data: { creation_options: EncodedCreationOptions } }>(
          "/_allauth/browser/v1/account/authenticators/webauthn",
          { method: "GET" },
        ),
      );
      const cred = (await navigator.credentials.create(
        decodeCreationOptions(opts.data.creation_options),
      )) as PublicKeyCredential | null;
      if (!cred) throw new Error("No credential created");
      return api("/_allauth/browser/v1/account/authenticators/webauthn", {
        method: "POST",
        body: JSON.stringify({ name, credential: encodeAttestation(cred) }),
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
      setNewName("");
      toast.success("Passkey registered");
    },
    onError: (e) => toast.error(`Registration failed: ${e.message}`),
  });

  const remove = useMutation({
    mutationFn: (id: number) =>
      withReauth(() =>
        api("/_allauth/browser/v1/account/authenticators/webauthn", {
          method: "DELETE",
          body: JSON.stringify({ authenticator: id }),
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });

  return (
    <div className="space-y-4">
      <ul className="space-y-2">
        {list.data?.map((pk) => (
          <li
            key={pk.id}
            className="flex items-center justify-between rounded border p-3"
          >
            <div>
              <div className="text-sm font-medium">{pk.name}</div>
              <div className="text-xs text-muted-foreground">
                added {new Date(pk.created_at * 1000).toLocaleDateString()}
              </div>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => remove.mutate(pk.id)}
            >
              Remove
            </Button>
          </li>
        ))}
        {!list.data?.length && (
          <li className="text-sm text-muted-foreground">No passkeys yet.</li>
        )}
      </ul>
      <div className="flex gap-2 max-w-sm">
        <Input
          placeholder="passkey name (e.g. iPhone, Yubikey 5)"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <Button
          onClick={() => register.mutate(newName)}
          disabled={!newName || register.isPending}
        >
          Add passkey
        </Button>
      </div>
    </div>
  );
}
