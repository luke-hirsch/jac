import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useInvalidateSession } from "@/lib/auth";
import {
  decodeRequestOptions,
  encodeAssertion,
  type EncodedRequestOptions,
} from "@/lib/webauthn";
import { Button } from "@/components/ui/button";

export function PasskeyLoginButton() {
  const navigate = useNavigate();
  const invalidate = useInvalidateSession();

  const login = useMutation({
    mutationFn: async () => {
      const opts = await api<{ data: { request_options: EncodedRequestOptions } }>(
        "/_allauth/browser/v1/auth/webauthn/login",
        { method: "GET" },
      );
      const cred = (await navigator.credentials.get(
        decodeRequestOptions(opts.data.request_options),
      )) as PublicKeyCredential | null;
      if (!cred) throw new Error("No credential selected");
      return api<{ meta?: { is_authenticated?: boolean } }>(
        "/_allauth/browser/v1/auth/webauthn/login",
        {
          method: "POST",
          body: JSON.stringify({ credential: encodeAssertion(cred) }),
        },
      );
    },
    onSuccess: async (resp) => {
      await invalidate();
      if (resp.meta?.is_authenticated)
        navigate({ to: "/", search: {} as never });
    },
    onError: (e) => toast.error(`Passkey login failed: ${e.message}`),
  });

  return (
    <Button
      variant="outline"
      className="w-full mt-3"
      onClick={() => login.mutate()}
    >
      Sign in with passkey
    </Button>
  );
}
