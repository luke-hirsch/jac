import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useForm } from "@tanstack/react-form";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useInvalidateSession } from "@/lib/auth";
import { resolveAuthOutcome } from "@/lib/auth-flow";
import { zodValidator, z } from "@/lib/form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PasskeyLoginButton } from "@/components/passkey-button";

type LoginSearch = { redirect?: string };

const schema = z.object({
  email: z.string().email(),
  password: z.string().min(1, "Required"),
});

export const Route = createFileRoute("/auth/login")({
  validateSearch: (s: Record<string, unknown>): LoginSearch => ({
    redirect: typeof s.redirect === "string" ? s.redirect : undefined,
  }),
  component: Login,
});

function Login() {
  const { redirect } = Route.useSearch();
  const navigate = useNavigate();
  const invalidate = useInvalidateSession();

  async function handleOutcome(input: unknown) {
    await invalidate();
    const outcome = resolveAuthOutcome(input);
    switch (outcome.kind) {
      case "authenticated":
      case "already_authenticated":
        navigate({ to: redirect ?? "/cv", search: {} as never });
        return;
      case "mfa_authenticate":
        navigate({ to: "/auth/mfa-challenge", search: { redirect } });
        return;
      case "verify_email":
        navigate({ to: "/auth/verify-email" });
        return;
      case "error":
        toast.error(
          outcome.fields.__non_field__ ??
            outcome.fields.password ??
            outcome.fields.email ??
            "Login failed",
        );
        return;
    }
  }

  const login = useMutation({
    mutationFn: (body: { email: string; password: string }) =>
      api("/_allauth/browser/v1/auth/login", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: handleOutcome,
    onError: handleOutcome,
  });

  const form = useForm({
    defaultValues: { email: "", password: "" },
    validators: { onChange: zodValidator(schema) },
    onSubmit: ({ value }) => login.mutateAsync(value),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sign in</CardTitle>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            form.handleSubmit();
          }}
        >
          <form.Field name="email">
            {(field) => (
              <div className="space-y-1">
                <Label htmlFor={field.name}>Email</Label>
                <Input
                  id={field.name}
                  type="email"
                  autoComplete="email"
                  value={field.state.value}
                  onChange={(e) => field.handleChange(e.target.value)}
                />
              </div>
            )}
          </form.Field>
          <form.Field name="password">
            {(field) => (
              <div className="space-y-1">
                <div className="flex justify-between">
                  <Label htmlFor={field.name}>Password</Label>
                  <Link
                    to="/auth/request-reset"
                    className="text-sm underline text-muted-foreground"
                  >
                    Forgot?
                  </Link>
                </div>
                <Input
                  id={field.name}
                  type="password"
                  autoComplete="current-password"
                  value={field.state.value}
                  onChange={(e) => field.handleChange(e.target.value)}
                />
              </div>
            )}
          </form.Field>
          <Button type="submit" className="w-full" disabled={login.isPending}>
            {login.isPending ? "Signing in…" : "Sign in"}
          </Button>
        </form>
        <p className="mt-4 text-sm text-muted-foreground text-center">
          No account?{" "}
          <Link to="/auth/signup" className="underline">
            Sign up
          </Link>
        </p>
        <PasskeyLoginButton />
      </CardContent>
    </Card>
  );
}
