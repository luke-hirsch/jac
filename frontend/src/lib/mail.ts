/**
 * The email an application goes out with, for the user to send by hand: jac doesn't send mail
 * for them. The application page copies the parts or opens them in the mail app as a `mailto:`
 * link; the PDF is attached there, since a link can't carry a file.
 */
import type { ApplicationRow } from "@/lib/queries/applications";

export type MailDraft = { to: string; subject: string; body: string };

/** Recipient from the posting's address, subject from the letter, the body Claude wrote (or the
 *  user). Each is "" when the application doesn't have it. */
export function mailDraft(app: ApplicationRow): MailDraft {
  return {
    to: app.posting_detail.address?.email ?? "",
    subject: app.letter_meta.subject ?? "",
    body: app.email_body,
  };
}

/** Percent-encoding for a mailto field (RFC 6068): spaces as %20, never "+", which mail apps
 *  show as a plus; line breaks as CRLF. */
function field(text: string): string {
  return encodeURIComponent(text.replace(/\r?\n/g, "\r\n"));
}

/** A `mailto:` link that opens the mail app with everything filled in. Empty parts are left out.
 *  The address keeps its "@" readable; anything else that could end the field is encoded. */
export function mailtoHref({ to, subject, body }: MailDraft): string {
  const address = encodeURIComponent(to.trim()).replace(/%40/g, "@");
  const query = [
    subject && `subject=${field(subject)}`,
    body && `body=${field(body)}`,
  ].filter(Boolean);
  return `mailto:${address}${query.length ? `?${query.join("&")}` : ""}`;
}
