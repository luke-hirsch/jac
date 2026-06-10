# Phase 2a setup guide — frontend foundation (hands-on, no shortcuts)

Goal of this guide: take `frontend/` from "default Vite starter" to "scaffold with Tailwind v4 + shadcn/ui + TanStack Router + Query + Form + Table, talking to the Django backend with cookies + CSRF, and a working route guard."

This is **Phase 2a only**. Phase 2b (auth pages), 2c (CV CRUD) and 2d (LLM connector UI) come after the bones are solid.

Run every command from `frontend/` unless the step says otherwise. Backend should be running on `http://localhost:8000` (`python manage.py runserver` from `backend/`). Frontend dev server is `http://localhost:5173`.

If a step's "verify" check fails, stop and fix before moving on. Each step builds on the previous.

---

## 0. Preflight

```bash
node --version   # want 20+ (22 LTS is fine)
npm --version
```

Make sure the backend is alive:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/docs/
# expect 200
```

If you don't get 200, start the backend first (`cd backend && python manage.py runserver`).

---

## 1. Wipe the default Vite starter

You're going to throw away everything React-y in `src/` except `main.tsx`, and rebuild from a clean slate.

```bash
cd frontend
rm src/App.tsx src/App.css src/index.css
rm -rf src/assets
rm public/icons.svg public/favicon.svg 2>/dev/null
```

Empty `src/main.tsx` for now — we'll fill it once Tailwind + Router are in. Replace its contents with a temporary stub:

```tsx
// src/main.tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <div>scaffolding…</div>
  </StrictMode>,
);
```

Sanity check:

```bash
npm run dev
```

Open http://localhost:5173 — you should see the literal word "scaffolding…". Kill the dev server (Ctrl-C) once you've seen it.

---

## 2. TypeScript path alias `@/*` → `src/*`

shadcn assumes this alias. Set it up before installing shadcn so its codemods land correctly.

Edit `tsconfig.json` (the root one) — add `compilerOptions` with `paths`:

```json
{
  "files": [],
  "references": [
    { "path": "./tsconfig.app.json" },
    { "path": "./tsconfig.node.json" }
  ],
  "compilerOptions": {
    "paths": {
      "@/*": ["./src/*"]
    }
  }
}
```

Edit `tsconfig.app.json` — add the same `paths` inside `compilerOptions`:

```json
"paths": {
  "@/*": ["./src/*"]
}
```

(`baseUrl` is deprecated in TypeScript 6.0 — TS5101 — and stops working in 7.0. `paths` entries have never required it; when they're relative like `./src/*` they resolve from the `tsconfig.json` directly.)

Tell Vite about the alias too. Edit `vite.config.ts`:

```ts
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
```

Docs: <https://vite.dev/config/shared-options.html#resolve-alias>

Verify TypeScript still compiles:

```bash
npx tsc -b
# should exit 0 with no output
```

---

## 3. Tailwind v4

v4 ships as a Vite plugin — _very_ different from v3 (no PostCSS config, no big `tailwind.config.js`).

```bash
npm install -D tailwindcss @tailwindcss/vite
```

Wire the Vite plugin in `vite.config.ts`:

```ts
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
});
```

Create `src/index.css` with just:

```css
@import "tailwindcss";
```

Import it from `src/main.tsx` (add the line at the top):

```tsx
import "@/index.css";
```

Replace the stub inside `<StrictMode>` with a Tailwind-using element so you can see if the plugin is wired:

```tsx
<div className="m-8 text-3xl font-bold text-emerald-600">tailwind works</div>
```

Docs: <https://tailwindcss.com/docs/installation/using-vite>

Verify:

```bash
npm run dev
```

You should see chunky emerald "tailwind works". Kill the dev server.

---

## 4. shadcn/ui init + base components

shadcn isn't a npm package — it's a CLI that copies components into your repo. You own the code, you edit it freely.

```bash
npx shadcn@latest init
```

Answers:

- **Component library**: `Radix` (the long-standing default; pick `Base` only if you specifically want Base UI primitives — once chosen, don't switch later)
- **Base color**: pick whatever (`zinc` is a safe default)

(CSS variables are enabled by default in the current CLI — no prompt. The old `Style` prompt — `default` vs `new-york` — has also been removed.)

This writes `components.json`, edits `src/index.css` to add the design tokens, and creates `src/lib/utils.ts` (the `cn()` className merger).

Now add the base set called out in the roadmap:

```bash
npx shadcn@latest add button input label card dialog dropdown-menu form table select textarea sonner
```

(`sonner` is the modern shadcn toast — `toast` was deprecated in favour of it.)

Each component lands in `src/components/ui/`. Open one, e.g. `src/components/ui/button.tsx`, and confirm it imports from `@/lib/utils` — that confirms the path alias works end-to-end.

Smoke-test a real component in `src/main.tsx`:

```tsx
import "@/index.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Button } from "@/components/ui/button";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <div className="p-8">
      <Button>click me</Button>
    </div>
  </StrictMode>,
);
```

Docs: <https://ui.shadcn.com/docs/installation/vite>

`npm run dev` → you should see a properly styled button. Kill it.

---

## 5. Vite dev proxy (keep cookies same-origin)

The whole point of the proxy is that the browser thinks both the SPA and the API live at `localhost:5173`. That makes the `csrftoken` cookie and Django's session cookie work without CORS preflights or `SameSite=None` workarounds.

Update `vite.config.ts`:

```ts
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const BACKEND = "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    proxy: {
      "/api": BACKEND,
      "/_allauth": BACKEND,
      "/admin": BACKEND,
      "/media": BACKEND,
      "/static": BACKEND,
    },
  },
});
```

Docs: <https://vite.dev/config/server-options.html#server-proxy>

Verify (with backend running):

```bash
npm run dev
# in another shell:
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5173/api/docs/
# expect 200
```

If you get 502/connection refused, the backend isn't up. If 404, the proxy path is wrong.

---

## 6. TanStack Query

Single source of truth for server state, caching, and request deduplication.

```bash
npm install @tanstack/react-query
npm install -D @tanstack/react-query-devtools
```

Create `src/lib/query.ts`:

```ts
import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});
```

We'll wire the provider in step 8 (after Router is in place). Docs: <https://tanstack.com/query/latest/docs/framework/react/quick-start>

---

## 7. TanStack Router (file-based routes with the Vite plugin)

```bash
npm install @tanstack/react-router
npm install -D @tanstack/router-plugin @tanstack/router-devtools
```

Add the router plugin to `vite.config.ts` (**must come before** `react()`):

```ts
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";

const BACKEND = "http://localhost:8000";

export default defineConfig({
  plugins: [
    tanstackRouter({ target: "react", autoCodeSplitting: true }),
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    proxy: {
      "/api": BACKEND,
      "/_allauth": BACKEND,
      "/admin": BACKEND,
      "/media": BACKEND,
      "/static": BACKEND,
    },
  },
});
```

Create the root route at `src/routes/__root.tsx`:

```tsx
import { Outlet, createRootRoute } from "@tanstack/react-router";

export const Route = createRootRoute({
  component: () => (
    <div className="min-h-screen">
      <Outlet />
    </div>
  ),
});
```

Create the index route at `src/routes/index.tsx`:

```tsx
import { createFileRoute } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/")({
  component: Home,
});

function Home() {
  return (
    <div className="p-8 space-y-4">
      <h1 className="text-2xl font-bold">lukehirsch</h1>
      <Button>hello</Button>
    </div>
  );
}
```

Docs: <https://tanstack.com/router/latest/docs/framework/react/quick-start> and <https://tanstack.com/router/latest/docs/framework/react/routing/file-based-routing>

The plugin will auto-generate `src/routeTree.gen.ts` next time you run `npm run dev`. Don't edit that file by hand — it's regenerated.

---

## 8. Wire Router + Query in `main.tsx`

Replace `src/main.tsx` completely:

```tsx
import "@/index.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { queryClient } from "@/lib/query";
import { routeTree } from "./routeTree.gen";

const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
```

Verify:

```bash
npm run dev
```

http://localhost:5173 should render the same "lukehirsch + button" page, but now under the router. `src/routeTree.gen.ts` should exist after the first run.

---

## 9. API fetch wrapper (cookies + CSRF + typed errors)

Django's CSRF middleware:

- sets a `csrftoken` cookie on any GET that hits a view (we'll lean on `GET /_allauth/browser/v1/auth/session` for the first one)
- expects an `X-CSRFToken` header matching that cookie on all unsafe methods (`POST`, `PUT`, `PATCH`, `DELETE`)

Create `src/lib/api.ts`:

```ts
function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp("(^|; )" + name + "=([^;]+)"));
  return match ? decodeURIComponent(match[2]) : null;
}

export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(status: number, data: unknown, message?: string) {
    super(message ?? `HTTP ${status}`);
    this.status = status;
    this.data = data;
  }
}

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);

  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (UNSAFE.has(method)) {
    const token = readCookie("csrftoken");
    if (token) headers.set("X-CSRFToken", token);
  }

  const res = await fetch(path, {
    ...init,
    method,
    headers,
    credentials: "same-origin",
  });

  const isJson = res.headers.get("content-type")?.includes("application/json");
  const body = isJson ? await res.json().catch(() => null) : await res.text();

  if (!res.ok) throw new ApiError(res.status, body);
  return body as T;
}
```

---

## 10. Auth session hook (`useAuth`)

allauth's headless session endpoint tells you whether the visitor is anonymous, half-authenticated (MFA pending), or fully authenticated.

Docs: <https://docs.allauth.org/en/latest/headless/openapi-specification/> — look at `GET /_allauth/browser/v1/auth/session`.

Create `src/lib/auth.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";

export type AuthStatus = "anonymous" | "authenticated" | "mfa_required";

type SessionResponse = {
  status: number;
  data?: { user?: { id: number; email: string } };
  meta?: { is_authenticated?: boolean };
};

export function useAuth() {
  const query = useQuery({
    queryKey: ["auth", "session"],
    queryFn: async () => {
      try {
        return await api<SessionResponse>("/_allauth/browser/v1/auth/session");
      } catch (e) {
        if (e instanceof ApiError && (e.status === 401 || e.status === 410)) {
          return e.data as SessionResponse;
        }
        throw e;
      }
    },
    staleTime: 30_000,
  });

  const data = query.data;
  let status: AuthStatus = "anonymous";
  if (data?.meta?.is_authenticated) status = "authenticated";
  else if (data?.status === 401 && data?.data?.user) status = "mfa_required";

  return { ...query, status, user: data?.data?.user };
}
```

The 401-with-user shape is how allauth signals "we know who you are but you still owe us an MFA factor."

---

## 11. `<RequireAuth>` route guard

TanStack Router's `beforeLoad` is the right hook — it runs before the component mounts, so you can redirect without flashing protected content.

Create `src/routes/_authenticated.tsx` (a pathless layout route — the `_` prefix means "no URL segment"). The fresh docs ([authenticated-routes](https://tanstack.com/router/latest/docs/framework/react/guide/authenticated-routes)) call for three things our older draft was missing:

- `beforeLoad({ location })` — grab the intended URL so login can bounce the user back via `search: { redirect: location.href }`.
- `isRedirect(error)` — re-throw intentional `redirect()`s and only convert _real_ failures (network errors, etc.) into a login redirect. Brittle `instanceof` checks against your own `ApiError` go away.
- **No `component`** — pathless layout routes render `<Outlet />` automatically, so don't define one unless you need a chrome wrapper.

```tsx
import { createFileRoute, redirect, isRedirect } from "@tanstack/react-router";
import { api } from "@/lib/api";

type Session = { meta?: { is_authenticated?: boolean } };

export const Route = createFileRoute("/_authenticated")({
  beforeLoad: async ({ location }) => {
    try {
      const session = await api<Session>("/_allauth/browser/v1/auth/session");
      if (!session.meta?.is_authenticated) {
        throw redirect({
          to: "/auth/login",
          search: { redirect: location.href },
        });
      }
    } catch (error) {
      if (isRedirect(error)) throw error;
      throw redirect({
        to: "/auth/login",
        search: { redirect: location.href },
      });
    }
  },
});
```

> Note on `createFileRoute('/_authenticated')(...)`: the path-string form is still the current API on `@tanstack/react-router` ≥ 1.170. You may see object-only `createFileRoute({...})` examples in TanStack Start docs — that's a Start-only convenience and not what we use here.

Add a stub login route at `src/routes/auth/login.tsx`. It declares a `validateSearch` so the `?redirect=...` param coming from `_authenticated.tsx` is typed (and so the guard's `search: { redirect: ... }` type-checks against this route):

```tsx
import { createFileRoute } from "@tanstack/react-router";

type LoginSearch = { redirect?: string };

export const Route = createFileRoute("/auth/login")({
  validateSearch: (search: Record<string, unknown>): LoginSearch => ({
    redirect: typeof search.redirect === "string" ? search.redirect : undefined,
  }),
  component: () => (
    <div className="p-8">
      <h1 className="text-2xl">login (stub — Phase 2b builds this)</h1>
    </div>
  ),
});
```

> The TS errors in `_authenticated.tsx` (`'/auth/login' is not assignable to '/' | '.' | '..'` and `'redirect' does not exist`) disappear once this stub exists and the router plugin regenerates `routeTree.gen.ts` — that happens automatically on the next `npm run dev` save, or you can run `npx tsr generate` to force it.

Add a protected stub at `src/routes/_authenticated/account.tsx`:

```tsx
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

type Profile = { display_name: string; bio: string; timezone: string };

export const Route = createFileRoute("/_authenticated/account")({
  component: Account,
});

function Account() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["profile"],
    queryFn: () => api<Profile>("/api/spa/profile/"),
  });

  if (isLoading) return <div className="p-8">loading…</div>;
  if (error) return <div className="p-8">error: {String(error)}</div>;
  return <pre className="p-8 text-sm">{JSON.stringify(data, null, 2)}</pre>;
}
```

---

## 12. End-to-end verification

Backend running on 8000, frontend running on 5173. In a private/incognito window:

1. Visit `http://localhost:5173/` → home page renders.
2. Visit `http://localhost:5173/account` → should redirect to `/auth/login` (the stub).
3. Open `http://localhost:8000/admin/`, log in as a superuser, then back to `http://localhost:5173/account` → should now render your `UserProfile` JSON (because the session cookie is shared via the same `localhost` host).
4. Open DevTools → Application → Cookies for `localhost:5173`. You should see both `sessionid` and `csrftoken`.
5. From DevTools console:
   ```js
   await fetch("/api/spa/profile/", {
     method: "PATCH",
     credentials: "same-origin",
     headers: {
       "Content-Type": "application/json",
       "X-CSRFToken": document.cookie.match(/csrftoken=([^;]+)/)[1],
     },
     body: JSON.stringify({ display_name: "test from console" }),
   }).then((r) => r.json());
   ```
   → returns the updated profile. If you get a 403, CSRF is broken (re-check the proxy + that you're using `credentials: "same-origin"`).

If all five pass, Phase 2a is done.

---

## 13. Install remaining libs the roadmap calls for

These don't need wiring yet — they'll be pulled in by Phase 2b/2c/2d. Install them now so `package.json` matches the roadmap:

```bash
npm install @tanstack/react-form @tanstack/react-table zod qrcode.react lucide-react
```

Docs:

- TanStack Form: <https://tanstack.com/form/latest/docs/framework/react/quick-start>
- TanStack Table: <https://tanstack.com/table/latest/docs/framework/react/guide/installation>
- Zod: <https://zod.dev>
- lucide-react: <https://lucide.dev/guide/packages/lucide-react>
- qrcode.react: <https://github.com/zpao/qrcode.react>

---

## What you should have at the endd

```
frontend/
├── components.json
├── package.json                  # all roadmap deps installed
├── tsconfig.json                 # @/* alias
├── tsconfig.app.json             # @/* alias
├── vite.config.ts                # tanstackRouter + react + tailwindcss plugins + proxy + alias
└── src/
    ├── components/ui/            # shadcn primitives (button, input, …, sonner)
    ├── index.css                 # @import "tailwindcss" + shadcn tokens
    ├── lib/
    │   ├── api.ts                # fetch wrapper with CSRF
    │   ├── auth.ts               # useAuth() against /_allauth/.../auth/session
    │   ├── query.ts              # shared QueryClient
    │   └── utils.ts              # cn() (shadcn)
    ├── main.tsx                  # QueryClientProvider + RouterProvider
    ├── routeTree.gen.ts          # generated, do not edit
    └── routes/
        ├── __root.tsx
        ├── index.tsx
        ├── _authenticated.tsx    # session guard
        ├── _authenticated/
        │   └── account.tsx       # reads /api/spa/profile/
        └── auth/
            └── login.tsx         # stub for Phase 2b
```

Commit checkpoint suggestion:

```bash
git add frontend/ .claude/plans/phase-2a-setup-guide.md
git commit -m "Phase 2a: frontend foundation (TanStack + Tailwind v4 + shadcn + auth guard)"
```

---

## What's next (do NOT start until 2a is committed and green)

- **2b** — implement all six `/auth/*` flows + `/account/*` pages against the headless allauth endpoints.
- **2c** — six `/cv/*` list+editor pages backed by `/api/jac/`.
- **2d** — `/settings/llm` + `/settings/llm/usage` backed by `/api/llm/`.

Each has its own subsurface of the allauth + DRF API that's worth walking through once you've felt the scaffolding land under your fingers.
