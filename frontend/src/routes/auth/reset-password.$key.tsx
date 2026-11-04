import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useForm } from "@tanstack/react-form";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, allauthErrorsByField, ApiError } from "@/lib/api";
import { useInvalidateSession } from "@/lib/auth";
import { zodValidator, z } from "@/lib/form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const schema = z
  .object({ password: z.string().min(8), confirm: z.string() })
  .refine((v) => v.password === v.confirm, {
    path: ["confirm"],
    message: "Passwords don't match",
  });

export const Route = createFileRoute("/auth/reset-password/$key")({
  component: ResetPassword,
});

function ResetPassword() {
  const { key } = Route.useParams();
  const navigate = useNavigate();
  const invalidate = useInvalidateSession();

  const reset = useMutation({
    mutationFn: async (body: { password: string }) => {
      try {
        return await api("/_allauth/browser/v1/auth/password/reset", {
          method: "POST",
          body: JSON.stringify({ key, password: body.password }),
        });
      } catch (e) {
        // allauth returns 401 with the next-auth-flow on successful reset when
        // ACCOUNT_LOGIN_ON_PASSWORD_RESET is False (our default) — "password
        // changed, now go log in." Treat it as success.
        if (e instanceof ApiError && e.status === 401) return e.data;
        throw e;
      }
    },
    onSuccess: async () => {
      await invalidate();
      toast.success("Password reset");
      navigate({ to: "/auth/login", search: {} as never });
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 429) {
        toast.error("Too many attempts. Try again later.");
        return;
      }
      const fields = allauthErrorsByField(e);
      toast.error(
        fields.key ?? fields.password ?? fields.__non_field__ ?? "Reset failed",
      );
    },
  });

  const form = useForm({
    defaultValues: { password: "", confirm: "" },
    validators: { onChange: zodValidator(schema) },
    onSubmit: ({ value }) => reset.mutateAsync({ password: value.password }),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Choose a new password</CardTitle>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            form.handleSubmit();
          }}
        >
          <form.Field name="password">
            {(field) => (
              <div className="space-y-1">
                <Label htmlFor={field.name}>New password</Label>
                <Input
                  id={field.name}
                  type="password"
                  autoComplete="new-password"
                  value={field.state.value}
                  onChange={(e) => field.handleChange(e.target.value)}
                />
              </div>
            )}
          </form.Field>
          <form.Field name="confirm">
            {(field) => (
              <div className="space-y-1">
                <Label htmlFor={field.name}>Confirm</Label>
                <Input
                  id={field.name}
                  type="password"
                  autoComplete="new-password"
                  value={field.state.value}
                  onChange={(e) => field.handleChange(e.target.value)}
                />
              </div>
            )}
          </form.Field>
          <Button type="submit" className="w-full" disabled={reset.isPending}>
            Set password
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
