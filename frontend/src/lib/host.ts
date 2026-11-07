/** Which host this SPA instance serves, from the hostname + the base domain. Three fixed hosts:
 *  the apex (Django's landing in prod; the SPA only sees it in vite dev), `jac.` (the authed app)
 *  and `portfolio.` (every owner's portfolio, at /<handle>/). An owner is a path segment, never a
 *  subdomain. */
export type SiteHost = "apex" | "jac" | "portfolio";

export const JAC_SUBDOMAIN = "jac";
export const PORTFOLIO_SUBDOMAIN = "portfolio";

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
  if (h === `${JAC_SUBDOMAIN}.${base}`) return "jac";
  if (h === `${PORTFOLIO_SUBDOMAIN}.${base}`) return "portfolio";
  return "apex"; // the apex, www., and any unknown host: the safe default
}

export function siteHost(): SiteHost {
  return parseHost(window.location.hostname);
}

/** An origin on this deployment, preserving scheme + port (dev: http://jac.localhost:5173). */
function originOf(host: string): string {
  const { protocol, port } = window.location;
  const p = port ? `:${port}` : "";
  return `${protocol}//${host}${p}`;
}

/** The authed app. */
export function jacOrigin(base: string = baseDomain()): string {
  return originOf(`${JAC_SUBDOMAIN}.${base}`);
}

/** The public front door: Django's landing. */
export function apexOrigin(base: string = baseDomain()): string {
  return originOf(base);
}

/** Every owner's portfolio, one path each. */
export function portfolioOrigin(base: string = baseDomain()): string {
  return originOf(`${PORTFOLIO_SUBDOMAIN}.${base}`);
}

/** Hand the browser to another host from a route's beforeLoad. The promise never settles, so the
 *  router stays put until the page unloads: a thrown redirect would run a second route's guard,
 *  and a second `location.replace` would win over this one. */
export function leaveTo(url: string): Promise<never> {
  window.location.replace(url);
  return new Promise<never>(() => {});
}
