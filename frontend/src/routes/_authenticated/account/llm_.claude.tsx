import { useRef, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { toast } from "sonner";
import { QueryError } from "@/components/query-error";
import { TransferConsent } from "@/components/transfer-consent";
import { useImportChangesFile } from "@/components/skill/use-import-changes-file";
import { Lines } from "@/components/toast-lines";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  changeRows,
  connectorHeader,
  connectorUrl,
  envLines,
  errorLines,
  parseImportFile,
  staleLines,
  useAccessTokens,
  useCareerChanges,
  useCreateAccessToken,
  useDownloadCareerFile,
  useDownloadSkill,
  useRevokeAccessToken,
  useUndoCareerChange,
  type AccessTokenRow,
  type CareerChange,
  type CareerChangeBatch,
  type NewAccessToken,
} from "@/lib/queries/skill";

export const Route = createFileRoute("/_authenticated/account/llm_/claude")({
  component: ClaudePage,
});

/** Where Anthropic says what it does with the data of a Claude account. */
const CLAUDE_TERMS = [
  {
    label: "Consumer Terms",
    href: "https://www.anthropic.com/legal/consumer-terms",
  },
  {
    label: "Commercial Terms",
    href: "https://www.anthropic.com/legal/commercial-terms",
  },
  { label: "Privacy Policy", href: "https://www.anthropic.com/legal/privacy" },
];

function ClaudePage() {
  // Consent is stamped on each token as it's created; this only unlocks creating one for this
  // visit, as the API key's card does on the LLM providers page.
  const [agreed, setAgreed] = useState(false);
  return (
    <div className="space-y-8">
      <section className="space-y-4">
        <div>
          <h2 className="text-lg font-medium">Claude</h2>
          <p className="text-sm text-muted-foreground">
            Write applications with Claude, on your own subscription: paste a
            posting into a chat, talk the CV and the letter through, and Claude
            saves them here, where you check the layout and export the PDF.
            jac's own AI isn't used for any of it. Claude reaches your career
            and your applications, never your account or your API keys.
          </p>
        </div>
        {!agreed && <ClaudeConsent onAgree={() => setAgreed(true)} />}
        <ConnectorSteps />
        <CreateToken agreed={agreed} />
      </section>
      <Separator />
      <FileMode />
      <Separator />
      <CareerChanges />
      <Separator />
      <TokenList />
    </div>
  );
}

/** The consent card for a token: the data goes to Anthropic through the user's Claude account,
 *  the same transfer as an API key. The server stamps it on the token; revoking withdraws it. */
function ClaudeConsent({ onAgree }: { onAgree: () => void }) {
  return (
    <TransferConsent
      consent="I agree that the data above goes to Anthropic through every token I create, also outside the EU. I can withdraw by revoking the token."
      onAgree={onAgree}
    >
      <p>
        With a token, Claude reads your career, your contact details, your
        personality answers and writing sample, and your applications from jac,
        and saves applications and career changes here. That data goes to
        Anthropic, under your Claude account. Anthropic may process it in the
        USA, where data protection can be weaker than in the EU and authorities
        may be able to access it.
      </p>
      <p>
        What Anthropic does with it is set by your Claude account's terms and
        privacy settings (
        {CLAUDE_TERMS.map((t, i) => (
          <span key={t.href}>
            {i > 0 && ", "}
            <a
              href={t.href}
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              {t.label}
            </a>
          </span>
        ))}
        ), and we have no influence on that. A career file you download and give
        Claude yourself is your own transfer: jac sends nothing then.
      </p>
    </TransferConsent>
  );
}

/** The jac connector: Claude's servers call jac's MCP endpoint, so the app on the web, the
 *  desktop and the phone reaches jac even though its code sandbox can't. */
function ConnectorSteps() {
  const url = connectorUrl(window.location.origin);
  return (
    <div className="space-y-2">
      <h3 className="font-medium">In the Claude app: the jac connector</h3>
      <ol className="list-decimal space-y-1 pl-5 text-sm">
        <li>
          Create a token below, e.g. <q>Claude connector</q>.
        </li>
        <li>
          In Claude: Customize → Connectors → + Add → Add custom connector. Name{" "}
          <code>jac</code>, URL <code className="break-all">{url}</code>,
          authentication <q>No sign in</q>, and under Request headers{" "}
          <code>authorization</code> with the value shown with your token.
        </li>
        <li>
          No Request headers in that dialog? Use the URL with your token in it
          instead, and no header.
        </li>
        <li>
          In a chat, switch jac on (+ → Connectors) and paste a job posting.
          Claude reads your career, saves when you say so, and its link opens
          the application here.
        </li>
      </ol>
      <p className="text-sm text-muted-foreground">
        Claude Code takes the same token, as <code>JAC_URL</code> and{" "}
        <code>JAC_TOKEN</code> in the shell you start it from, with the skill
        from the zip below.
      </p>
    </div>
  );
}

/** Without the connector, the Claude app can't reach jac: the career and the results go by
 *  file. */
function FileMode() {
  return (
    <section className="space-y-4">
      <div>
        <h3 className="font-medium">Without the connector: by file</h3>
        <p className="text-sm text-muted-foreground">
          The skill alone can't reach jac from the Claude app, so the career
          goes into the chat as a file and Claude's results come back as files
          you import.
        </p>
      </div>
      <SetupSteps />
      <div className="space-y-3">
        <DownloadSkill />
        <DownloadCareerFile />
      </div>
    </section>
  );
}

function DownloadSkill() {
  const download = useDownloadSkill();
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button
        disabled={download.isPending}
        onClick={() =>
          download.mutate(undefined, {
            onError: (e) => toast.error(errorLines(e, "Download failed.")[0]),
          })
        }
      >
        {download.isPending ? "Preparing…" : "Download skill (.zip)"}
      </Button>
      <p className="max-w-sm text-xs text-muted-foreground">
        Once: the skill alone, nothing about you in it. Upload it again only
        when jac's skill changes.
      </p>
    </div>
  );
}

function DownloadCareerFile() {
  const download = useDownloadCareerFile();
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button
        variant="outline"
        disabled={download.isPending}
        onClick={() =>
          download.mutate(undefined, {
            onError: (e) => toast.error(errorLines(e, "Download failed.")[0]),
          })
        }
      >
        {download.isPending ? "Preparing…" : "Download career for Claude"}
      </Button>
      <p className="max-w-sm text-xs text-muted-foreground">
        For each chat: your career and its review as of now, with your contact
        details for the letter. Download a fresh one after you change your
        career here.
      </p>
    </div>
  );
}

function SetupSteps() {
  return (
    <ol className="list-decimal space-y-1 pl-5 text-sm">
      <li>
        Once: download the skill and upload <code>jac-skill.zip</code> in
        Claude, under Settings → Capabilities → Skills.
      </li>
      <li>
        For each chat: download your career for Claude, attach the file, and
        paste a job posting (or its link).
      </li>
      <li>
        Claude hands you a file to import: an application under{" "}
        <Link to="/applications" className="underline">
          Applications → Import from Claude
        </Link>
        , career changes under Import changes, below.
      </li>
    </ol>
  );
}

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** The batches of career edits Claude applied or the user imported, newest first, each one
 *  undoable. */
function CareerChanges() {
  const batches = useCareerChanges();
  const rows = batches.data ?? [];
  return (
    <section className="space-y-3">
      <div>
        <h3 className="font-medium">Career changes</h3>
        <p className="text-sm text-muted-foreground">
          Claude can review your career and, once you agree, edit entries:
          descriptions, the skills a job used, skills that are missing. Every
          batch of changes is listed here and can be undone. If Claude handed
          you a <code>career-changes.json</code>, import it here.
        </p>
      </div>
      <ImportChanges />
      {rows.length ? (
        <ul className="divide-y">
          {rows.map((batch) => (
            <BatchItem key={batch.id} batch={batch} />
          ))}
        </ul>
      ) : batches.isError ? (
        <QueryError
          error={batches.error}
          what="your career changes"
          onRetry={() => batches.refetch()}
          retrying={batches.isFetching}
        />
      ) : (
        <p className="text-sm text-muted-foreground">
          {batches.isLoading ? "Loading…" : "No changes yet."}
        </p>
      )}
    </section>
  );
}

/** Import the changes Claude wrote as a file, when its sandbox couldn't reach jac. */
function ImportChanges() {
  const importChanges = useImportChangesFile();
  const fileRef = useRef<HTMLInputElement>(null);

  async function onFile(file: File) {
    let parsed;
    try {
      parsed = parseImportFile(await file.text());
    } catch (e) {
      toast.error((e as Error).message);
      return;
    }
    if (parsed.kind === "application")
      toast.error(
        "That's an application: import it under Applications → Import from Claude.",
      );
    else await importChanges.apply(parsed.changes);
  }

  return (
    <>
      <input
        ref={fileRef}
        type="file"
        accept=".json,application/json"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = ""; // the same file again is a new pick
          if (file) onFile(file);
        }}
      />
      <Button
        variant="outline"
        disabled={importChanges.isPending}
        onClick={() => fileRef.current?.click()}
      >
        {importChanges.isPending ? "Importing…" : "Import changes"}
      </Button>
    </>
  );
}

function BatchItem({ batch }: { batch: CareerChangeBatch }) {
  const undo = useUndoCareerChange();
  const n = batch.changes.length;
  const count = `${n} change${n === 1 ? "" : "s"}`;

  function onUndo() {
    if (!confirm(`Undo ${count}? The entries go back to how they were.`))
      return;
    undo.mutate(batch.id, {
      onSuccess: () => toast.success("Changes undone"),
      onError: (e) =>
        toast.error("Could not undo", {
          description: (
            <Lines lines={staleLines(e, "Try again in a moment.", batch)} />
          ),
          duration: 10_000,
        }),
    });
  }

  return (
    <li className="space-y-2 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm">
          <span className="font-medium">{when(batch.created_at)}</span>
          <span className="text-muted-foreground">
            {" "}
            · {batch.source === "claude" ? "by Claude" : "imported"} · {count}
          </span>
        </p>
        {batch.undone_at ? (
          <span className="text-xs text-muted-foreground">
            undone {when(batch.undone_at)}
          </span>
        ) : (
          <Button
            variant="ghost"
            size="sm"
            disabled={undo.isPending}
            onClick={onUndo}
          >
            {undo.isPending ? "Undoing…" : "Undo"}
          </Button>
        )}
      </div>
      <ul className={batch.undone_at ? "space-y-1 opacity-60" : "space-y-1"}>
        {batch.changes.map((change, i) => (
          <ChangeItem
            key={`${change.id}-${i}`}
            change={change}
            labels={batch.labels}
          />
        ))}
      </ul>
    </li>
  );
}

function ChangeItem({
  change,
  labels,
}: {
  change: CareerChange;
  labels: Record<string, string>;
}) {
  const rows = changeRows(change, labels);
  // A new entry has no before, a removed one no after: one column says it all.
  const oneSide = change.before === null || change.after === null;
  return (
    <li>
      <details className="rounded border px-3 py-2">
        <summary className="cursor-pointer text-sm">
          <span className="font-medium">{change.label || change.id}</span>
          {change.before === null && (
            <Badge variant="secondary" className="ml-2">
              new
            </Badge>
          )}
          {change.after === null && (
            <Badge variant="outline" className="ml-2">
              removed
            </Badge>
          )}
          {change.why && (
            <span className="text-muted-foreground"> · {change.why}</span>
          )}
        </summary>
        <div className="mt-2 space-y-3">
          {rows.length ? (
            rows.map((row) => (
              <div key={row.field} className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground">
                  {row.field}
                </p>
                {row.kind === "list" ? (
                  <div className="text-sm">
                    {row.added.length > 0 && (
                      <p>added: {row.added.join(", ")}</p>
                    )}
                    {row.removed.length > 0 && (
                      <p>removed: {row.removed.join(", ")}</p>
                    )}
                  </div>
                ) : oneSide ? (
                  <FieldText text={row.after ?? row.before} />
                ) : (
                  <div className="grid gap-2 sm:grid-cols-2">
                    <FieldText caption="before" text={row.before} />
                    <FieldText caption="after" text={row.after} />
                  </div>
                )}
              </div>
            ))
          ) : (
            <p className="text-sm text-muted-foreground">Nothing to show.</p>
          )}
        </div>
      </details>
    </li>
  );
}

function FieldText({
  caption,
  text,
}: {
  caption?: string;
  text: string | null;
}) {
  return (
    <div className="min-w-0 rounded bg-muted px-2 py-1 text-sm">
      {caption && <p className="text-xs text-muted-foreground">{caption}</p>}
      {text === null ? (
        <p className="italic text-muted-foreground">empty</p>
      ) : (
        <p className="whitespace-pre-wrap break-words">{text}</p>
      )}
    </div>
  );
}

/** One token for the connector or Claude Code: the box shows it in the shapes each one takes.
 *  Locked until the user agreed to the transfer in the card above. */
function CreateToken({ agreed }: { agreed: boolean }) {
  const create = useCreateAccessToken();
  const [name, setName] = useState("");
  // Held for this visit only: dismissing the box forgets the key.
  const [minted, setMinted] = useState<NewAccessToken | null>(null);

  function onCreate() {
    create.mutate(
      { name: name.trim(), transferConsent: agreed },
      {
        onSuccess: (token) => {
          setMinted(token);
          setName("");
        },
        onError: (e) =>
          toast.error(errorLines(e, "Could not create the token.")[0]),
      },
    );
  }

  return minted ? (
    <NewTokenBox token={minted} onDismiss={() => setMinted(null)} />
  ) : (
    <div className="flex items-end gap-2">
      <div className="flex-1 space-y-1">
        <Label htmlFor="token-name">Token name</Label>
        <Input
          id="token-name"
          value={name}
          maxLength={60}
          disabled={!agreed}
          placeholder={agreed ? "e.g. Claude connector" : "Agree above first"}
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      <Button
        onClick={onCreate}
        disabled={!agreed || create.isPending || !name.trim()}
      >
        {create.isPending ? "Creating…" : "Create token"}
      </Button>
    </div>
  );
}

function NewTokenBox({
  token,
  onDismiss,
}: {
  token: NewAccessToken;
  onDismiss: () => void;
}) {
  const origin = window.location.origin;
  return (
    <div className="space-y-3 rounded-md border px-4 py-3">
      <p className="text-sm font-medium">
        Copy it now: the key isn't shown again.
      </p>
      <CopyBlock
        label="Connector: the authorization header's value"
        text={connectorHeader(token.key)}
      />
      <CopyBlock
        label="Connector without request headers: this URL instead"
        text={connectorUrl(origin, token.key)}
      />
      <CopyBlock
        label="Claude Code: in the shell you start it from"
        text={envLines(origin, token.key)}
      />
      <Button size="sm" variant="ghost" onClick={onDismiss}>
        Done
      </Button>
    </div>
  );
}

function CopyBlock({ label, text }: { label: string; text: string }) {
  return (
    <div className="space-y-1">
      <p className="text-xs text-muted-foreground">{label}</p>
      <div className="flex items-start gap-2">
        <pre className="min-w-0 flex-1 overflow-x-auto rounded-md bg-muted p-2 text-xs">
          {text}
        </pre>
        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            navigator.clipboard.writeText(text).then(
              () => toast.success("Copied"),
              () => toast.error("Copy failed: select the text instead."),
            )
          }
        >
          Copy
        </Button>
      </div>
    </div>
  );
}

function TokenList() {
  const tokens = useAccessTokens();
  const revoke = useRevokeAccessToken();
  const rows = tokens.data ?? [];

  function onRevoke(row: AccessTokenRow) {
    if (
      !confirm(
        `Revoke "${row.name || row.prefix}"? Whatever uses it stops working.`,
      )
    )
      return;
    revoke.mutate(row.id, {
      onSuccess: () => toast.success("Token revoked"),
      onError: () => toast.error("Revoke failed"),
    });
  }

  return (
    <section className="space-y-3">
      <h3 className="font-medium">Tokens</h3>
      {rows.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Key</TableHead>
              <TableHead>Created</TableHead>
              <TableHead>Last used</TableHead>
              <TableHead className="text-right" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.id}>
                <TableCell>
                  {row.name || (
                    <span className="text-muted-foreground">unnamed</span>
                  )}
                </TableCell>
                <TableCell className="font-mono text-xs">
                  {row.prefix}…
                </TableCell>
                <TableCell>
                  {new Date(row.created_at).toLocaleDateString()}
                </TableCell>
                <TableCell>
                  {row.last_used_at
                    ? new Date(row.last_used_at).toLocaleDateString()
                    : "never"}
                </TableCell>
                <TableCell className="text-right">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-destructive"
                    disabled={revoke.isPending}
                    onClick={() => onRevoke(row)}
                  >
                    Revoke
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : tokens.isError ? (
        <QueryError
          error={tokens.error}
          what="your tokens"
          onRetry={() => tokens.refetch()}
          retrying={tokens.isFetching}
        />
      ) : (
        <p className="text-sm text-muted-foreground">
          {tokens.isLoading
            ? "Loading…"
            : "No tokens yet. The connector and Claude Code each use one."}
        </p>
      )}
    </section>
  );
}
