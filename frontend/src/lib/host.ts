/** host from the hostname + VITE_BASE_DOMAIN.
 *  localStorage is per-origin, so a handle host's stamp is naturally that owner's alone. */
export type SiteHost =
  | { kind: "apex" }
  | { kind: "app" }
  | { kind: "handle"; handle: string };

const BASE =
  (import.meta.env.VITE_BASE_DOMAIN as string | undefined) ?? "localhost";

export function parseHost(hostname: string, base: string = BASE): SiteHost {
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
export function appOrigin(base: string = BASE): string {
  const { protocol, port } = window.location;
  const p = port ? `:${port}` : "";
  return `${protocol}//app.${base}${p}`;
}
