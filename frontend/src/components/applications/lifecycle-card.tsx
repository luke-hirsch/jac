import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  OUTCOME_LABELS,
  STATUS_LABELS,
  useTransitionApplication,
  useUpdateApplication,
  type ApplicationRow,
  type ResponseOutcome,
} from "@/lib/queries/applications";

export function LifecycleCard({ app }: { app: ApplicationRow }) {
  const transition = useTransitionApplication();
  const update = useUpdateApplication();
  const [deadline, setDeadline] = useState(app.deadline ?? "");
  const [outcome, setOutcome] = useState<ResponseOutcome>("interview");
  const [note, setNote] = useState("");

  function move(payload: Parameters<typeof transition.mutate>[0]["payload"]) {
    transition.mutate(
      { id: app.id, payload },
      {
        onError: () => toast.error("That move isn't allowed right now."),
      },
    );
  }

  function saveDeadline() {
    update.mutate(
      { id: app.id, body: { deadline: deadline || null } },
      {
        onSuccess: () => toast.success("Deadline saved"),
        onError: () => toast.error("Could not save the deadline"),
      },
    );
  }

  const busy = transition.isPending;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Lifecycle</CardTitle>
        <Badge variant="outline">{STATUS_LABELS[app.status]}</Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1">
          <Label htmlFor="deadline">Application deadline</Label>
          <div className="flex items-center gap-2">
            <Input
              id="deadline"
              type="date"
              className="w-44"
              value={deadline}
              onChange={(e) => setDeadline(e.target.value)}
            />
            <Button
              size="sm"
              variant="outline"
              disabled={update.isPending || deadline === (app.deadline ?? "")}
              onClick={saveDeadline}
            >
              Save
            </Button>
          </div>
        </div>

        {/* Only the moves that are legal from the current status are offered — the server
            re-checks (TRANSITIONS), this is just the friendly surface. */}
        {app.status === "draft" && (
          <Button
            size="sm"
            disabled={busy}
            onClick={() => move({ to: "approved" })}
          >
            Approve
          </Button>
        )}

        {app.status === "approved" && (
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              disabled={busy}
              onClick={() => move({ to: "sent", delivery_method: "manual" })}
            >
              Mark sent (downloaded)
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => move({ to: "sent", delivery_method: "email" })}
            >
              Mark sent (emailed myself)
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => move({ to: "draft" })}
            >
              Back to draft
            </Button>
          </div>
        )}

        {(app.status === "sent" || app.status === "follow_up") && (
          <div className="space-y-3">
            {app.status === "sent" && (
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => move({ to: "follow_up" })}
              >
                Mark follow-up sent
              </Button>
            )}
            <div className="space-y-2 rounded-md border p-3">
              <Label>Record a response</Label>
              <Select
                value={outcome}
                onValueChange={(v) => setOutcome(v as ResponseOutcome)}
              >
                <SelectTrigger className="w-44">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(OUTCOME_LABELS).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Textarea
                rows={2}
                placeholder="Note (optional)…"
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
              <Button
                size="sm"
                disabled={busy}
                onClick={() =>
                  move({ to: "response", response_outcome: outcome, note })
                }
              >
                Record response
              </Button>
            </div>
          </div>
        )}

        {app.status === "response" && (
          <p className="text-sm text-muted-foreground">
            Response recorded
            {app.response_outcome
              ? `: ${OUTCOME_LABELS[app.response_outcome]}`
              : ""}
            {app.notes ? ` — ${app.notes}` : ""}
          </p>
        )}

        {app.status === "inactive" && (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => move({ to: "draft" })}
          >
            Reopen
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
