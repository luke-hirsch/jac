import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useForm } from "@tanstack/react-form";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, allauthErrorsByField } from "@/lib/api";
import { useAuth, useInvalidateSession } from "@/lib/auth";
import { zodValidator, z } from "@/lib/form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type Search = { redirect?: string; next?: string };

const schema = z.object({ code: z.string().min(4) });

export const Route = createFileRoute("/auth/mfa-challenge")({
  validateSearch: (s: Record<string, unknown>): Search => ({
    redirect: typeof s.redirect === "string" ? s.redirect : undefined,
    next: typeof s.next === "string" ? s.next : undefined,
  }),
  component: MfaChallenge,
});

function MfaChallenge() {
  const { redirect, next } = Route.useSearch();
  const navigate = useNavigate();
  const invalidate = useInvalidateSession();
  const { status } = useAuth();

  // Two entrypoints:
  // - login-flow MFA (pending mfa_authenticate stage) → /auth/2fa/authenticate
  // - already-authenticated step-up (admin gate redirect, no pending stage)
  //   → /auth/2fa/reauthenticate, which fires authenticator_used and lets
  //   spa.signals flip session["mfa_authenticated"] for AdminRequireMfaMiddleware.
  const endpoint =
    status === "authenticated"
      ? "/_allauth/browser/v1/auth/2fa/reauthenticate"
      : "/_allauth/browser/v1/auth/2fa/authenticate";

  const verify = useMutation({
    mutationFn: (body: { code: string }) =>
      api(endpoint, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: async () => {
      await invalidate();
      toast.success("Verified");
      // `next` comes from AdminRequireMfaMiddleware (e.g. /admin/), takes precedence.
      if (next) window.location.href = next;
      else navigate({ to: redirect ?? "/", search: {} as never });
    },
    onError: (e) => {
      const fields = allauthErrorsByField(e);
      toast.error(fields.code ?? fields.__non_field__ ?? "Invalid code");
    },
  });

  const form = useForm({
    defaultValues: { code: "" },
    validators: { onChange: zodValidator(schema) },
    onSubmit: ({ value }) => verify.mutateAsync(value),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Two-factor sign-in</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground mb-4">
          Enter the 6-digit code from your authenticator, or a recovery code.
        </p>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            form.handleSubmit();
          }}
        >
          <form.Field name="code">
            {(field) => (
              <div className="space-y-1">
                <Label htmlFor={field.name}>Code</Label>
                <Input
                  id={field.name}
                  autoComplete="one-time-code"
                  inputMode="text"
                  value={field.state.value}
                  onChange={(e) => field.handleChange(e.target.value.trim())}
                />
              </div>
            )}
          </form.Field>
          <Button type="submit" className="w-full" disabled={verify.isPending}>
            Verify
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
