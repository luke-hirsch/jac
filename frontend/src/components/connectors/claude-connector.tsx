import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { toast } from "sonner";
import { QueryError } from "@/components/query-error";
import { ConsentKept, TransferConsent } from "@/components/transfer-consent";
import {
  consentGiven,
  useConsent,
  useGiveConsent,
  useWithdrawConsent,
} from "@/lib/queries/consent";
import { Lines } from "@/components/toast-lines";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
  errorLines,
  expandedBatch,
  staleLines,
  useAccessTokens,
  useCareerChanges,
  useCreateAccessToken,
  useDownloadSkill,
  useKeepAllCareerChanges,
  useKeepCareerChange,
  useRevokeAccessToken,
  useSetTokenPortfolio,
  useUndoCareerChange,
  type AccessTokenRow,
  type CareerChange,
  type CareerChangeBatch,
  type NewAccessToken,
} from "@/lib/queries/skill";
import { cn } from "@/lib/utils";

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

/** Claude's connector, in Account → LLM under Connectors: the consent, the steps, the skill,
 *  the tokens and the career edits Claude made. The connector is the only way Claude reaches
 *  jac; the rules travel with it, so the skill never needs updating. */
export function ClaudeConnector() {
  // The consent is kept, like a cookie choice: asked once, and never again once given or once a
  // token exists (each one was created on an "I agree").
  const consent = useConsent("claude");
  const tokens = useAccessTokens();
  const give = useGiveConsent("claude");
  const withdraw = useWithdrawConsent("claude");
  const agreed = consentGiven(
    consent.data?.given_at,
    (tokens.data ?? []).map((t) => t.transfer_consent_at),
  );
  const consentUnknown = consent.isLoading || tokens.isLoading;
  return (
    <div className="space-y-6">
      <section className="space-y-4">
        <div>
          <h3 className="font-medium">Claude</h3>
          <p className="text-sm text-muted-foreground">
            Write applications with Claude, on your own subscription: paste a
            posting into a chat, talk the CV and the letter through, and Claude
            saves them here, where you check the layout and export the PDF.
            jac's own AI isn't used for any of it. Claude reaches your career,
            your applications and your portfolio, never your account or your API
            keys. It changes your public portfolio only with a token you allow
            to.
          </p>
        </div>
        {!consentUnknown && !agreed && (
          <ClaudeConsent
            pending={give.isPending}
            onAgree={() =>
              give.mutate(undefined, {
                onError: () =>
                  toast.error("Couldn't save your consent. Try again."),
              })
            }
          />
        )}
        {agreed && (
          <ConsentKept
            givenAt={consent.data?.given_at ?? null}
            pending={withdraw.isPending}
            withdrawWarning="Withdraw your consent? Every token is revoked: the connector stops, and creating a new token asks again."
            onWithdraw={() =>
              withdraw.mutate(undefined, {
                onSuccess: () =>
                  toast.success("Consent withdrawn, tokens revoked"),
                onError: () => toast.error("Couldn't withdraw. Try again."),
              })
            }
          />
        )}
        <ConnectorSteps />
        <CreateToken agreed={agreed} />
        <DownloadSkill />
      </section>
      <TokenList />
      <CareerChanges />
    </div>
  );
}

/** The consent card for tokens: the data goes to Anthropic through the user's Claude account,
 *  the same transfer as an API key. Shown until the user agrees once; the server keeps it. */
function ClaudeConsent({
  onAgree,
  pending,
}: {
  onAgree: () => void;
  pending: boolean;
}) {
  return (
    <TransferConsent
      consent="I agree that the data above goes to Anthropic through every token I create, also outside the EU. I can withdraw here at any time, which revokes my tokens."
      onAgree={onAgree}
      pending={pending}
    >
      <p>
        With a token, Claude reads your career, your contact details, your
        personality answers and writing sample, your applications and your
        portfolio from jac, and saves applications and career changes here, and
        portfolio changes with a token you allow to. That data goes to
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
        ), and we have no influence on that.
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
      <ol className="list-decimal space-y-1 pl-5 text-sm">
        <li>
          Create a token below, e.g. <q>Claude</q>.
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
          Download the skill below and upload <code>jac-skill.zip</code> in
          Claude, under Settings → Capabilities → Skills. It only points Claude
          at the connector, so it never needs updating.
        </li>
        <li>
          In a chat, switch jac on (+ → Connectors) and paste a job posting.
          Claude reads your career, saves when you say so, and its link opens
          the application here.
        </li>
      </ol>
      <p className="text-sm text-muted-foreground">
        Claude Code, signed in with the same Claude account, picks up both the
        connector and the skill.
      </p>
    </div>
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
        Once: one file that points Claude at the connector, nothing about you in
        it.
      </p>
    </div>
  );
}

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** The batches of career edits Claude applied, newest first, each one undoable until the user
 *  keeps it. Only the newest open batch starts expanded: it's the one to check, and the rest
 *  read as one line each. */
function CareerChanges() {
  const batches = useCareerChanges();
  // Up here, not in the row: a kept row unmounts before its own callbacks could toast.
  const keep = useKeepCareerChange();
  const keepAll = useKeepAllCareerChanges();
  const rows = batches.data ?? [];
  const expanded = expandedBatch(rows);

  // No confirm: the edits stay, only the undo goes.
  function onKeep(batch: CareerChangeBatch) {
    const [done, verb] = batch.undone_at
      ? ["removed", "remove"]
      : ["kept", "keep"];
    keep.mutate(batch.id, {
      onSuccess: () => toast.success(`Batch ${done}`),
      onError: (e) =>
        toast.error(errorLines(e, `Couldn't ${verb} it. Try again.`)[0]),
    });
  }

  function onKeepAll() {
    // Every batch goes, also the ones older than this list shows: so no count.
    if (!confirm("Keep all batches? They can't be undone any more.")) return;
    keepAll.mutate(undefined, {
      onSuccess: () => toast.success("All batches kept"),
      onError: (e) =>
        toast.error(errorLines(e, "Couldn't keep them. Try again.")[0]),
    });
  }

  return (
    <section className="space-y-3">
      <div className="space-y-1">
        <div className="flex items-center justify-between gap-2">
          <h4 className="text-sm font-medium">Career changes</h4>
          {rows.length > 0 && (
            <Button
              variant="outline"
              size="sm"
              disabled={keepAll.isPending}
              onClick={onKeepAll}
            >
              {keepAll.isPending ? "Keeping…" : "Keep all"}
            </Button>
          )}
        </div>
        <p className="text-sm text-muted-foreground">
          Claude can review your career and, once you agree, edit entries:
          descriptions, the skills a job used, skills that are missing. Every
          batch of changes is listed here and can be undone. Once a batch is
          right, keep it: it leaves the list and can't be undone any more, here
          or by Claude. The edits stay.
        </p>
      </div>
      {rows.length ? (
        <ul className="divide-y">
          {rows.map((batch) => (
            <BatchItem
              key={batch.id}
              batch={batch}
              startsOpen={batch.id === expanded}
              keeping={
                keepAll.isPending ||
                (keep.isPending && keep.variables === batch.id)
              }
              onKeep={() => onKeep(batch)}
            />
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

/** One batch: its header line (when, by whom, how many, and what can be done with it), and
 *  its changes below it once expanded. The buttons sit beside the toggle, not in it, so a
 *  click on one never folds the batch. */
function BatchItem({
  batch,
  startsOpen,
  keeping,
  onKeep,
}: {
  batch: CareerChangeBatch;
  startsOpen: boolean;
  keeping: boolean;
  onKeep: () => void;
}) {
  const undo = useUndoCareerChange();
  // Held per batch, keyed by its id: what the user opened stays open as the list refreshes.
  const [open, setOpen] = useState(startsOpen);
  const busy = keeping || undo.isPending;
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
        <button
          type="button"
          className="flex min-w-0 items-center gap-1 text-left text-sm"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          <ChevronRight
            className={cn(
              "size-4 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-90",
            )}
          />
          <span>
            <span className="font-medium">{when(batch.created_at)}</span>
            <span className="text-muted-foreground">
              {" "}
              · {batch.source === "claude" ? "by Claude" : "imported"} · {count}
            </span>
          </span>
        </button>
        {batch.undone_at ? (
          <div className="flex items-center gap-1">
            <span className="text-xs text-muted-foreground">
              undone {when(batch.undone_at)}
            </span>
            <Button
              variant="ghost"
              size="sm"
              disabled={keeping}
              onClick={onKeep}
            >
              {keeping ? "Removing…" : "Remove"}
            </Button>
          </div>
        ) : (
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="sm" disabled={busy} onClick={onKeep}>
              {keeping ? "Keeping…" : "Keep"}
            </Button>
            <Button variant="ghost" size="sm" disabled={busy} onClick={onUndo}>
              {undo.isPending ? "Undoing…" : "Undo"}
            </Button>
          </div>
        )}
      </div>
      {open && (
        <ul className={batch.undone_at ? "space-y-1 opacity-60" : "space-y-1"}>
          {batch.changes.map((change, i) => (
            <ChangeItem
              key={`${change.id}-${i}`}
              change={change}
              labels={batch.labels}
            />
          ))}
        </ul>
      )}
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

/** One token for the connector: the box shows it in the two shapes Claude's dialog takes.
 *  Locked until the user agreed to the transfer (the card above, once). */
function CreateToken({ agreed }: { agreed: boolean }) {
  const create = useCreateAccessToken();
  const [name, setName] = useState("");
  // Off unless ticked: a portfolio write is public the moment Claude makes it.
  const [portfolio, setPortfolio] = useState(false);
  // Held for this visit only: dismissing the box forgets the key.
  const [minted, setMinted] = useState<NewAccessToken | null>(null);

  function onCreate() {
    create.mutate(
      {
        name: name.trim(),
        transferConsent: agreed,
        mayEditPortfolio: portfolio,
      },
      {
        onSuccess: (token) => {
          setMinted(token);
          setName("");
          setPortfolio(false);
        },
        onError: (e) =>
          toast.error(errorLines(e, "Could not create the token.")[0]),
      },
    );
  }

  return minted ? (
    <NewTokenBox token={minted} onDismiss={() => setMinted(null)} />
  ) : (
    <div className="space-y-2">
      <div className="flex items-end gap-2">
        <div className="flex-1 space-y-1">
          <Label htmlFor="token-name">Token name</Label>
          <Input
            id="token-name"
            value={name}
            maxLength={60}
            disabled={!agreed}
            placeholder={agreed ? "e.g. Claude" : "Agree above first"}
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
      <div className="flex items-start gap-2">
        <Checkbox
          id="token-portfolio"
          checked={portfolio}
          disabled={!agreed}
          onCheckedChange={(v) => setPortfolio(v === true)}
        />
        <div className="space-y-0.5">
          <Label htmlFor="token-portfolio">
            May change your public portfolio
          </Label>
          <p className="text-xs text-muted-foreground">
            Its blocks, its links and the standard page, live, after your yes in
            the chat. Without it, Claude only reads them. You can switch it for
            each token below.
          </p>
        </div>
      </div>
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
  const portfolio = useSetTokenPortfolio();
  const rows = tokens.data ?? [];

  function onPortfolio(row: AccessTokenRow, allowed: boolean) {
    if (
      allowed &&
      !confirm(
        `Let "${row.name || row.prefix}" change your public portfolio? Whatever uses it can then change what every visitor sees.`,
      )
    )
      return;
    portfolio.mutate(
      { id: row.id, allowed },
      {
        onSuccess: () =>
          toast.success(
            allowed ? "It may change your portfolio" : "Portfolio: read only",
          ),
        onError: (e) =>
          toast.error(errorLines(e, "Couldn't switch it. Try again.")[0]),
      },
    );
  }

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
      <h4 className="text-sm font-medium">Tokens</h4>
      {rows.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Key</TableHead>
              <TableHead>Created</TableHead>
              <TableHead>Last used</TableHead>
              <TableHead>Portfolio</TableHead>
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
                <TableCell>
                  <Checkbox
                    aria-label={`${row.name || row.prefix} may change your public portfolio`}
                    checked={row.may_edit_portfolio}
                    disabled={portfolio.isPending}
                    onCheckedChange={(v) => onPortfolio(row, v === true)}
                  />
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
            : "No tokens yet. The connector uses one."}
        </p>
      )}
    </section>
  );
}
