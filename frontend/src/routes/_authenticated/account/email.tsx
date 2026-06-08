import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm } from "@tanstack/react-form";
import { useState } from "react";
import { toast } from "sonner";
import { api, allauthErrorsByField } from "@/lib/api";
import { zodValidator, z } from "@/lib/form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type EmailRow = { email: string; verified: boolean; primary: boolean };
const EMAIL_KEY = ["account", "emails"] as const;

export const Route = createFileRoute("/_authenticated/account/email")({
  component: EmailPage,
});

type EmailAction = {
  method: "POST" | "PATCH" | "PUT" | "DELETE";
  body: object;
  success: string;
};

function EmailPage() {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: EMAIL_KEY,
    queryFn: () =>
      api<{ data: EmailRow[] }>("/_allauth/browser/v1/account/email").then(
        (r) => r.data,
      ),
  });

  const mutate = useMutation({
    mutationFn: (a: EmailAction) =>
      api("/_allauth/browser/v1/account/email", {
        method: a.method,
        body: JSON.stringify(a.body),
      }),
    onSuccess: (_, a) => {
      qc.invalidateQueries({ queryKey: EMAIL_KEY });
      toast.success(a.success);
    },
    onError: (e) => {
      const fields = allauthErrorsByField(e);
      toast.error(fields.email ?? fields.__non_field__ ?? "Failed");
    },
  });

  const verify = useMutation({
    mutationFn: (key: string) =>
      api("/_allauth/browser/v1/auth/email/verify", {
        method: "POST",
        body: JSON.stringify({ key }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: EMAIL_KEY });
      toast.success("Email verified");
    },
    onError: (e) => {
      const fields = allauthErrorsByField(e);
      toast.error(fields.key ?? fields.__non_field__ ?? "Code rejected");
    },
  });

  const form = useForm({
    defaultValues: { email: "" },
    validators: {
      onChange: zodValidator(z.object({ email: z.string().email() })),
    },
    onSubmit: async ({ value, formApi }) => {
      await mutate.mutateAsync({
        method: "POST",
        body: { email: value.email },
        success: "Verification sent",
      });
      formApi.reset();
    },
  });

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold">Email addresses</h2>
        <p className="text-sm text-muted-foreground">
          Add additional addresses for password reset; promote one to primary.
        </p>
      </div>

      <ul className="space-y-2">
        {list.data?.map((row) => (
          <li key={row.email} className="rounded border p-3 space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <div className="font-mono text-sm">{row.email}</div>
                <div className="text-xs text-muted-foreground">
                  {row.primary ? "primary · " : ""}
                  {row.verified ? "verified" : "unverified"}
                </div>
              </div>
              <div className="flex gap-2">
                {!row.verified && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      mutate.mutate({
                        method: "PATCH",
                        body: { email: row.email },
                        success: "Verification resent",
                      })
                    }
                  >
                    Resend
                  </Button>
                )}
                {!row.primary && row.verified && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      mutate.mutate({
                        method: "PUT",
                        body: { email: row.email, primary: true },
                        success: "Primary updated",
                      })
                    }
                  >
                    Make primary
                  </Button>
                )}
                {!row.primary && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      mutate.mutate({
                        method: "DELETE",
                        body: { email: row.email },
                        success: "Email removed",
                      })
                    }
                  >
                    Remove
                  </Button>
                )}
              </div>
            </div>
            {!row.verified && (
              <VerifyCodeForm
                onSubmit={(key) => verify.mutate(key)}
                pending={verify.isPending}
              />
            )}
          </li>
        ))}
      </ul>

      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          form.handleSubmit();
        }}
      >
        <form.Field name="email">
          {(field) => (
            <div className="flex-1 space-y-1">
              <Label htmlFor={field.name}>Add address</Label>
              <Input
                id={field.name}
                type="email"
                value={field.state.value}
                onChange={(e) => field.handleChange(e.target.value)}
              />
            </div>
          )}
        </form.Field>
        <Button type="submit" className="self-end" disabled={mutate.isPending}>
          Add
        </Button>
      </form>
    </div>
  );
}

function VerifyCodeForm({
  onSubmit,
  pending,
}: {
  onSubmit: (key: string) => void;
  pending: boolean;
}) {
  const [key, setKey] = useState("");
  return (
    <form
      className="flex gap-2 items-end border-t pt-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (key.trim()) onSubmit(key.trim());
      }}
    >
      <div className="flex-1 space-y-1">
        <Label htmlFor="verify-code" className="text-xs">
          Paste verification code
        </Label>
        <Input
          id="verify-code"
          autoComplete="one-time-code"
          value={key}
          onChange={(e) => setKey(e.target.value.trim().toUpperCase())}
        />
      </div>
      <Button type="submit" size="sm" disabled={pending || !key.trim()}>
        {pending ? "Verifying…" : "Verify"}
      </Button>
    </form>
  );
}
