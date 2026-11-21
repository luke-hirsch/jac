import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import {
  checkResultLabel,
  configPayload,
  modelListLabel,
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
import { ConsentKept, TransferConsent } from "@/components/transfer-consent";
import {
  consentGiven,
  useConsent,
  useGiveConsent,
  useWithdrawConsent,
} from "@/lib/queries/consent";
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
  // The consent is kept, like a cookie choice: asked once, and never again once given or once a
  // key is stored on it (a stored key means the card was answered).
  const consent = useConsent("providers");
  const give = useGiveConsent("providers");
  const withdraw = useWithdrawConsent("providers");
  const agreed = consentGiven(
    consent.data?.given_at,
    configs.map((c) => c.transfer_consent_at),
  );
  const consentUnknown = consent.isLoading || configsQ.isLoading;

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-medium">LLM providers</h2>
        <p className="text-sm text-muted-foreground">
          A generation runs on exactly one executor. HirschAI is built in and
          free; add a commercial key below to unlock high mode and the
          researched personal paragraph. Keys are write-only — stored encrypted,
          never shown again. Saving a key asks the provider which models it
          reaches; those are the ones the generate panel offers.
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

      {!consentUnknown && !agreed && commercial.length > 0 && (
        <ProviderConsent
          providers={commercial}
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
          withdrawWarning="Withdraw your consent? Your API keys are deleted, and saving a new one asks again."
          onWithdraw={() =>
            withdraw.mutate(undefined, {
              onSuccess: () => toast.success("Consent withdrawn, keys deleted"),
              onError: () => toast.error("Couldn't withdraw. Try again."),
            })
          }
        />
      )}

      <div className="space-y-3">
        {commercial.map((row) => (
          <ProviderCard
            key={row.provider}
            row={row}
            config={configFor(row.provider)}
            agreed={agreed}
          />
        ))}
      </div>
    </div>
  );
}

/** The consent card for API keys, shown until the user agrees once: the server keeps it, and
 *  stamps it on every key saved on it. */
function ProviderConsent({
  providers,
  onAgree,
  pending,
}: {
  providers: ExecutorRow[];
  onAgree: () => void;
  pending: boolean;
}) {
  return (
    <TransferConsent
      consent="I agree that the data of my runs goes to the provider whose key I enter, also outside the EU. I can withdraw here at any time, which deletes my keys."
      onAgree={onAgree}
      pending={pending}
    >
      <p>
        With your own key, the data of a run (career entries, the job posting,
        dossiers) goes to that provider, under your account and your contract
        with them. They may process it in the USA, where data protection can be
        weaker than in the EU and authorities may be able to access it.
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
        ), and we have no influence on that. Without a key, everything stays on
        HirschAI.
      </p>
    </TransferConsent>
  );
}

function ProviderCard({
  row,
  config,
  agreed,
}: {
  row: ExecutorRow;
  config: LLMConfigRow | null;
  agreed: boolean;
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

        {config?.has_api_key && (
          <p className="text-xs text-muted-foreground">
            {modelListLabel(config)} Check refreshes the list, then sends a test
            message.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
