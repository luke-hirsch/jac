/** What the owner's publish switch says. Pure, so the promise it makes is testable. */

export type PublishState = {
  handle: string;
  portfolio_public: boolean;
  portfolio_always_public: boolean;
};

export function portfolioAddress(handle: string, base: string): string {
  return `${handle}.${base}`;
}

export function isLive(p: PublishState): boolean {
  return p.portfolio_public || p.portfolio_always_public;
}

export function publishNotice(p: PublishState, base: string): string {
  const at = portfolioAddress(p.handle, base);
  if (p.portfolio_always_public) {
    return `This is the site's own portfolio, so it is always public, at ${base} and ${at}.`;
  }
  if (p.portfolio_public) {
    return `Published: anyone can read your career entries and blocks at ${at}, without a login.`;
  }
  return (
    `Not published: nobody can read your portfolio at ${at}, it looks like no such portfolio exists. ` +
    "A link you share, such as an application's QR code, still works and shows only that link's own selection."
  );
}
