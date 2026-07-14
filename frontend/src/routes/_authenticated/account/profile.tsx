import { createFileRoute } from "@tanstack/react-router";
import { useForm } from "@tanstack/react-form";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import {
  drfFieldError,
  shouldSend,
  type FieldSaveState,
} from "@/lib/field-save";
import { LineSaveHint } from "@/components/cv/line-save-hint";
import { zodValidator, z } from "@/lib/form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type Profile = {
  id: number;
  username: string;
  first_name: string;
  last_name: string;
  display_name: string;
  bio: string;
  phone: string;
  website: string;
  linkedin_url: string;
  github_url: string;
  street: string;
  address_line2: string;
  zip: string;
  city: string;
  country: string;
  timezone: string;
  theme: "system" | "light" | "dark";
  contrast: "normal" | "high";
  email_reminders: boolean;
  show_socials: boolean;
};

const schema = z.object({
  first_name: z.string().max(150),
  last_name: z.string().max(150),
  display_name: z.string().max(100),
  bio: z.string().max(500),
  phone: z.string().max(30),
  website: z.string().url().or(z.literal("")),
  linkedin_url: z.string().url().or(z.literal("")),
  github_url: z.string().url().or(z.literal("")),
  street: z.string().max(200),
  address_line2: z.string().max(200),
  zip: z.string().max(20),
  city: z.string().max(100),
  country: z.string().max(100),
  timezone: z.string().min(1),
  theme: z.enum(["system", "light", "dark"]),
  contrast: z.enum(["normal", "high"]),
  email_reminders: z.boolean(),
  show_socials: z.boolean(),
});

type ProfileSchema = z.infer<typeof schema>;
type StringKey = {
  [K in keyof ProfileSchema]: ProfileSchema[K] extends string ? K : never;
}[keyof ProfileSchema];

export const Route = createFileRoute("/_authenticated/account/profile")({
  component: ProfilePage,
});

function ProfilePage() {
  const profileQ = useQuery({
    queryKey: ["profile"],
    queryFn: () => api<Profile>("/api/spa/profile/"),
  });

  // No global toasts: single-field saves report through their own inline hint,
  // the whole-form submit toasts for itself.
  const patch = useMutation({
    mutationFn: (body: Partial<Profile>) =>
      api<Profile>("/api/spa/profile/", {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
  });

  if (profileQ.isLoading) return <p>loading…</p>;
  if (!profileQ.data) return <p>could not load profile</p>;

  const p = profileQ.data;

  return (
    <ProfileForm
      username={p.username}
      initial={{
        first_name: p.first_name,
        last_name: p.last_name,
        display_name: p.display_name,
        bio: p.bio,
        phone: p.phone,
        website: p.website,
        linkedin_url: p.linkedin_url,
        github_url: p.github_url,
        street: p.street,
        address_line2: p.address_line2,
        zip: p.zip,
        city: p.city,
        country: p.country,
        timezone: p.timezone,
        theme: p.theme,
        contrast: p.contrast,
        email_reminders: p.email_reminders,
        show_socials: p.show_socials,
      }}
      onSubmit={(v) =>
        patch
          .mutateAsync(v)
          .then(() => toast.success("Saved"))
          .catch(() => toast.error("Save failed"))
      }
      onPatch={(body) => patch.mutateAsync(body)}
      busy={patch.isPending}
    />
  );
}

function ProfileForm({
  username,
  initial,
  onSubmit,
  onPatch,
  busy,
}: {
  username: string;
  initial: ProfileSchema;
  onSubmit: (v: ProfileSchema) => Promise<unknown>;
  onPatch: (body: Partial<ProfileSchema>) => Promise<unknown>;
  busy: boolean;
}) {
  const form = useForm({
    defaultValues: initial,
    validators: { onChange: zodValidator(schema) },
    onSubmit: ({ value }) => onSubmit(value),
  });

  // Line-by-line saving: each field PATCHes as you leave it, with its own
  // saved/error hint; the Save button remains as a save-everything fallback.
  const [fieldStates, setFieldStates] = useState<
    Record<string, FieldSaveState>
  >({});
  const lastSent = useRef<Record<string, unknown>>({ ...initial });
  const save = (
    name: keyof ProfileSchema,
    value: unknown,
    clientErrors: Array<unknown> = [],
  ) => {
    if (!shouldSend(value, lastSent.current[name], clientErrors)) return;
    setFieldStates((s) => ({ ...s, [name]: { state: "saving" } }));
    onPatch({ [name]: value })
      .then(() => {
        lastSent.current[name] = value;
        setFieldStates((s) => ({ ...s, [name]: { state: "saved" } }));
      })
      .catch((e) =>
        setFieldStates((s) => ({
          ...s,
          [name]: { state: "error", message: drfFieldError(e, name) },
        })),
      );
  };

  const text = (name: StringKey, label: string, type = "text") => (
    <form.Field name={name}>
      {(field) => (
        <div className="space-y-1">
          <Label htmlFor={field.name}>{label}</Label>
          <Input
            id={field.name}
            type={type}
            value={field.state.value}
            onChange={(e) => field.handleChange(e.target.value)}
            onBlur={() =>
              save(name, field.state.value, field.state.meta.errors)
            }
          />
          <LineSaveHint s={fieldStates[name]} />
        </div>
      )}
    </form.Field>
  );

  const textarea = (name: StringKey, label: string) => (
    <form.Field name={name}>
      {(field) => (
        <div className="space-y-1">
          <Label htmlFor={field.name}>{label}</Label>
          <Textarea
            id={field.name}
            rows={4}
            value={field.state.value}
            onChange={(e) => field.handleChange(e.target.value)}
            onBlur={() =>
              save(name, field.state.value, field.state.meta.errors)
            }
          />
          <LineSaveHint s={fieldStates[name]} />
        </div>
      )}
    </form.Field>
  );

  return (
    <form
      className="space-y-5"
      onSubmit={(e) => {
        e.preventDefault();
        form.handleSubmit();
      }}
    >
      <div className="space-y-1">
        <Label>Username</Label>
        <Input value={username} disabled readOnly />
      </div>
      <div className="grid grid-cols-2 gap-3">
        {text("first_name", "First name")}
        {text("last_name", "Last name")}
      </div>
      {text("display_name", "Display name")}
      {textarea("bio", "Bio")}
      {text("phone", "Phone")}
      {text("website", "Website", "url")}
      {text("linkedin_url", "LinkedIn", "url")}
      {text("github_url", "GitHub", "url")}
      <div className="space-y-3 pt-2">
        <div>
          <h3 className="text-sm font-medium">Address</h3>
          <p className="text-xs text-muted-foreground">
            Used as the sender block on generated cover letters.
          </p>
        </div>
        {text("street", "Street")}
        {text("address_line2", "Address line 2")}
        <div className="grid grid-cols-[1fr_2fr] gap-3">
          {text("zip", "ZIP / Postcode")}
          {text("city", "City")}
        </div>
        {text("country", "Country")}
      </div>
      {text("timezone", "Timezone")}
      <form.Field name="theme">
        {(field) => (
          <div className="space-y-1">
            <Label>Theme</Label>
            <Select
              value={field.state.value}
              onValueChange={(v) => {
                field.handleChange(v as never);
                save("theme", v);
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="system">Follow system</SelectItem>
                <SelectItem value="light">Light</SelectItem>
                <SelectItem value="dark">Dark</SelectItem>
              </SelectContent>
            </Select>
            <LineSaveHint s={fieldStates.theme} />
          </div>
        )}
      </form.Field>
      <form.Field name="contrast">
        {(field) => (
          <div className="space-y-1">
            <Label>Contrast</Label>
            <Select
              value={field.state.value}
              onValueChange={(v) => {
                field.handleChange(v as never);
                save("contrast", v);
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="normal">Normal</SelectItem>
                <SelectItem value="high">High contrast</SelectItem>
              </SelectContent>
            </Select>
            <LineSaveHint s={fieldStates.contrast} />
          </div>
        )}
      </form.Field>
      <form.Field name="email_reminders">
        {(field) => (
          <div className="space-y-1">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={field.state.value}
                onChange={(e) => {
                  field.handleChange(e.target.checked);
                  save("email_reminders", e.target.checked);
                }}
              />
              Email me follow-up reminders
            </label>
            <LineSaveHint s={fieldStates.email_reminders} />
          </div>
        )}
      </form.Field>
      <form.Field name="show_socials">
        {(field) => (
          <div className="space-y-1">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={field.state.value}
                onChange={(e) => {
                  field.handleChange(e.target.checked);
                  save("show_socials", e.target.checked);
                }}
              />
              Show my contact details & socials on the exported CV
            </label>
            <LineSaveHint s={fieldStates.show_socials} />
          </div>
        )}
      </form.Field>
      <Button type="submit" disabled={busy}>
        Save
      </Button>
    </form>
  );
}
