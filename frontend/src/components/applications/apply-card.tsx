import { useState } from "react";
import { toast } from "sonner";
import { ExternalLink, Mail } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { reseedField, storedText } from "@/lib/draft-merge";
import { drfFieldError } from "@/lib/field-save";
import { mailDraft, mailtoHref } from "@/lib/mail";
import {
  useUpdateApplication,
  type ApplicationRow,
} from "@/lib/queries/applications";

const FIELDS = [
  "apply_url",
  "apply_note",
  "salary_note",
  "email_body",
] as const;
type Field = (typeof FIELDS)[number];
type Drafts = Record<Field, string>;

function serverCopy(app: ApplicationRow): Drafts {
  return {
    apply_url: app.apply_url,
    apply_note: app.apply_note,
    salary_note: app.salary_note,
    email_body: app.email_body,
  };
}

function copy(text: string, what: string) {
  navigator.clipboard.writeText(text).then(
    () => toast.success(`${what} copied`),
    () => toast.error(`Could not copy the ${what.toLowerCase()}`),
  );
}

/** Where the application goes and the email it goes with. jac doesn't send it: the user copies
 *  the parts, or opens them in the mail app and attaches the exported PDF there. */
export function ApplyCard({ app }: { app: ApplicationRow }) {
  const update = useUpdateApplication();
  const server = serverCopy(app);
  const [draft, setDraft] = useState<Drafts>(server);

  // Claude can save these while the page is open: a field follows the server unless it has
  // unsaved typing of its own (the content drafts' rule).
  const [prev, setPrev] = useState<Drafts>(server);
  if (FIELDS.some((f) => prev[f] !== server[f])) {
    const next = { ...draft };
    for (const f of FIELDS) {
      if (reseedField(prev[f], server[f], draft[f]) === "take")
        next[f] = server[f];
    }
    setPrev(server);
    setDraft(next);
  }

  const dirty = FIELDS.filter((f) => storedText(draft[f]) !== server[f]);
  const set = (f: Field) => (value: string) =>
    setDraft((d) => ({ ...d, [f]: value }));

  function save() {
    const body = Object.fromEntries(
      dirty.map((f) => [f, storedText(draft[f])]),
    );
    update.mutate(
      { id: app.id, body },
      {
        onSuccess: () => toast.success("Saved"),
        onError: (err) =>
          toast.error(
            dirty
              .map((f) => drfFieldError(err, f))
              .find((m) => m !== "Save failed") ?? "Could not save",
          ),
      },
    );
  }

  const mail = { ...mailDraft(app), body: draft.email_body };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Applying</CardTitle>
        <CardDescription>
          Claude fills these in with the application, and you can change them
          here. Download the PDF from the export and attach it in your mail app.
        </CardDescription>
      </CardHeader>
      <CardContent className="min-w-0 space-y-5">
        <div className="space-y-1">
          <Label htmlFor="apply-url">Portal or form</Label>
          <Input
            id="apply-url"
            type="url"
            inputMode="url"
            placeholder="https://… (empty when it goes by email)"
            value={draft.apply_url}
            onChange={(e) => set("apply_url")(e.target.value)}
          />
          {server.apply_url && (
            <Button asChild variant="outline" size="sm" className="mt-1">
              <a href={server.apply_url} target="_blank" rel="noreferrer">
                <ExternalLink /> Open the form
              </a>
            </Button>
          )}
        </div>

        <div className="space-y-1">
          <Label htmlFor="apply-note">How to apply</Label>
          <Textarea
            id="apply-note"
            rows={2}
            placeholder="Reference number, documents wanted, one PDF…"
            value={draft.apply_note}
            onChange={(e) => set("apply_note")(e.target.value)}
          />
        </div>

        <div className="space-y-1">
          <Label htmlFor="salary-note">Salary</Label>
          <Textarea
            id="salary-note"
            rows={2}
            placeholder="What to ask for and what it rests on. Ask Claude for an estimate."
            value={draft.salary_note}
            onChange={(e) => set("salary_note")(e.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            Only for you: no export or preview prints it.
          </p>
        </div>

        <div className="space-y-3 border-t pt-4">
          <dl className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 text-sm">
            <dt className="text-muted-foreground">To</dt>
            <dd className="break-all">
              {mail.to || (
                <span className="text-muted-foreground">
                  no address in the posting
                </span>
              )}
            </dd>
            <dd>
              {mail.to && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => copy(mail.to, "Address")}
                >
                  Copy
                </Button>
              )}
            </dd>
            <dt className="text-muted-foreground">Subject</dt>
            <dd className="break-words">
              {mail.subject || (
                <span className="text-muted-foreground">no letter yet</span>
              )}
            </dd>
            <dd>
              {mail.subject && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => copy(mail.subject, "Subject")}
                >
                  Copy
                </Button>
              )}
            </dd>
          </dl>

          <div className="space-y-1">
            <Label htmlFor="email-body">Email text</Label>
            <Textarea
              id="email-body"
              rows={8}
              placeholder="Ask Claude for the email with the application, or write it here."
              value={draft.email_body}
              onChange={(e) => set("email_body")(e.target.value)}
            />
          </div>

          <div className="flex flex-wrap gap-2">
            <Button asChild size="sm">
              <a href={mailtoHref(mail)}>
                <Mail /> Open in mail app
              </a>
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!draft.email_body.trim()}
              onClick={() => copy(storedText(draft.email_body), "Email text")}
            >
              Copy text
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="ml-auto"
              disabled={update.isPending || dirty.length === 0}
              onClick={save}
            >
              Save
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
