/** host from the hostname + the base domain.
 *  localStorage is per-origin, so a handle host's stamp is naturally that owner's alone. */
export type SiteHost =
  | { kind: "apex" }
  | { kind: "app" }
  | { kind: "handle"; handle: string };

/** The domain this deployment runs on. It's read at run time, so one image serves any domain: nginx
 *  fills a meta tag in (deploy/image/spa.inc). In `vite dev` the placeholder is still there. */
export function baseDomain(): string {
  const fromPage = globalThis.document
    ?.querySelector?.('meta[name="jac-base-domain"]')
    ?.getAttribute("content");
  return fromPage && !fromPage.startsWith("__") ? fromPage : "localhost";
}

export function parseHost(
  hostname: string,
  base: string = baseDomain(),
): SiteHost {
  const h = hostname.toLowerCase().replace(/\.$/, "");
  if (h === base || h === `www.${base}`) return { kind: "apex" };
  const suffix = `.${base}`;
  if (!h.endsWith(suffix)) return { kind: "apex" }; // unknown host → safe apex default
  const sub = h.slice(0, -suffix.length);
  if (sub === "app") return { kind: "app" };
  if (!sub || sub.includes(".")) return { kind: "apex" };
  return { kind: "handle", handle: sub };
}

export function siteHost(): SiteHost {
  return parseHost(window.location.hostname);
}

export function currentHandle(): string | null {
  const h = siteHost();
  return h.kind === "handle" ? h.handle : null;
}

/** The app host's origin, preserving scheme + port (dev: http://app.localhost:5173). */
export function appOrigin(base: string = baseDomain()): string {
  const { protocol, port } = window.location;
  const p = port ? `:${port}` : "";
  return `${protocol}//app.${base}${p}`;
}

/** The public landing page. On the app host "/" only ever leads back into the auth pages. */
export function apexOrigin(base: string = baseDomain()): string {
  const { protocol, port } = window.location;
  const p = port ? `:${port}` : "";
  return `${protocol}//${base}${p}`;
}
