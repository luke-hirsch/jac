import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useForm } from "@tanstack/react-form";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, allauthErrorsByField } from "@/lib/api";
import { useInvalidateSession } from "@/lib/auth";
import { zodValidator, z } from "@/lib/form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError } from "@/components/field-error";

const schema = z.object({ key: z.string().min(4) });

export const Route = createFileRoute("/auth/verify-email")({
  component: VerifyEmail,
});

function VerifyEmail() {
  const navigate = useNavigate();
  const invalidate = useInvalidateSession();

  const verify = useMutation({
    mutationFn: (body: { key: string }) =>
      api("/_allauth/browser/v1/auth/email/verify", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: async () => {
      await invalidate();
      toast.success("Email verified");
      navigate({ to: "/cv" });
    },
    onError: (e) => {
      const fields = allauthErrorsByField(e);
      toast.error(fields.key ?? fields.__non_field__ ?? "Code rejected");
    },
  });

  const form = useForm({
    defaultValues: { key: "" },
    validators: { onChange: zodValidator(schema) },
    onSubmit: ({ value }) => verify.mutateAsync(value),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Verify your email</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground mb-4">
          We sent a code to your inbox. Paste it below.
        </p>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            form.handleSubmit();
          }}
        >
          <form.Field name="key">
            {(field) => (
              <div className="space-y-1">
                <Label htmlFor={field.name}>Verification code</Label>
                <Input
                  id={field.name}
                  autoComplete="one-time-code"
                  value={field.state.value}
                  onChange={(e) =>
                    field.handleChange(e.target.value.trim().toUpperCase())
                  }
                />
                <FieldError msg={field.state.meta.errors?.[0]} />
              </div>
            )}
          </form.Field>
          <Button type="submit" className="w-full" disabled={verify.isPending}>
            {verify.isPending ? "Verifying…" : "Verify"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
