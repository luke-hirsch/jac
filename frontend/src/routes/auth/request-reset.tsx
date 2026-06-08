import { createFileRoute, Link } from "@tanstack/react-router";
import { useForm } from "@tanstack/react-form";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { zodValidator, z } from "@/lib/form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const schema = z.object({ email: z.string().email() });

export const Route = createFileRoute("/auth/request-reset")({
  component: RequestReset,
});

function RequestReset() {
  const reset = useMutation({
    mutationFn: (body: { email: string }) =>
      api("/_allauth/browser/v1/auth/password/request", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => toast.success("Check your inbox for a reset link."),
    onError: (e) => {
      // 429 surfaces a real signal — if we said "check inbox" the user would
      // wait for an email that won't arrive. Other errors keep the generic
      // success message to avoid leaking which addresses are registered.
      if (e instanceof ApiError && e.status === 429) {
        toast.error("Too many requests. Try again later.");
        return;
      }
      toast.success("Check your inbox for a reset link.");
    },
  });

  const form = useForm({
    defaultValues: { email: "" },
    validators: { onChange: zodValidator(schema) },
    onSubmit: ({ value }) => reset.mutateAsync(value),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Reset password</CardTitle>
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
          <Button type="submit" className="w-full" disabled={reset.isPending}>
            Send reset link
          </Button>
        </form>
        <p className="mt-4 text-sm text-muted-foreground text-center">
          <Link to="/auth/login" className="underline">
            Back to sign in
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}
