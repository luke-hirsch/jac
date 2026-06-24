import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import {
  PROVIDER_SPECS,
  rowToState,
  switchProvider,
  toPayload,
  useCreateConfig,
  useDeleteConfig,
  useLLMConfigs,
  useUpdateConfig,
  type ConfigFormState,
  type LLMConfigRow,
  type Provider,
} from "@/lib/queries/llm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export const Route = createFileRoute("/_authenticated/account/llm")({
  component: LLMConfigPage,
});

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
  const configsQ = useLLMConfigs();
  const [editing, setEditing] = useState<LLMConfigRow | "new" | null>(null);
  const del = useDeleteConfig();

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-medium">LLM providers</h2>
          <p className="text-sm text-muted-foreground">
            Map an alias (e.g. <code>reasoning</code>, <code>writer</code>) to a
            provider, model and key. The JAC pipeline resolves through these;
            with none set it falls back to the zero-cost Ollama default.
          </p>
        </div>
        <Button onClick={() => setEditing("new")}>Add config</Button>
      </div>

      {configsQ.isLoading && <p className="text-sm">loading…</p>}
      {configsQ.data?.length === 0 && (
        <p className="text-sm text-muted-foreground">No configs yet.</p>
      )}

      <ul className="divide-y rounded-md border">
        {configsQ.data?.map((c) => (
          <li
            key={c.id}
            className="flex items-center justify-between gap-3 px-4 py-3"
          >
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-medium">{c.alias}</span>
                <Badge variant="secondary">
                  {PROVIDER_SPECS[c.provider].label}
                </Badge>
                {c.has_api_key ? (
                  <Badge variant="outline">key stored ✓</Badge>
                ) : (
                  PROVIDER_SPECS[c.provider].apiKey === "required" && (
                    <Badge variant="destructive">no key</Badge>
                  )
                )}
              </div>
              <p className="truncate text-sm text-muted-foreground">
                {c.model}
              </p>
            </div>
            <div className="flex shrink-0 gap-2">
              <Button variant="outline" size="sm" onClick={() => setEditing(c)}>
                Edit
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  if (!confirm(`Delete config "${c.alias}"?`)) return;
                  del.mutate(c.id, {
                    onSuccess: () => toast.success("Deleted"),
                    onError: () => toast.error("Delete failed"),
                  });
                }}
              >
                Delete
              </Button>
            </div>
          </li>
        ))}
      </ul>

      {editing && (
        <ConfigDialog
          row={editing === "new" ? undefined : editing}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

function ConfigDialog({
  row,
  onClose,
}: {
  row?: LLMConfigRow;
  onClose: () => void;
}) {
  const [state, setState] = useState<ConfigFormState>(() => rowToState(row));
  const create = useCreateConfig();
  const update = useUpdateConfig();
  const spec = PROVIDER_SPECS[state.provider];
  const busy = create.isPending || update.isPending;

  const set = (patch: Partial<ConfigFormState>) =>
    setState((s) => ({ ...s, ...patch }));

  function save() {
    let payload;
    try {
      payload = toPayload(state); // throws on bad JSON
    } catch (e) {
      return toast.error((e as Error).message);
    }
    if (!payload.alias) return toast.error("Alias is required.");
    if (!payload.model) return toast.error("Model is required.");
    if (spec.url === "required" && !payload.url)
      return toast.error("URL is required for this provider.");
    if (spec.apiKey === "required" && !row?.has_api_key && !payload.api_key)
      return toast.error("API key is required.");

    const done = {
      onSuccess: () => {
        toast.success("Saved");
        onClose();
      },
      onError: (e: unknown) => toast.error(apiMessage(e)),
    };
    if (row) update.mutate({ id: row.id, body: payload }, done);
    else create.mutate(payload, done);
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {row ? `Edit ${row.alias}` : "New LLM config"}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <Field label="Alias">
            <Input
              value={state.alias}
              placeholder="reasoning"
              onChange={(e) => set({ alias: e.target.value })}
            />
          </Field>

          <Field label="Provider">
            <Select
              value={state.provider}
              onValueChange={(v) =>
                setState((s) => switchProvider(s, v as Provider))
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(PROVIDER_SPECS) as Provider[]).map((p) => (
                  <SelectItem key={p} value={p}>
                    {PROVIDER_SPECS[p].label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field label="Model">
            <Input
              value={state.model}
              placeholder={spec.modelPlaceholder}
              onChange={(e) => set({ model: e.target.value })}
            />
          </Field>

          {spec.url !== "hidden" && (
            <Field
              label={spec.url === "required" ? "URL" : "Base URL (optional)"}
            >
              <Input
                value={state.url}
                placeholder="http://localhost:11434"
                onChange={(e) => set({ url: e.target.value })}
              />
            </Field>
          )}

          <Field label="Max tokens (optional)">
            <Input
              type="number"
              value={state.max_tokens}
              onChange={(e) => set({ max_tokens: e.target.value })}
            />
          </Field>

          {/* structured providers: discrete extra inputs */}
          {spec.extra === "structured" &&
            spec.extraFields.map((f) => (
              <Field key={f.key} label={f.label} help={f.help}>
                {f.kind === "select" ? (
                  <Select
                    value={state.extraFields[f.key] ?? ""}
                    onValueChange={(v) =>
                      set({ extraFields: { ...state.extraFields, [f.key]: v } })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="default" />
                    </SelectTrigger>
                    <SelectContent>
                      {f.options.map((o) => (
                        <SelectItem key={o || "__default__"} value={o}>
                          {o || "default"}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <Input
                    type="number"
                    value={state.extraFields[f.key] ?? ""}
                    onChange={(e) =>
                      set({
                        extraFields: {
                          ...state.extraFields,
                          [f.key]: e.target.value,
                        },
                      })
                    }
                  />
                )}
              </Field>
            ))}

          {/* custom / ollama: raw JSON extras */}
          {spec.extra === "json" && (
            <Field label="Extra config (JSON)" help={spec.jsonHint}>
              <Textarea
                rows={5}
                className="font-mono text-xs"
                value={state.extraJson}
                placeholder="{}"
                onChange={(e) => set({ extraJson: e.target.value })}
              />
            </Field>
          )}

          <Field
            label="API key"
            help={
              row?.has_api_key
                ? "A key is stored. Leave blank to keep it, or enter a new one to replace."
                : spec.apiKey === "required"
                  ? "Required for this provider."
                  : "Optional for this provider."
            }
          >
            <Input
              type="password"
              autoComplete="new-password"
              value={state.api_key}
              placeholder={row?.has_api_key ? "••••••••" : ""}
              onChange={(e) => set({ api_key: e.target.value })}
            />
          </Field>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={save} disabled={busy}>
            {busy ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <Label>{label}</Label>
      {children}
      {help && <p className="text-xs text-muted-foreground">{help}</p>}
    </div>
  );
}
