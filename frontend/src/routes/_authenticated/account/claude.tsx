import { useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { toast } from "sonner";
import { QueryError } from "@/components/query-error";
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
  envLines,
  errorLines,
  useAccessTokens,
  useCreateAccessToken,
  useDownloadSkill,
  useRevokeAccessToken,
  type AccessTokenRow,
  type NewAccessToken,
} from "@/lib/queries/skill";

export const Route = createFileRoute("/_authenticated/account/claude")({
  component: ClaudePage,
});

function ClaudePage() {
  return (
    <div className="space-y-8">
      <section className="space-y-4">
        <div>
          <h2 className="text-lg font-medium">Claude</h2>
          <p className="text-sm text-muted-foreground">
            Write applications with Claude, on your own subscription: paste a
            posting into Claude, talk the CV and letter through, and save it
            here. The skill reads your career and writes applications with a
            personal token. It can't reach your account, your API keys or
            anything else.
          </p>
        </div>
        <DownloadSkill />
        <SetupSteps />
      </section>
      <Separator />
      <ClaudeCode />
      <Separator />
      <TokenList />
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
        The zip contains a token: anyone with the file can read your career and
        write applications until you revoke it below.
      </p>
    </div>
  );
}

function SetupSteps() {
  return (
    <ol className="list-decimal space-y-1 pl-5 text-sm">
      <li>
        In Claude: Settings → Capabilities → Skills → upload{" "}
        <code>jac-skill.zip</code>.
      </li>
      <li>
        In the same settings, allow code execution to reach{" "}
        <code>{window.location.host}</code> under network access. Without it the
        skill still works from the snapshot in the zip, and you import what it
        writes under{" "}
        <Link to="/applications" className="underline">
          Applications → Import from Claude
        </Link>
        .
      </li>
      <li>Start a chat and paste a job posting (or its link).</li>
    </ol>
  );
}

/** Claude Code reads the address and the token from the shell, so this mints a bare token. */
function ClaudeCode() {
  const create = useCreateAccessToken();
  const [name, setName] = useState("");
  // Held for this visit only: dismissing the box forgets the key.
  const [minted, setMinted] = useState<NewAccessToken | null>(null);

  function onCreate() {
    create.mutate(name.trim(), {
      onSuccess: (token) => {
        setMinted(token);
        setName("");
      },
      onError: (e) =>
        toast.error(errorLines(e, "Could not create the token.")[0]),
    });
  }

  return (
    <section className="space-y-3">
      <div>
        <h3 className="font-medium">Claude Code</h3>
        <p className="text-sm text-muted-foreground">
          The same skill works in Claude Code, with a token of its own.
        </p>
      </div>
      {minted ? (
        <NewTokenBox token={minted} onDismiss={() => setMinted(null)} />
      ) : (
        <div className="flex items-end gap-2">
          <div className="flex-1 space-y-1">
            <Label htmlFor="token-name">Token name</Label>
            <Input
              id="token-name"
              value={name}
              maxLength={60}
              placeholder="e.g. Claude Code, laptop"
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <Button
            onClick={onCreate}
            disabled={create.isPending || !name.trim()}
          >
            {create.isPending ? "Creating…" : "Create token"}
          </Button>
        </div>
      )}
    </section>
  );
}

function NewTokenBox({
  token,
  onDismiss,
}: {
  token: NewAccessToken;
  onDismiss: () => void;
}) {
  const lines = envLines(window.location.origin, token.key);
  return (
    <div className="space-y-2 rounded-md border px-4 py-3">
      <p className="text-sm font-medium">
        Copy it now: the key isn't shown again.
      </p>
      <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">
        {lines}
      </pre>
      <p className="text-xs text-muted-foreground">
        Put these lines in your shell profile, and the skill folder from the zip
        in <code>~/.claude/skills/jac/</code>.
      </p>
      <div className="flex gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            navigator.clipboard.writeText(lines).then(
              () => toast.success("Copied"),
              () => toast.error("Copy failed: select the lines instead."),
            )
          }
        >
          Copy
        </Button>
        <Button size="sm" variant="ghost" onClick={onDismiss}>
          Done
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
            : "No tokens yet. Downloading the skill creates one."}
        </p>
      )}
    </section>
  );
}
