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
import { FieldError } from "@/components/field-error";
const schema = z
  .object({
    email: z.string().email(),
    password: z.string().min(8, "At least 8 characters"),
    confirm: z.string(),
  })
  .refine((v) => v.password === v.confirm, {
    path: ["confirm"],
    message: "Passwords don't match",
  });

export const Route = createFileRoute("/auth/signup")({ component: Signup });

function Signup() {
  const navigate = useNavigate();
  const invalidate = useInvalidateSession();

  async function handleOutcome(input: unknown) {
    await invalidate();
    const outcome = resolveAuthOutcome(input);
    switch (outcome.kind) {
      case "authenticated":
      case "already_authenticated":
        navigate({ to: "/cv", search: {} as never });
        return;
      case "verify_email":
        navigate({ to: "/auth/verify-email" });
        return;
      case "mfa_authenticate":
        // Doesn't happen on signup — the user has no authenticator yet — but
        // fall back gracefully if the contract ever changes.
        navigate({ to: "/auth/mfa-challenge", search: {} as never });
        return;
      case "error":
        toast.error(outcome.fields.__non_field__ ?? "Signup failed");
        return;
    }
  }

  const signup = useMutation({
    mutationFn: (body: { email: string; password: string }) =>
      api("/_allauth/browser/v1/auth/signup", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: handleOutcome,
    onError: handleOutcome,
  });

  const form = useForm({
    defaultValues: { email: "", password: "", confirm: "" },
    validators: { onChange: zodValidator(schema) },
    onSubmit: async ({ value }) =>
      signup.mutateAsync({ email: value.email, password: value.password }),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Create account</CardTitle>
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
                <FieldError msg={field.state.meta.errors?.[0]} />
              </div>
            )}
          </form.Field>
          <form.Field name="password">
            {(field) => (
              <div className="space-y-1">
                <Label htmlFor={field.name}>Password</Label>
                <Input
                  id={field.name}
                  type="password"
                  autoComplete="new-password"
                  value={field.state.value}
                  onChange={(e) => field.handleChange(e.target.value)}
                />
                <FieldError msg={field.state.meta.errors?.[0]} />
              </div>
            )}
          </form.Field>
          <form.Field name="confirm">
            {(field) => (
              <div className="space-y-1">
                <Label htmlFor={field.name}>Confirm password</Label>
                <Input
                  id={field.name}
                  type="password"
                  autoComplete="new-password"
                  value={field.state.value}
                  onChange={(e) => field.handleChange(e.target.value)}
                />
                <FieldError msg={field.state.meta.errors?.[0]} />
              </div>
            )}
          </form.Field>
          <Button type="submit" className="w-full" disabled={signup.isPending}>
            {signup.isPending ? "Creating…" : "Create account"}
          </Button>
        </form>
        <p className="mt-4 text-sm text-muted-foreground text-center">
          Have an account?{" "}
          <Link to="/auth/login" className="underline">
            Sign in
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}
