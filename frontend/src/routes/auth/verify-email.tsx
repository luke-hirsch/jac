import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useForm } from "@tanstack/react-form";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { signOut, useInvalidateSession } from "@/lib/auth";
import {
  forgetPendingEmail,
  pendingEmail,
  resendErrorMessage,
  verifyErrorMessage,
} from "@/lib/auth-flow";
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
  const email = pendingEmail();

  const verify = useMutation({
    mutationFn: (body: { key: string }) =>
      api("/_allauth/browser/v1/auth/email/verify", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: async () => {
      forgetPendingEmail();
      await invalidate();
      toast.success("Email verified");
      // A brand-new account: the page that offers the sample career.
      navigate({ to: "/applications" });
    },
    onError: (e) => toast.error(verifyErrorMessage(e)),
  });

  const resend = useMutation({
    mutationFn: () =>
      api("/_allauth/browser/v1/auth/email/verify/resend", { method: "POST" }),
    onSuccess: () =>
      toast.success(
        email ? `New code sent to ${email}` : "New code sent to your inbox",
      ),
    onError: (e) => toast.error(resendErrorMessage(e)),
  });

  const startOver = useMutation({
    mutationFn: signOut,
    onSuccess: async () => {
      forgetPendingEmail();
      await invalidate();
      navigate({ to: "/auth/signup" });
    },
    onError: () => toast.error("Could not start over. Try again."),
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
          We sent a code to{" "}
          {email ? (
            <strong className="text-foreground break-all">{email}</strong>
          ) : (
            "your inbox"
          )}
          . Paste it below. It can take a minute, and it may land in spam.
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
        <div className="mt-4 flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            className="flex-1"
            onClick={() => resend.mutate()}
            disabled={resend.isPending}
          >
            {resend.isPending ? "Sending…" : "Resend code"}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="flex-1"
            onClick={() => startOver.mutate()}
            disabled={startOver.isPending}
          >
            Start over
          </Button>
        </div>
        <p className="mt-3 text-xs text-muted-foreground">
          Mistyped the address? Start over and sign up again. Used this address
          before? Start over and sign in instead: that mails a fresh code.
        </p>
      </CardContent>
    </Card>
  );
}
