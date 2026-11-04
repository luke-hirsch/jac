import { useForm } from "@tanstack/react-form";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, allauthErrorsByField } from "@/lib/api";
import { zodValidator, z } from "@/lib/form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const schema = z
  .object({
    current_password: z.string().min(1),
    new_password: z.string().min(8),
    confirm: z.string(),
  })
  .refine((v) => v.new_password === v.confirm, {
    path: ["confirm"],
    message: "Passwords don't match",
  });

export function ChangePassword() {
  const change = useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      api("/_allauth/browser/v1/account/password/change", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => toast.success("Password changed"),
    onError: (e) => {
      const fields = allauthErrorsByField(e);
      toast.error(
        fields.current_password ?? fields.new_password ?? "Change failed",
      );
    },
  });

  const form = useForm({
    defaultValues: { current_password: "", new_password: "", confirm: "" },
    validators: { onChange: zodValidator(schema) },
    onSubmit: ({ value, formApi }) =>
      change
        .mutateAsync({
          current_password: value.current_password,
          new_password: value.new_password,
        })
        .then(() => formApi.reset()),
  });

  return (
    <form
      className="space-y-4 max-w-sm"
      onSubmit={(e) => {
        e.preventDefault();
        form.handleSubmit();
      }}
    >
      <form.Field name="current_password">
        {(field) => (
          <div className="space-y-1">
            <Label htmlFor={field.name}>Current password</Label>
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
      <form.Field name="new_password">
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
      <Button type="submit" disabled={change.isPending}>
        Change password
      </Button>
    </form>
  );
}
