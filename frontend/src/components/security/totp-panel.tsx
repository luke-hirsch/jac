import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm } from "@tanstack/react-form";
import { QRCodeSVG } from "qrcode.react";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { withReauth } from "@/lib/reauth";
import { zodValidator, z } from "@/lib/form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type TotpMeta = { secret: string; totp_url: string };
const TOTP_KEY = ["mfa", "totp"] as const;
const RECOVERY_KEY = ["mfa", "recovery"] as const;

export function TotpPanel() {
  const qc = useQueryClient();

  const totp = useQuery({
    queryKey: TOTP_KEY,
    queryFn: async () => {
      try {
        await api("/_allauth/browser/v1/account/authenticators/totp");
        return { activated: true, meta: null as TotpMeta | null };
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          const meta = (e.data as { meta?: TotpMeta }).meta!;
          return { activated: false, meta };
        }
        throw e;
      }
    },
  });

  const activate = useMutation({
    mutationFn: (body: { code: string }) =>
      withReauth(() =>
        api("/_allauth/browser/v1/account/authenticators/totp", {
          method: "POST",
          body: JSON.stringify(body),
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: TOTP_KEY });
      toast.success("Authenticator enrolled");
    },
    onError: () => toast.error("Code rejected"),
  });

  const deactivate = useMutation({
    mutationFn: () =>
      withReauth(() =>
        api("/_allauth/browser/v1/account/authenticators/totp", {
          method: "DELETE",
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: TOTP_KEY });
      qc.invalidateQueries({ queryKey: RECOVERY_KEY });
      toast.success("Authenticator removed");
    },
  });

  const form = useForm({
    defaultValues: { code: "" },
    validators: {
      onChange: zodValidator(z.object({ code: z.string().min(6).max(8) })),
    },
    onSubmit: ({ value, formApi }) =>
      activate.mutateAsync(value).then(() => formApi.reset()),
  });

  if (totp.isLoading)
    return <p className="text-sm text-muted-foreground">loading…</p>;

  if (totp.data?.activated) {
    return (
      <div className="space-y-3">
        <p className="text-sm">Authenticator app is active.</p>
        <Button
          variant="destructive"
          size="sm"
          onClick={() => deactivate.mutate()}
        >
          Remove authenticator
        </Button>
        <RecoveryCodesPanel />
      </div>
    );
  }

  const meta = totp.data?.meta;
  if (!meta) return null;
  return (
    <div className="space-y-4">
      <p className="text-sm">
        Scan this QR with your authenticator app, then enter a 6-digit code:
      </p>
      <div className="rounded border inline-block p-3 bg-white">
        <QRCodeSVG value={meta.totp_url} size={160} />
      </div>
      <p className="text-xs text-muted-foreground break-all">
        or paste this secret manually:{" "}
        <code className="font-mono">{meta.secret}</code>
      </p>
      <form
        className="flex gap-2 max-w-xs"
        onSubmit={(e) => {
          e.preventDefault();
          form.handleSubmit();
        }}
      >
        <form.Field name="code">
          {(field) => (
            <div className="flex-1 space-y-1">
              <Label htmlFor={field.name}>Code</Label>
              <Input
                id={field.name}
                inputMode="numeric"
                autoComplete="one-time-code"
                value={field.state.value}
                onChange={(e) => field.handleChange(e.target.value.trim())}
              />
            </div>
          )}
        </form.Field>
        <Button
          type="submit"
          className="self-end"
          disabled={activate.isPending}
        >
          Activate
        </Button>
      </form>
    </div>
  );
}

function RecoveryCodesPanel() {
  const qc = useQueryClient();
  const codes = useQuery({
    queryKey: RECOVERY_KEY,
    queryFn: () =>
      api<{ data: { unused_codes: string[] } }>(
        "/_allauth/browser/v1/account/authenticators/recovery-codes",
      ).then((r) => r.data),
  });

  const regen = useMutation({
    mutationFn: () =>
      withReauth(() =>
        api("/_allauth/browser/v1/account/authenticators/recovery-codes", {
          method: "POST",
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: RECOVERY_KEY });
      toast.success("Recovery codes regenerated");
    },
  });

  if (!codes.data) return null;

  return (
    <div className="mt-4 space-y-2">
      <h3 className="text-sm font-medium">Recovery codes</h3>
      <p className="text-xs text-muted-foreground">
        Store these somewhere safe. Each is single-use.
      </p>
      <ul className="grid grid-cols-2 gap-1 font-mono text-sm bg-muted/40 p-3 rounded">
        {codes.data.unused_codes.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>
      <Button variant="outline" size="sm" onClick={() => regen.mutate()}>
        Regenerate
      </Button>
    </div>
  );
}
