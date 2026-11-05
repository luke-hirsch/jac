import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import {
  checkResultLabel,
  configPayload,
  useCheckConfig,
  useCreateConfig,
  useDeleteConfig,
  useExecutors,
  useLLMConfigs,
  useUpdateConfig,
  type CheckResult,
  type ExecutorRow,
  type LLMConfigRow,
} from "@/lib/queries/llm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export const Route = createFileRoute("/_authenticated/account/llm")({
  component: LLMConfigPage,
});

/** Where each provider says what it does with the data. */
const PROVIDER_TERMS: Record<string, string> = {
  anthropic: "https://www.anthropic.com/legal/commercial-terms",
  openai: "https://openai.com/policies/data-processing-addendum/",
};

/** Flatten the first DRF/ApiError field error into a readable message. */
function apiMessage(err: unknown): string {
  if (err instanceof ApiError && err.data && typeof err.data === "object") {
    const data = err.data as Record<string, unknown>;
    for (const [field, val] of Object.entries(data)) {
      const msg = Array.isArray(val) ? val[0] : val;
      if (typeof msg === "string")
        return field === "detail" ? msg : `${field}: ${msg}`;
    }
  }
  return "Save failed.";
}

function LLMConfigPage() {
  const executors = useExecutors();
  const configsQ = useLLMConfigs();
  const rows = executors.data ?? [];
  const configs = configsQ.data ?? [];

  const hirsch = rows.find((r) => r.self_hosted) ?? null;
  const commercial = rows.filter((r) => !r.self_hosted);
  const configFor = (provider: string) =>
    configs.find((c) => c.provider === provider) ?? null;
  // Consent is stored per key when it's saved; this only unlocks the inputs for this visit.
  const [agreed, setAgreed] = useState(false);
  const consentMissing = commercial.some(
    (r) => !configFor(r.provider)?.transfer_consent_at,
  );

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-medium">LLM providers</h2>
        <p className="text-sm text-muted-foreground">
          A generation runs on exactly one executor. HirschAI is built in and
          free; add a commercial key below to unlock high mode and the
          researched personal paragraph. Keys are write-only — stored encrypted,
          never shown again.
        </p>
      </div>

      {hirsch && (
        <div className="flex items-center gap-2 rounded-md border px-4 py-3">
          <span className="font-medium">{hirsch.label}</span>
          <Badge variant="secondary">built in · runs standard</Badge>
          {hirsch.reachable === false ? (
            <Badge variant="outline">asleep · jobs wait for it</Badge>
          ) : hirsch.reachable ? (
            <Badge variant="outline">online</Badge>
          ) : (
            <Badge variant="outline">wakes when needed</Badge>
          )}
        </div>
      )}

      {executors.isLoading && <p className="text-sm">loading…</p>}

      {consentMissing && !agreed && (
        <TransferConsent
          providers={commercial}
          onAgree={() => setAgreed(true)}
        />
      )}

      <div className="space-y-3">
        {commercial.map((row) => (
          <ProviderCard
            key={row.provider}
            row={row}
            config={configFor(row.provider)}
            agreed={agreed}
            onDeleted={() => setAgreed(false)}
          />
        ))}
      </div>
    </div>
  );
}

/** Consent before any key goes in (Art. 49(1)(a) DSGVO): an "I agree", not a notice to wave
 *  away. The server stamps it on the key that's saved next; deleting the key withdraws it. */
function TransferConsent({
  providers,
  onAgree,
}: {
  providers: ExecutorRow[];
  onAgree: () => void;
}) {
  return (
    <Card className="border border-destructive/50">
      <CardHeader>
        <CardTitle className="text-base">Your data leaves the EU</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p>
          With your own key, the data of a run (career entries, the job posting,
          dossiers) goes to that provider, under your account and your contract
          with them. They may process it in the USA, where data protection can
          be weaker than in the EU and authorities may be able to access it.
        </p>
        <p>
          What the provider does with it is set by its own terms (
          {providers.map((p, i) => (
            <span key={p.provider}>
              {i > 0 && ", "}
              <a
                href={PROVIDER_TERMS[p.provider]}
                target="_blank"
                rel="noreferrer"
                className="underline"
              >
                {p.label}
              </a>
            </span>
          ))}
          ), and we have no influence on that. Without a key, everything stays
          on HirschAI.
        </p>
        <p>
          I agree that the data of my runs goes to the provider whose key I
          enter, also outside the EU. I can withdraw by deleting the key.
        </p>
        <Button size="sm" onClick={onAgree}>
          I agree
        </Button>
      </CardContent>
    </Card>
  );
}

function ProviderCard({
  row,
  config,
  agreed,
  onDeleted,
}: {
  row: ExecutorRow;
  config: LLMConfigRow | null;
  agreed: boolean;
  onDeleted: () => void;
}) {
  const create = useCreateConfig();
  const update = useUpdateConfig();
  const del = useDeleteConfig();
  const check = useCheckConfig();
  const [apiKey, setApiKey] = useState("");
  const [checkResult, setCheckResult] = useState<CheckResult | null>(null);
  const busy = create.isPending || update.isPending;
  // A key's consent lasts until the key is deleted; without one, the card above unlocks this.
  const locked = !config?.transfer_consent_at && !agreed;

  const saveOpts = {
    onSuccess: () => {
      toast.success(`${row.label} saved`);
      setApiKey("");
    },
    onError: (e: unknown) => toast.error(apiMessage(e)),
  };

  function onSaveKey() {
    const body = configPayload({
      provider: row.provider,
      apiKey,
      transferConsent: agreed,
    });
    if (!config && !body.api_key) return toast.error("Enter an API key first.");
    if (config) update.mutate({ id: config.id, body }, saveOpts);
    else create.mutate(body, saveOpts);
  }

  function onMakeDefault() {
    if (!config) return;
    update.mutate(
      {
        id: config.id,
        body: configPayload({ provider: row.provider, makeDefault: true }),
      },
      {
        onSuccess: () => toast.success(`${row.label} is now the default`),
        onError: (e) => toast.error(apiMessage(e)),
      },
    );
  }

  function onCheck() {
    if (!config) return;
    setCheckResult(null);
    check.mutate(config.id, {
      onSuccess: (r) => setCheckResult(r),
      onError: () => toast.error("Check failed"),
    });
  }

  function onDelete() {
    if (!config) return;
    if (!confirm(`Remove the ${row.label} key?`)) return;
    del.mutate(config.id, {
      onSuccess: () => {
        toast.success(`${row.label} key removed`);
        // Deleting withdrew the consent, so the next key asks again.
        onDeleted();
      },
      onError: () => toast.error("Delete failed"),
    });
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center gap-2 space-y-0">
        <CardTitle className="text-base">{row.label}</CardTitle>
        {config?.has_api_key ? (
          <Badge variant="outline">key set</Badge>
        ) : (
          <Badge variant="secondary">no key</Badge>
        )}
        {config?.default && <Badge>default</Badge>}
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-end gap-2">
          <div className="flex-1 space-y-1">
            <Label>{config?.has_api_key ? "Replace API key" : "API key"}</Label>
            <Input
              type="password"
              autoComplete="new-password"
              value={apiKey}
              disabled={locked}
              placeholder={
                locked
                  ? "Agree to the transfer above first"
                  : config?.has_api_key
                    ? "••••••••"
                    : ""
              }
              onChange={(e) => setApiKey(e.target.value)}
            />
          </div>
          <Button
            onClick={onSaveKey}
            disabled={busy || locked || !apiKey.trim()}
          >
            {busy ? "Saving…" : config?.has_api_key ? "Replace" : "Save key"}
          </Button>
        </div>

        {config && (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={
                !config.has_api_key || config.default || update.isPending
              }
              onClick={onMakeDefault}
            >
              {config.default ? "Default executor" : "Make default"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!config.has_api_key || check.isPending}
              onClick={onCheck}
            >
              {check.isPending ? "Checking…" : "Check"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={del.isPending}
              onClick={onDelete}
            >
              Delete
            </Button>
            {checkResult && (
              <span
                className={`text-xs ${
                  checkResult.ok ? "text-muted-foreground" : "text-destructive"
                }`}
              >
                {checkResultLabel(checkResult)}
              </span>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
