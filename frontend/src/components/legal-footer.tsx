import { AppearanceSwitch } from "@/components/appearance-switch";

/** Imprint / privacy / terms on every page of every host. Plain <a>, not router <Link>:
 *  /legal/* is Django-rendered, so it needs a full navigation (nginx / the vite proxy). Where
 *  nobody is signed in (the portfolio host), the visitor's appearance switch sits beside them. */
const LINKS = [
  ["Impressum", "/legal/imprint/"],
  ["Datenschutz", "/legal/privacy/"],
  ["Nutzungsbedingungen", "/legal/terms/"],
] as const;

export function LegalFooter() {
  return (
    <footer className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1 border-t px-4 py-3 text-xs text-muted-foreground">
      {LINKS.map(([label, href]) => (
        <a key={href} href={href} className="hover:underline">
          {label}
        </a>
      ))}
      <AppearanceSwitch />
    </footer>
  );
}
