# Phase 2b setup guide — auth flows + account pages (hands-on, no shortcuts)

Goal of this guide: take the Phase 2a scaffold and grow it into a real, end-to-end auth surface that drives the headless allauth API. By the end you can sign up, verify email, log in, recover a password, enrol TOTP + a WebAuthn passkey, manage emails + sessions, change/reset password, and delete the account — all from React, all against the running Django backend.

This is **Phase 2b only**. Phase 2c (CV CRUD) and 2d (LLM connector UI) come after the auth/account surfaces are solid.

Run every command from `frontend/` unless the step says otherwise. Backend on `http://localhost:8000`, frontend on `http://localhost:5173`. If a step's "verify" check fails, stop and fix before moving on.

---

## 0. Preflight

Phase 2a should be committed (commit `fa30c6e`). Confirm:

```bash
cd frontend
ls src/routes/_authenticated.tsx src/routes/auth/login.tsx src/lib/auth.ts src/lib/api.ts
# all four should exist
```

Backend up, email backend = console (it is by default):

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5173/_allauth/browser/v1/auth/session
# expect 401 — anonymous session. 401 here means "proxy works, allauth answered,
# nobody is logged in." A 200 only comes back once you have a session cookie.
# A 502/connection refused means the backend or proxy is down — fix that first.
```

(See §1 — allauth uses 401 to mean "I heard you, here's the flow you'd need to enter." For an anonymous curl with no cookies, that's the success case.)

Run the backend suite once so you know it's green going in:

```bash
cd ../backend && python manage.py test && cd ../frontend
# expect "Ran 163 tests ... OK"
```

---

## 1. Headless allauth — the contract you're coding against

Before writing any UI, internalise the response shape. Every `_allauth/browser/v1/...` response is:

```ts
{
  status: number,            // mirrors HTTP status — 200 ok, 401 needs-something, 409 conflict, 410 expired
  data?: { user?, flows?, methods? },
  meta?: { is_authenticated?: boolean, ... },
  errors?: [{ message, code, param }]
}
```

The crucial idea: **401 is not always "anonymous"**. allauth returns 401 to mean "we know who you are but you owe us one more step." The `data.flows` array tells you what step. The flow ids you'll see in this phase:

| flow id              | meaning                                                |
| -------------------- | ------------------------------------------------------ |
| `verify_email`       | user just signed up, must enter the emailed code       |
| `login`              | normal anonymous → login form                          |
| `mfa_authenticate`   | password ok, TOTP/recovery-code/passkey still required |
| `reauthenticate`     | sensitive endpoint demands recent password reauth      |
| `mfa_reauthenticate` | sensitive endpoint demands recent MFA reauth           |

Spec: <https://docs.allauth.org/en/latest/headless/openapi-specification/>. Open it in another tab — you'll consult it more than this guide.

Endpoints we will hit (all under `/_allauth/browser/v1`):

| Path                                     | Methods                       | Used by                                           |
| ---------------------------------------- | ----------------------------- | ------------------------------------------------- |
| `/auth/session`                          | GET, DELETE                   | session probe; logout                             |
| `/auth/signup`                           | POST                          | `/auth/signup`                                    |
| `/auth/login`                            | POST                          | `/auth/login`                                     |
| `/auth/email/verify`                     | POST, GET                     | `/auth/verify-email` (code entry)                 |
| `/auth/password/request`                 | POST                          | `/auth/request-reset`                             |
| `/auth/password/reset`                   | POST                          | `/auth/reset-password/$key`                       |
| `/auth/reauthenticate`                   | POST                          | sensitive flows (password change, TOTP add, etc.) |
| `/auth/2fa/authenticate`                 | POST                          | `/auth/mfa-challenge`                             |
| `/auth/2fa/reauthenticate`               | POST                          | sensitive MFA flows                               |
| `/auth/webauthn/login`                   | GET, POST                     | passkey login                                     |
| `/account/email`                         | GET, POST, PATCH, DELETE, PUT | `/account/email`                                  |
| `/account/password/change`               | POST                          | `/account/security`                               |
| `/account/authenticators`                | GET                           | `/account/security` overview                      |
| `/account/authenticators/totp`           | GET, POST, DELETE             | TOTP enrol/remove                                 |
| `/account/authenticators/recovery-codes` | GET, POST                     | recovery-code reveal/regenerate                   |
| `/account/authenticators/webauthn`       | GET, POST, PUT, DELETE        | passkey list/add/rename/remove                    |
| `/auth/sessions`                         | GET, DELETE                   | log out other sessions                            |

Settings already locked in [backend/lukehirsch/settings.py](backend/lukehirsch/settings.py):

- `ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = True` — verify-email shows a **code input**, not a click-through link.
- `HEADLESS_FRONTEND_URLS["account_reset_password_from_key"] = FRONTEND_URL + "/auth/reset-password/{key}"` — the reset email links to that route, which then POSTs `/auth/password/reset`.
- `MFA_PASSKEY_LOGIN_ENABLED = True` — passkey-only login is allowed; surface it on `/auth/login`.

---

## 2. Refactor the API + auth library for flows

Phase 2a's `useAuth()` understands `anonymous | authenticated | mfa_required`. Phase 2b adds two more states (`verify_email_required`, `reauth_required`) and exposes the raw `flows` so pages can react.

Replace `src/lib/auth.ts`:

```ts
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";

export type AuthStatus =
  | "anonymous"
  | "authenticated"
  | "verify_email_required"
  | "mfa_required"
  | "reauth_required";

export type Flow = { id: string; providers?: string[]; is_pending?: boolean };

export type SessionResponse = {
  status: number;
  data?: {
    user?: { id: number; email: string; display?: string };
    flows?: Flow[];
    methods?: { id: string; at: number; email?: string }[];
  };
  meta?: { is_authenticated?: boolean };
};

export const SESSION_KEY = ["auth", "session"] as const;

async function fetchSession(): Promise<SessionResponse> {
  try {
    return await api<SessionResponse>("/_allauth/browser/v1/auth/session");
  } catch (e) {
    // allauth returns 401/410 with a valid body when the session is anonymous
    // or expired — treat the payload as truth, not the HTTP code.
    if (e instanceof ApiError && (e.status === 401 || e.status === 410)) {
      return e.data as SessionResponse;
    }
    throw e;
  }
}

export function useAuth() {
  const query = useQuery({
    queryKey: SESSION_KEY,
    queryFn: fetchSession,
    staleTime: 30_000,
  });

  const data = query.data;
  const flows = data?.data?.flows ?? [];
  const pending = flows.find((f) => f.is_pending)?.id;

  let status: AuthStatus = "anonymous";
  if (data?.meta?.is_authenticated) status = "authenticated";
  else if (pending === "verify_email") status = "verify_email_required";
  else if (pending === "mfa_authenticate") status = "mfa_required";
  else if (pending === "reauthenticate" || pending === "mfa_reauthenticate")
    status = "reauth_required";

  return { ...query, status, user: data?.data?.user, flows };
}

export function useInvalidateSession() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: SESSION_KEY });
}
```

`useInvalidateSession` is the hammer every successful auth mutation hits to force the next render through the guard logic.

Update `src/routes/_authenticated.tsx` so it uses the shared `fetchSession`. The fancy "route by pending flow" branching arrives in §11 _after_ `/auth/verify-email` and `/auth/mfa-challenge` exist — TanStack Router's typed `to:` only accepts routes the codegen has seen. For now: probe the session, redirect anonymous users to `/auth/login`.

```tsx
import { createFileRoute, redirect, isRedirect } from "@tanstack/react-router";
import { fetchSession } from "@/lib/auth";

export const Route = createFileRoute("/_authenticated")({
  beforeLoad: async ({ location }) => {
    try {
      const session = await fetchSession();
      if (session.meta?.is_authenticated) return;
      throw redirect({
        to: "/auth/login",
        search: { redirect: location.href },
      });
    } catch (e) {
      if (isRedirect(e)) throw e;
      throw redirect({
        to: "/auth/login",
        search: { redirect: location.href },
      });
    }
  },
});
```

(For this to compile, prefix `fetchSession` in `lib/auth.ts` with `export`: `export async function fetchSession(...)`.)

Sanity build:

```bash
npx tsc -b
# expect zero output
```

---

## 3. Form scaffolding — TanStack Form + Zod + shadcn

Every page in this phase is a small typed form. Set up the shared bits once.

We already have `@tanstack/react-form`, `zod`, and the shadcn `form` component. Add a thin Zod-resolver helper and a shared `<FieldError>` component.

Create `src/lib/form.ts`:

```ts
import { z, ZodType } from "zod";

export function zodValidator<T>(schema: ZodType<T>) {
  return ({ value }: { value: T }) => {
    const result = schema.safeParse(value);
    if (result.success) return undefined;
    const fields: Record<string, string> = {};
    for (const issue of result.error.issues) {
      const key = issue.path.join(".");
      if (!fields[key]) fields[key] = issue.message;
    }
    return { fields };
  };
}

export { z };
```

That's it — TanStack Form's `validators.onChange` accepts the `{ fields: { ... } }` shape directly.

Server errors come back as `data.errors: [{message, code, param}]`. Add a normaliser to `src/lib/api.ts` (append to the bottom):

```ts

```

### 3.1. Centralised allauth-outcome resolver

Three of the next four pages (login, signup, mfa-challenge) all need to decode the same response shape: success → 200 with `meta.is_authenticated`; "already in" → 409; "MFA pending" → 401 + `mfa_authenticate` flow; "verify pending" → 401 + `verify_email` flow; otherwise an error. Rather than re-branching that logic per page, define it once.

`src/lib/auth-flow.ts`:

```ts
import { ApiError, allauthErrorsByField } from "@/lib/api";
import type { SessionResponse } from "@/lib/auth";

export type AuthOutcome =
  | { kind: "authenticated"; response: SessionResponse }
  | { kind: "already_authenticated" }
  | { kind: "verify_email" }
  | { kind: "mfa_authenticate" }
  | { kind: "error"; fields: Record<string, string> };

/**
 * Decode an allauth response (success body or thrown ApiError) into a single
 * outcome the call site can switch on. Pass `useMutation`'s `onSuccess` payload
 * or `onError` payload directly.
 */
export function resolveAuthOutcome(input: unknown): AuthOutcome {
  if (input instanceof ApiError) {
    if (input.status === 409) return { kind: "already_authenticated" };
    if (input.status === 401) {
      const body = input.data as
        | { data?: { flows?: { id: string; is_pending?: boolean }[] } }
        | undefined;
      const flows = body?.data?.flows ?? [];
      const pending = flows.find((f) => f.is_pending)?.id ?? flows[0]?.id;
      if (pending === "verify_email") return { kind: "verify_email" };
      if (pending === "mfa_authenticate") return { kind: "mfa_authenticate" };
    }
    return { kind: "error", fields: allauthErrorsByField(input) };
  }
  const resp = input as SessionResponse | undefined;
  if (resp?.meta?.is_authenticated)
    return { kind: "authenticated", response: resp };
  return { kind: "error", fields: {} };
}
```

With this in place, the login/signup mutation handlers each shrink to:

```tsx
async function handleOutcome(input: unknown) {
  await invalidate();
  const outcome = resolveAuthOutcome(input);
  switch (outcome.kind) {
    case "authenticated":
    case "already_authenticated":
      navigate({ to: redirect ?? "/", search: {} as never });
      return;
    case "mfa_authenticate":
      navigate({
        to: "/auth/mfa-challenge" as never,
        search: { redirect } as never,
      });
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
  mutationFn: (body) =>
    api("/_allauth/browser/v1/auth/login", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  onSuccess: handleOutcome,
  onError: handleOutcome,
});
```

Each call site keeps its own "where to go on success" (login → `redirect ?? "/"`; signup → `/auth/verify-email`; mfa-challenge → `next ?? redirect`) — but the branching disappears. The "(`as never` on the `to`)" hack is needed only until those routes exist; once §6/§8 land their files, the codegen narrows the type and the casts can come off.

§§5–8 below use this helper directly. If you read those sections without first writing `auth-flow.ts`, the imports will fail.

---

Add the sonner toaster mount once at the root. Edit `src/routes/__root.tsx`:

```tsx
import { Outlet, createRootRoute } from "@tanstack/react-router";
import { Toaster } from "@/components/ui/sonner";

export const Route = createRootRoute({
  component: () => (
    <div className="min-h-screen bg-background text-foreground">
      <Outlet />
      <Toaster richColors position="top-right" />
    </div>
  ),
});
```

---

## 4. Auth layout + shared chrome

All six `/auth/*` pages share a centered card. Create a pathless layout route at `src/routes/auth.tsx`:

```tsx
import { createFileRoute, Outlet, Link } from "@tanstack/react-router";

export const Route = createFileRoute("/auth")({
  component: () => (
    <div className="min-h-screen grid place-items-center p-4 bg-muted/30">
      <div className="w-full max-w-sm space-y-6">
        <Link
          to="/"
          className="block text-center text-sm text-muted-foreground hover:underline"
        >
          ← back to home
        </Link>
        <Outlet />
      </div>
    </div>
  ),
});
```

Quick note on routing: this turns `/auth/*` into nested routes that render through this layout. Your existing `src/routes/auth/login.tsx` keeps the same URL but now mounts inside this layout — the file-based router resolves `auth.tsx` + `auth/login.tsx` automatically.

---

## 5. `/auth/signup`

Replace nothing — create `src/routes/auth/signup.tsx`:

```tsx
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
        navigate({ to: "/", search: {} as never });
        return;
      case "verify_email":
        navigate({ to: "/auth/verify-email" });
        return;
      case "mfa_authenticate":
        // Doesn't happen on signup — the user has no authenticator yet — but
        // fall back gracefully if the contract ever changes.
        navigate({ to: "/auth/mfa-challenge" as never, search: {} as never });
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

function FieldError({ msg }: { msg?: string }) {
  if (!msg) return null;
  return <p className="text-sm text-destructive">{msg}</p>;
}
```

`FieldError` is duplicated across pages — promote it to `src/components/field-error.tsx` once the second page lands.

Verify:

```bash
npm run dev
# In incognito: visit /auth/signup, submit valid form.
# Backend terminal should print a verification email; you should land on /auth/verify-email.
```

---

## 6. `/auth/verify-email`

Two ways in: (a) user just signed up and was redirected here, (b) user came in later and the session still says `verify_email` is pending. Both work the same — POST the code to `/auth/email/verify`.

Create `src/routes/auth/verify-email.tsx`:

```tsx
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useForm } from "@tanstack/react-form";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError, allauthErrorsByField } from "@/lib/api";
import { useInvalidateSession } from "@/lib/auth";
import { zodValidator, z } from "@/lib/form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

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
      navigate({ to: "/account/profile" });
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
```

Verify: paste the code from the backend terminal, land on `/account/profile`. (We'll build that page next — for now you'll see a TanStack Router "Not Found" or the guard kicking in. That's fine, the navigate worked.)

---

## 7. `/auth/login`

Now upgrade the 2a stub. The login response branches three ways:

| Result           | What allauth returns                         | What we do                     |
| ---------------- | -------------------------------------------- | ------------------------------ |
| Logged in        | 200 with `meta.is_authenticated: true`       | redirect to `?redirect` or `/` |
| MFA owed         | 401 with `flows: [{id: "mfa_authenticate"}]` | navigate `/auth/mfa-challenge` |
| Email unverified | 401 with `flows: [{id: "verify_email"}]`     | navigate `/auth/verify-email`  |
| Bad credentials  | 400/401 + `errors`                           | toast the message              |

Replace `src/routes/auth/login.tsx`:

```tsx
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
        navigate({ to: redirect ?? "/", search: {} as never });
        return;
      case "mfa_authenticate":
        navigate({
          to: "/auth/mfa-challenge" as never,
          search: { redirect } as never,
        });
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

function PasskeyLoginButton() {
  // Placeholder; full WebAuthn ceremony lives in step 11.
  return null;
}
```

Verify: log in with the verified user → land on `/` (the home from 2a).

---

## 8. `/auth/mfa-challenge`

Used both from the login redirect _and_ from `AdminRequireMfaMiddleware` (which redirects staff users with `?next=/admin/`). Code accepts a TOTP code, a recovery code, or — once we wire it — a passkey.

Create `src/routes/auth/mfa-challenge.tsx`:

```tsx
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

  const verify = useMutation({
    mutationFn: (body: { code: string }) =>
      api("/_allauth/browser/v1/auth/2fa/authenticate", {
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
```

Notice the `next` branch uses `window.location.href` — that's a server-rendered URL (`/admin/`), not a client route, so a full navigation is correct.

---

## 9. `/auth/request-reset` + `/auth/reset-password/$key`

The settings in [backend/lukehirsch/settings.py:235](backend/lukehirsch/settings.py#L235) embed the React link `FRONTEND_URL/auth/reset-password/{key}` in the email — so the route must be `/auth/reset-password/$key`, not `/auth/confirm-reset`.

Create `src/routes/auth/request-reset.tsx`:

```tsx
import { createFileRoute, Link } from "@tanstack/react-router";
import { useForm } from "@tanstack/react-form";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
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
    onError: () => toast.success("Check your inbox for a reset link."), // don't leak account existence
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
```

> **Email-enumeration trade-off:** the `onSuccess` / `onError` handlers both show the same toast on purpose so the UI can't be used to probe whether an email is registered. allauth already replies 200 either way for the same reason. Only loosen this if your threat model explicitly accepts enumeration.

Create the confirmation page at `src/routes/auth/reset-password.$key.tsx` (the `$` denotes a dynamic segment in file-based routing):

```tsx
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
    mutationFn: (body: { password: string }) =>
      api("/_allauth/browser/v1/auth/password/reset", {
        method: "POST",
        body: JSON.stringify({ key, password: body.password }),
      }),
    onSuccess: async () => {
      await invalidate();
      toast.success("Password reset");
      navigate({ to: "/auth/login", search: {} as never });
    },
    onError: (e) => {
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
```

Verify the full reset round-trip:

1. From `/auth/login`, click "Forgot?", submit email.
2. Look at the backend terminal — copy the link beginning with `http://localhost:5173/auth/reset-password/...`.
3. Open it, choose a new password, log in with it.

---

## 10. Account layout + shared sidebar

All four `/account/*` pages share a left-nav. Create `src/routes/_authenticated/account.tsx` (replaces the 2a stub):

```tsx
import { createFileRoute, Link, Outlet } from "@tanstack/react-router";
import { Card } from "@/components/ui/card";

const ITEMS = [
  { to: "/account/profile", label: "Profile" },
  { to: "/account/email", label: "Email addresses" },
  { to: "/account/security", label: "Security" },
  { to: "/account/danger", label: "Danger zone" },
] as const;

export const Route = createFileRoute("/_authenticated/account")({
  component: AccountLayout,
});

function AccountLayout() {
  return (
    <div className="max-w-5xl mx-auto p-6 grid gap-6 md:grid-cols-[180px_1fr]">
      <nav className="space-y-1">
        {ITEMS.map((i) => (
          <Link
            key={i.to}
            to={i.to}
            className="block px-3 py-2 rounded-md text-sm hover:bg-muted"
            activeProps={{ className: "bg-muted font-medium" }}
          >
            {i.label}
          </Link>
        ))}
      </nav>
      <Card className="p-6">
        <Outlet />
      </Card>
    </div>
  );
}
```

Now make the four children. Each goes in `src/routes/_authenticated/account/`.

### 10a. `/account/profile`

`src/routes/_authenticated/account/profile.tsx`:

```tsx
import { createFileRoute } from "@tanstack/react-router";
import { useForm } from "@tanstack/react-form";
import { useQuery, useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
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
  display_name: string;
  bio: string;
  phone: string;
  website: string;
  linkedin_url: string;
  github_url: string;
  timezone: string;
  theme: "system" | "light" | "dark";
  contrast: "normal" | "high";
  email_reminders: boolean;
};

const schema = z.object({
  display_name: z.string().max(100),
  bio: z.string().max(500),
  phone: z.string().max(30),
  website: z.string().url().or(z.literal("")),
  linkedin_url: z.string().url().or(z.literal("")),
  github_url: z.string().url().or(z.literal("")),
  timezone: z.string().min(1),
  theme: z.enum(["system", "light", "dark"]),
  contrast: z.enum(["normal", "high"]),
  email_reminders: z.boolean(),
});

export const Route = createFileRoute("/_authenticated/account/profile")({
  component: ProfilePage,
});

function ProfilePage() {
  const profileQ = useQuery({
    queryKey: ["profile"],
    queryFn: () => api<Profile>("/api/spa/profile/"),
  });

  const patch = useMutation({
    mutationFn: (body: Partial<Profile>) =>
      api<Profile>("/api/spa/profile/", {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => toast.success("Saved"),
    onError: () => toast.error("Save failed"),
  });

  if (profileQ.isLoading) return <p>loading…</p>;
  if (!profileQ.data) return <p>could not load profile</p>;

  const p = profileQ.data;

  return (
    <ProfileForm
      initial={{
        display_name: p.display_name,
        bio: p.bio,
        phone: p.phone,
        website: p.website,
        linkedin_url: p.linkedin_url,
        github_url: p.github_url,
        timezone: p.timezone,
        theme: p.theme,
        contrast: p.contrast,
        email_reminders: p.email_reminders,
      }}
      onSubmit={(v) => patch.mutateAsync(v)}
      busy={patch.isPending}
    />
  );
}

function ProfileForm({
  initial,
  onSubmit,
  busy,
}: {
  initial: z.infer<typeof schema>;
  onSubmit: (v: z.infer<typeof schema>) => Promise<unknown>;
  busy: boolean;
}) {
  const form = useForm({
    defaultValues: initial,
    validators: { onChange: zodValidator(schema) },
    onSubmit: ({ value }) => onSubmit(value),
  });

  return (
    <form
      className="space-y-5"
      onSubmit={(e) => {
        e.preventDefault();
        form.handleSubmit();
      }}
    >
      <TextField form={form} name="display_name" label="Display name" />
      <TextareaField form={form} name="bio" label="Bio" />
      <TextField form={form} name="phone" label="Phone" />
      <TextField form={form} name="website" label="Website" type="url" />
      <TextField form={form} name="linkedin_url" label="LinkedIn" type="url" />
      <TextField form={form} name="github_url" label="GitHub" type="url" />
      <TextField form={form} name="timezone" label="Timezone" />
      <form.Field name="theme">
        {(field) => (
          <div className="space-y-1">
            <Label>Theme</Label>
            <Select
              value={field.state.value}
              onValueChange={(v) => field.handleChange(v as never)}
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
          </div>
        )}
      </form.Field>
      <form.Field name="contrast">
        {(field) => (
          <div className="space-y-1">
            <Label>Contrast</Label>
            <Select
              value={field.state.value}
              onValueChange={(v) => field.handleChange(v as never)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="normal">Normal</SelectItem>
                <SelectItem value="high">High contrast</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}
      </form.Field>
      <form.Field name="email_reminders">
        {(field) => (
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={field.state.value}
              onChange={(e) => field.handleChange(e.target.checked)}
            />
            Email me follow-up reminders
          </label>
        )}
      </form.Field>
      <Button type="submit" disabled={busy}>
        Save
      </Button>
    </form>
  );
}

function TextField({ form, name, label, type = "text" }: any) {
  return (
    <form.Field name={name}>
      {(field: any) => (
        <div className="space-y-1">
          <Label htmlFor={name}>{label}</Label>
          <Input
            id={name}
            type={type}
            value={field.state.value}
            onChange={(e) => field.handleChange(e.target.value)}
          />
        </div>
      )}
    </form.Field>
  );
}

function TextareaField({ form, name, label }: any) {
  return (
    <form.Field name={name}>
      {(field: any) => (
        <div className="space-y-1">
          <Label htmlFor={name}>{label}</Label>
          <Textarea
            id={name}
            rows={4}
            value={field.state.value}
            onChange={(e) => field.handleChange(e.target.value)}
          />
        </div>
      )}
    </form.Field>
  );
}
```

Avatar upload is deferred — it needs `multipart/form-data` which the JSON `api()` wrapper doesn't speak. Add a follow-up note to Phase 3 ("avatar upload helper + DRF parser tweak"). Don't shoehorn it in now.

Verify: visit `/account/profile`, change display name → toast success → reload → value persists.

### 10b. `/account/email`

allauth's `/_allauth/browser/v1/account/email` is the one URL with four methods. The contract:

| Method | Body                       | Effect                                             |
| ------ | -------------------------- | -------------------------------------------------- |
| GET    | —                          | list `{ data: [{email, verified, primary}, ...] }` |
| POST   | `{ email }`                | add new address (sends verification email)         |
| PATCH  | `{ email }`                | resend verification for that address               |
| PUT    | `{ email, primary: true }` | make it primary (must be verified)                 |
| DELETE | `{ email }`                | remove address                                     |

`src/routes/_authenticated/account/email.tsx`:

```tsx
import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm } from "@tanstack/react-form";
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

function EmailPage() {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: EMAIL_KEY,
    queryFn: () =>
      api<{ data: EmailRow[] }>("/_allauth/browser/v1/account/email").then(
        (r) => r.data,
      ),
  });

  const mutate = (init: RequestInit, onOk: string) =>
    useMutation({
      mutationFn: (body: object) =>
        api("/_allauth/browser/v1/account/email", {
          ...init,
          body: JSON.stringify(body),
        }),
      onSuccess: () => {
        qc.invalidateQueries({ queryKey: EMAIL_KEY });
        toast.success(onOk);
      },
      onError: (e) => {
        const fields = allauthErrorsByField(e);
        toast.error(fields.email ?? fields.__non_field__ ?? "Failed");
      },
    });

  const add = mutate({ method: "POST" }, "Verification sent");
  const resend = mutate({ method: "PATCH" }, "Verification resent");
  const setPrimary = mutate({ method: "PUT" }, "Primary updated");
  const remove = mutate({ method: "DELETE" }, "Email removed");

  const form = useForm({
    defaultValues: { email: "" },
    validators: {
      onChange: zodValidator(z.object({ email: z.string().email() })),
    },
    onSubmit: async ({ value, formApi }) => {
      await add.mutateAsync({ email: value.email });
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
          <li
            key={row.email}
            className="flex items-center justify-between rounded border p-3"
          >
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
                  onClick={() => resend.mutate({ email: row.email })}
                >
                  Resend
                </Button>
              )}
              {!row.primary && row.verified && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    setPrimary.mutate({ email: row.email, primary: true })
                  }
                >
                  Make primary
                </Button>
              )}
              {!row.primary && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => remove.mutate({ email: row.email })}
                >
                  Remove
                </Button>
              )}
            </div>
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
        <Button type="submit" className="self-end" disabled={add.isPending}>
          Add
        </Button>
      </form>
    </div>
  );
}
```

### 10c. `/account/security` — password + MFA + passkeys

This is the heaviest page. Split it into three sections.

`src/routes/_authenticated/account/security.tsx`:

```tsx
import { createFileRoute } from "@tanstack/react-router";
import { ChangePassword } from "@/components/security/change-password";
import { TotpPanel } from "@/components/security/totp-panel";
import { PasskeyPanel } from "@/components/security/passkey-panel";
import { Separator } from "@/components/ui/separator";

export const Route = createFileRoute("/_authenticated/account/security")({
  component: Security,
});

function Security() {
  return (
    <div className="space-y-8">
      <section>
        <h2 className="text-lg font-semibold mb-2">Password</h2>
        <ChangePassword />
      </section>
      <Separator />
      <section>
        <h2 className="text-lg font-semibold mb-2">Authenticator app (TOTP)</h2>
        <TotpPanel />
      </section>
      <Separator />
      <section>
        <h2 className="text-lg font-semibold mb-2">Passkeys</h2>
        <PasskeyPanel />
      </section>
    </div>
  );
}
```

You'll need `separator` from shadcn:

```bash
npx shadcn@latest add separator
```

#### 10c.i. Change password

`src/components/security/change-password.tsx`:

```tsx
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
```

#### 10c.ii. Reauthenticate helper

Several endpoints below 401 with `flows: [{id: "reauthenticate"}]` if you haven't entered your password recently. Centralise the response:

`src/lib/reauth.ts`:

```ts
import { api, ApiError } from "@/lib/api";

/**
 * Run `fn`. If allauth says we need a fresh password, prompt for one and retry.
 *
 * Returns the result of `fn` on success, or throws on cancellation / unrelated errors.
 */
export async function withReauth<T>(
  fn: () => Promise<T>,
  prompt = "Confirm your password",
): Promise<T> {
  try {
    return await fn();
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 401) throw e;
    const body = e.data as {
      data?: { flows?: { id: string; is_pending?: boolean }[] };
    };
    const needsReauth = body.data?.flows?.some(
      (f) => f.id === "reauthenticate" && f.is_pending,
    );
    if (!needsReauth) throw e;

    const password = window.prompt(prompt);
    if (!password) throw new Error("Reauth cancelled");
    await api("/_allauth/browser/v1/auth/reauthenticate", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    return await fn();
  }
}
```

`window.prompt` is fine as a placeholder; swap it for a shadcn `Dialog` later. Mark it as a 2c follow-up.

#### 10c.iii. TOTP panel

`src/components/security/totp-panel.tsx`:

```tsx
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm } from "@tanstack/react-form";
import { QRCodeSVG } from "qrcode.react";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { withReauth } from "@/lib/reauth";
import { zodValidator, z } from "@/lib/form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type TotpMeta = { secret: string; totp_url: string };
const TOTP_KEY = ["mfa", "totp"] as const;
const RECOVERY_KEY = ["mfa", "recovery"] as const;

export function TotpPanel() {
  const qc = useQueryClient();

  const totp = useQuery({
    queryKey: TOTP_KEY,
    queryFn: async () => {
      try {
        await api("/_allauth/browser/v1/account/authenticators/totp");
        return { activated: true, meta: null as TotpMeta | null };
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          const meta = (e.data as { meta?: TotpMeta }).meta!;
          return { activated: false, meta };
        }
        throw e;
      }
    },
  });

  const activate = useMutation({
    mutationFn: (body: { code: string }) =>
      withReauth(() =>
        api("/_allauth/browser/v1/account/authenticators/totp", {
          method: "POST",
          body: JSON.stringify(body),
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: TOTP_KEY });
      toast.success("Authenticator enrolled");
    },
    onError: () => toast.error("Code rejected"),
  });

  const deactivate = useMutation({
    mutationFn: () =>
      withReauth(() =>
        api("/_allauth/browser/v1/account/authenticators/totp", {
          method: "DELETE",
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: TOTP_KEY });
      qc.invalidateQueries({ queryKey: RECOVERY_KEY });
      toast.success("Authenticator removed");
    },
  });

  const form = useForm({
    defaultValues: { code: "" },
    validators: {
      onChange: zodValidator(z.object({ code: z.string().min(6).max(8) })),
    },
    onSubmit: ({ value, formApi }) =>
      activate.mutateAsync(value).then(() => formApi.reset()),
  });

  if (totp.isLoading)
    return <p className="text-sm text-muted-foreground">loading…</p>;

  if (totp.data?.activated) {
    return (
      <div className="space-y-3">
        <p className="text-sm">Authenticator app is active.</p>
        <Button
          variant="destructive"
          size="sm"
          onClick={() => deactivate.mutate()}
        >
          Remove authenticator
        </Button>
        <RecoveryCodesPanel />
      </div>
    );
  }

  const meta = totp.data?.meta!;
  return (
    <div className="space-y-4">
      <p className="text-sm">
        Scan this QR with your authenticator app, then enter a 6-digit code:
      </p>
      <div className="rounded border inline-block p-3 bg-white">
        <QRCodeSVG value={meta.totp_url} size={160} />
      </div>
      <p className="text-xs text-muted-foreground break-all">
        or paste this secret manually:{" "}
        <code className="font-mono">{meta.secret}</code>
      </p>
      <form
        className="flex gap-2 max-w-xs"
        onSubmit={(e) => {
          e.preventDefault();
          form.handleSubmit();
        }}
      >
        <form.Field name="code">
          {(field) => (
            <div className="flex-1 space-y-1">
              <Label htmlFor={field.name}>Code</Label>
              <Input
                id={field.name}
                inputMode="numeric"
                autoComplete="one-time-code"
                value={field.state.value}
                onChange={(e) => field.handleChange(e.target.value.trim())}
              />
            </div>
          )}
        </form.Field>
        <Button
          type="submit"
          className="self-end"
          disabled={activate.isPending}
        >
          Activate
        </Button>
      </form>
    </div>
  );
}

function RecoveryCodesPanel() {
  const qc = useQueryClient();
  const codes = useQuery({
    queryKey: RECOVERY_KEY,
    queryFn: () =>
      api<{ data: { unused_codes: string[] } }>(
        "/_allauth/browser/v1/account/authenticators/recovery-codes",
      ).then((r) => r.data),
  });

  const regen = useMutation({
    mutationFn: () =>
      withReauth(() =>
        api("/_allauth/browser/v1/account/authenticators/recovery-codes", {
          method: "POST",
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: RECOVERY_KEY });
      toast.success("Recovery codes regenerated");
    },
  });

  if (!codes.data) return null;

  return (
    <div className="mt-4 space-y-2">
      <h3 className="text-sm font-medium">Recovery codes</h3>
      <p className="text-xs text-muted-foreground">
        Store these somewhere safe. Each is single-use.
      </p>
      <ul className="grid grid-cols-2 gap-1 font-mono text-sm bg-muted/40 p-3 rounded">
        {codes.data.unused_codes.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>
      <Button variant="outline" size="sm" onClick={() => regen.mutate()}>
        Regenerate
      </Button>
    </div>
  );
}
```

#### 10c.iv. Passkey panel

WebAuthn ceremonies need browser APIs (`navigator.credentials.create / get`) plus a tiny base64url helper. allauth sends the option dicts you forward to the browser, you forward the credential response back. Read the headless spec sections on `/account/authenticators/webauthn` and `/auth/webauthn/login` before debugging this.

`src/lib/webauthn.ts`:

```ts
function b64urlToBuf(s: string): ArrayBuffer {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const b64 = (s + pad).replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(b64);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return buf.buffer;
}

function bufToB64url(buf: ArrayBuffer | Uint8Array): string {
  const bytes = buf instanceof ArrayBuffer ? new Uint8Array(buf) : buf;
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function decodeCreationOptions(opts: any): CredentialCreationOptions {
  const pk = opts.publicKey;
  return {
    publicKey: {
      ...pk,
      challenge: b64urlToBuf(pk.challenge),
      user: { ...pk.user, id: b64urlToBuf(pk.user.id) },
      excludeCredentials: (pk.excludeCredentials ?? []).map((c: any) => ({
        ...c,
        id: b64urlToBuf(c.id),
      })),
    },
  };
}

export function decodeRequestOptions(opts: any): CredentialRequestOptions {
  const pk = opts.publicKey;
  return {
    publicKey: {
      ...pk,
      challenge: b64urlToBuf(pk.challenge),
      allowCredentials: (pk.allowCredentials ?? []).map((c: any) => ({
        ...c,
        id: b64urlToBuf(c.id),
      })),
    },
  };
}

export function encodeAttestation(cred: PublicKeyCredential) {
  const r = cred.response as AuthenticatorAttestationResponse;
  return {
    id: cred.id,
    rawId: bufToB64url(cred.rawId),
    type: cred.type,
    response: {
      attestationObject: bufToB64url(r.attestationObject),
      clientDataJSON: bufToB64url(r.clientDataJSON),
    },
  };
}

export function encodeAssertion(cred: PublicKeyCredential) {
  const r = cred.response as AuthenticatorAssertionResponse;
  return {
    id: cred.id,
    rawId: bufToB64url(cred.rawId),
    type: cred.type,
    response: {
      authenticatorData: bufToB64url(r.authenticatorData),
      clientDataJSON: bufToB64url(r.clientDataJSON),
      signature: bufToB64url(r.signature),
      userHandle: r.userHandle ? bufToB64url(r.userHandle) : null,
    },
  };
}
```

`src/components/security/passkey-panel.tsx`:

```tsx
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { withReauth } from "@/lib/reauth";
import { decodeCreationOptions, encodeAttestation } from "@/lib/webauthn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type Passkey = { id: number; name: string; created_at: string };
const KEY = ["mfa", "webauthn"] as const;

export function PasskeyPanel() {
  const qc = useQueryClient();
  const [newName, setNewName] = useState("");

  const list = useQuery({
    queryKey: KEY,
    queryFn: () =>
      api<{ data: Passkey[] }>(
        "/_allauth/browser/v1/account/authenticators/webauthn",
      ).then((r) => r.data),
  });

  const register = useMutation({
    mutationFn: async (name: string) => {
      const opts = await withReauth(() =>
        api<{ data: any }>(
          "/_allauth/browser/v1/account/authenticators/webauthn",
          { method: "GET" },
        ),
      );
      const cred = (await navigator.credentials.create(
        decodeCreationOptions(opts.data),
      )) as PublicKeyCredential | null;
      if (!cred) throw new Error("No credential created");
      return api("/_allauth/browser/v1/account/authenticators/webauthn", {
        method: "POST",
        body: JSON.stringify({ name, credential: encodeAttestation(cred) }),
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
      setNewName("");
      toast.success("Passkey registered");
    },
    onError: (e) => toast.error(`Registration failed: ${e.message}`),
  });

  const remove = useMutation({
    mutationFn: (id: number) =>
      withReauth(() =>
        api("/_allauth/browser/v1/account/authenticators/webauthn", {
          method: "DELETE",
          body: JSON.stringify({ authenticator: id }),
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });

  return (
    <div className="space-y-4">
      <ul className="space-y-2">
        {list.data?.map((pk) => (
          <li
            key={pk.id}
            className="flex items-center justify-between rounded border p-3"
          >
            <div>
              <div className="text-sm font-medium">{pk.name}</div>
              <div className="text-xs text-muted-foreground">
                added {new Date(pk.created_at).toLocaleDateString()}
              </div>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => remove.mutate(pk.id)}
            >
              Remove
            </Button>
          </li>
        ))}
        {!list.data?.length && (
          <li className="text-sm text-muted-foreground">No passkeys yet.</li>
        )}
      </ul>
      <div className="flex gap-2 max-w-sm">
        <Input
          placeholder="passkey name (e.g. iPhone, Yubikey 5)"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <Button
          onClick={() => register.mutate(newName)}
          disabled={!newName || register.isPending}
        >
          Add passkey
        </Button>
      </div>
    </div>
  );
}
```

Verify in Chrome:

- Visit `/account/security`.
- Activate TOTP using the QR (any authenticator app), confirm recovery codes show.
- Add a passkey — Chrome should pop the platform authenticator dialog. Touch ID / Windows Hello / etc.
- Log out, log back in → MFA challenge page expects a TOTP code. Recovery codes work too.

### 10d. `/auth/login` — passkey button (wire it up now)

Now that you can register a passkey, expose it on the login page. Replace `PasskeyLoginButton` in `src/routes/auth/login.tsx`:

```tsx
function PasskeyLoginButton() {
  const navigate = useNavigate();
  const invalidate = useInvalidateSession();

  const login = useMutation({
    mutationFn: async () => {
      const opts = await api<{ data: any }>(
        "/_allauth/browser/v1/auth/webauthn/login",
        { method: "GET" },
      );
      const cred = (await navigator.credentials.get(
        decodeRequestOptions(opts.data),
      )) as PublicKeyCredential | null;
      if (!cred) throw new Error("No credential selected");
      return api<{ meta?: { is_authenticated?: boolean } }>(
        "/_allauth/browser/v1/auth/webauthn/login",
        {
          method: "POST",
          body: JSON.stringify({ credential: encodeAssertion(cred) }),
        },
      );
    },
    onSuccess: async (resp) => {
      await invalidate();
      if (resp.meta?.is_authenticated)
        navigate({ to: "/", search: {} as never });
    },
    onError: (e) => toast.error(`Passkey login failed: ${e.message}`),
  });

  return (
    <Button
      variant="outline"
      className="w-full mt-3"
      onClick={() => login.mutate()}
    >
      Sign in with passkey
    </Button>
  );
}
```

Add the imports at the top: `decodeRequestOptions`, `encodeAssertion` from `@/lib/webauthn`. (TanStack Router's `useNavigate` and `useMutation` are already imported.)

### 10e. `/account/danger`

`src/routes/_authenticated/account/danger.tsx`:

```tsx
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { withReauth } from "@/lib/reauth";
import { useInvalidateSession } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type Sess = {
  id: number;
  ip: string;
  created_at: string;
  user_agent: string;
  is_current: boolean;
};

export const Route = createFileRoute("/_authenticated/account/danger")({
  component: DangerZone,
});

function DangerZone() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const invalidate = useInvalidateSession();
  const [confirmText, setConfirmText] = useState("");

  const sessions = useQuery({
    queryKey: ["account", "sessions"],
    queryFn: () =>
      api<{ data: Sess[] }>("/_allauth/browser/v1/auth/sessions").then(
        (r) => r.data,
      ),
  });

  const logoutOthers = useMutation({
    mutationFn: () =>
      api("/_allauth/browser/v1/auth/sessions", { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["account", "sessions"] });
      toast.success("Other sessions signed out");
    },
  });

  const logout = useMutation({
    mutationFn: () =>
      api("/_allauth/browser/v1/auth/session", { method: "DELETE" }),
    onSuccess: async () => {
      await invalidate();
      navigate({ to: "/auth/login", search: {} as never });
    },
  });

  const del = useMutation({
    mutationFn: () =>
      withReauth(() =>
        api("/_allauth/browser/v1/account", { method: "DELETE" }),
      ),
    onSuccess: async () => {
      await invalidate();
      toast.success("Account deleted");
      navigate({ to: "/", search: {} as never });
    },
    onError: () => toast.error("Could not delete account"),
  });

  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Sessions</h2>
        <ul className="space-y-2">
          {sessions.data?.map((s) => (
            <li key={s.id} className="rounded border p-3 text-sm">
              <div className="font-mono">
                {s.ip} · {new Date(s.created_at).toLocaleString()}
              </div>
              <div className="text-xs text-muted-foreground truncate">
                {s.user_agent}
              </div>
              {s.is_current && (
                <span className="text-xs text-emerald-600">
                  current session
                </span>
              )}
            </li>
          ))}
        </ul>
        <Button variant="outline" onClick={() => logoutOthers.mutate()}>
          Sign out other sessions
        </Button>
        <Button variant="outline" onClick={() => logout.mutate()}>
          Sign out
        </Button>
      </section>

      <section className="space-y-3 border-t pt-6">
        <h2 className="text-lg font-semibold text-destructive">
          Delete account
        </h2>
        <p className="text-sm text-muted-foreground">
          Permanently removes your profile, career entries, applications, and
          LLM configs.
        </p>
        <Label htmlFor="confirm">
          Type <code className="font-mono">delete</code> to confirm
        </Label>
        <Input
          id="confirm"
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
        />
        <Button
          variant="destructive"
          disabled={confirmText !== "delete" || del.isPending}
          onClick={() => del.mutate()}
        >
          Delete my account
        </Button>
      </section>
    </div>
  );
}
```

> **Account-delete endpoint note:** the allauth headless spec exposes the delete on `DELETE /_allauth/browser/v1/account` (no trailing segment). If your installed allauth version returns 404 here, check `python manage.py show_urls | grep allauth | grep -i account` and adjust. The roadmap calls this out as something we'll have a test for in Phase 2 verification.

---

## 11. Wire a global "sign out" affordance

Two things land here. (a) Now that `/auth/verify-email` and `/auth/mfa-challenge` exist, the codegen knows their types — so the guard can finally route the user to the _right_ recovery page based on the pending flow. (b) Add a small header above the account layout so the test loop in step 12 is friction-free. By default a pathless layout route renders `<Outlet />`, so adding a `component` is opt-in:

```tsx
import {
  createFileRoute,
  redirect,
  isRedirect,
  Outlet,
  Link,
  useNavigate,
} from "@tanstack/react-router";
import { useMutation } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import {
  useAuth,
  useInvalidateSession,
  type SessionResponse,
} from "@/lib/auth";
import { Button } from "@/components/ui/button";

async function probe(): Promise<SessionResponse> {
  try {
    return await api<SessionResponse>("/_allauth/browser/v1/auth/session");
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 410))
      return e.data as SessionResponse;
    throw e;
  }
}

export const Route = createFileRoute("/_authenticated")({
  beforeLoad: async ({ location }) => {
    try {
      const s = await probe();
      if (s.meta?.is_authenticated) return;
      const pending = s.data?.flows?.find((f) => f.is_pending)?.id;
      throw redirect({
        to:
          pending === "verify_email"
            ? "/auth/verify-email"
            : pending === "mfa_authenticate"
              ? "/auth/mfa-challenge"
              : "/auth/login",
        search: { redirect: location.href },
      });
    } catch (e) {
      if (isRedirect(e)) throw e;
      throw redirect({
        to: "/auth/login",
        search: { redirect: location.href },
      });
    }
  },
  component: AuthedLayout,
});

function AuthedLayout() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const invalidate = useInvalidateSession();
  const logout = useMutation({
    mutationFn: () =>
      api("/_allauth/browser/v1/auth/session", { method: "DELETE" }),
    onSuccess: async () => {
      await invalidate();
      navigate({ to: "/auth/login", search: {} as never });
    },
  });

  return (
    <div className="min-h-screen flex flex-col">
      <header className="flex items-center justify-between border-b px-4 py-2">
        <Link to="/" className="font-semibold">
          lukehirsch
        </Link>
        <div className="flex items-center gap-3 text-sm">
          <Link to="/account/profile" className="hover:underline">
            {user?.email}
          </Link>
          <Button variant="ghost" size="sm" onClick={() => logout.mutate()}>
            Sign out
          </Button>
        </div>
      </header>
      <main className="flex-1">
        <Outlet />
      </main>
    </div>
  );
}
```

---

## 12. End-to-end verification — the full loop

Backend + frontend running. In an incognito window:

1. **Signup.** `/auth/signup` → submit `you@example.com` + password. Backend terminal prints the verification email. Toast shows "Verification sent" (implicit, via redirect).
2. **Verify.** Land on `/auth/verify-email`. Paste the code → land on `/account/profile`. Header shows your email.
3. **Profile.** Change display name + theme → toast success → reload → values persist. (Inspect via Django shell: `User.objects.get(email="you@example.com").profile.display_name`.)
4. **Email.** `/account/email` → add a second address → backend prints a verify email → resend it → remove it.
5. **Password change.** `/account/security` → change password → log out (header button) → log back in with new password.
6. **Password reset.** Log out → `/auth/login` → "Forgot?" → paste email. Open the reset URL from the backend terminal → set a new password → land back on `/auth/login` → sign in.
7. **TOTP.** `/account/security` → scan QR with your phone (or any TOTP app) → enter code → see recovery codes. Log out → log in → land on `/auth/mfa-challenge` → enter TOTP code → in.
8. **Recovery code.** Log out → log in → use one of the saved recovery codes instead of a fresh TOTP. allauth marks it consumed.
9. **Passkey.** Back in `/account/security`, register a passkey (Chrome → Touch ID / Windows Hello / Yubikey). Log out. On `/auth/login`, click "Sign in with passkey" → tap your authenticator → in. Then log in again with email+password (with TOTP) to ensure both paths still work.
10. **Admin MFA gate.** `python manage.py createsuperuser` (or `python manage.py shell` → `u.is_staff = True; u.save()`), enrol TOTP in `/account/security`. Visit `http://localhost:8000/admin/` → redirected to `localhost:5173/auth/mfa-challenge?next=/admin/`. Submit a TOTP code → land back on `/admin/` logged in.
11. **Account delete.** `/account/danger` → type `delete` → confirm. Reload → can no longer log in. Django shell confirms the user is gone.

If all eleven pass, Phase 2b is done.

---

## 13. What you should have at the end

```
frontend/src/
├── components/
│   ├── field-error.tsx          # promoted from inline, once you DRY them up
│   ├── security/
│   │   ├── change-password.tsx
│   │   ├── passkey-panel.tsx
│   │   └── totp-panel.tsx
│   └── ui/
│       └── separator.tsx        # added by shadcn in step 10c
├── lib/
│   ├── api.ts                   # + allauthErrorsByField
│   ├── auth.ts                  # + useInvalidateSession, richer status
│   ├── form.ts                  # zodValidator helper
│   ├── reauth.ts                # withReauth() wrapper
│   └── webauthn.ts              # base64url + decode/encode helpers
└── routes/
    ├── __root.tsx                       # + <Toaster />
    ├── _authenticated.tsx               # smart redirect by pending flow + header
    ├── _authenticated/
    │   ├── account.tsx                  # account layout (left nav)
    │   └── account/
    │       ├── profile.tsx
    │       ├── email.tsx
    │       ├── security.tsx
    │       └── danger.tsx
    ├── auth.tsx                         # /auth/* layout
    ├── auth/
    │   ├── login.tsx                    # password + passkey
    │   ├── signup.tsx
    │   ├── verify-email.tsx
    │   ├── mfa-challenge.tsx
    │   ├── request-reset.tsx
    │   └── reset-password.$key.tsx
    └── index.tsx
```

Commit checkpoint:

```bash
git add frontend/ .claude/plans/phase-2b-setup-guide.md
git commit -m "Phase 2b: auth flows + account pages (allauth headless e2e)"
```

---

## 14. Known gaps to revisit in Phase 3

While doing this you'll feel the seams. Don't fix them in 2b — log them for the Phase 3 backend pass:

- **Avatar upload.** Needs a multipart helper + DRF `parser_classes` review on `UserProfileView`.
- **`withReauth` modal.** `window.prompt` is fine for now. Replace with a shadcn Dialog before any other reviewer sees it.
- **Sessions endpoint shape.** Confirm the `/_allauth/browser/v1/auth/sessions` payload matches the `Sess` type — different allauth versions vary slightly. Adjust either side as needed.
- **Account-delete endpoint shape.** Same: see the note in step 10e.
- **Email-enumeration on signup.** `signup` currently surfaces "email already in use" via the toast. If the threat model says "no enumeration on signup either", silence that error and rely on the verification email instead.
- **MFA reauth (`mfa_reauthenticate`) flow.** Not exercised yet — once a sensitive endpoint actually requires _MFA_ reauth (e.g. passkey removal on an MFA-only account), extend `withReauth` to handle the `mfa_reauthenticate` branch too.
- **Tests.** Backend has 163; frontend has zero. Phase 3 should at least add a Playwright smoke test for the signup → verify → login → MFA loop.

---

## What's next

- **2c** — six `/cv/*` list+editor pages backed by `/api/jac/`. The form scaffolding here (`zodValidator`, the `withReauth` pattern, the layout shape) carries directly over.
- **2d** — `/settings/llm` + `/settings/llm/usage` backed by `/api/llm/`. The write-only `api_key` field is the only real twist.vit
