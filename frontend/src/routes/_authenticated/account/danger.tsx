import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { withReauth } from "@/lib/reauth";
import { signOut, useInvalidateSession } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type Sess = {
  id: number;
  ip: string;
  created_at: string;
  user_agent: string;
  is_current: boolean;
};

export const Route = createFileRoute("/_authenticated/account/danger")({
  component: DangerZone,
});

function DangerZone() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const invalidate = useInvalidateSession();
  const [confirmText, setConfirmText] = useState("");

  const sessions = useQuery({
    queryKey: ["account", "sessions"],
    queryFn: () =>
      api<{ data: Sess[] }>("/_allauth/browser/v1/auth/sessions").then(
        (r) => r.data,
      ),
  });

  const logoutOthers = useMutation({
    mutationFn: () =>
      api("/_allauth/browser/v1/auth/sessions", { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["account", "sessions"] });
      toast.success("Other sessions signed out");
    },
  });

  const logout = useMutation({
    mutationFn: signOut,
    onSuccess: async () => {
      await invalidate();
      navigate({ to: "/auth/login", search: {} as never });
    },
  });

  const del = useMutation({
    mutationFn: () =>
      withReauth(() => api("/api/spa/account/", { method: "DELETE" })),
    onSuccess: async () => {
      await invalidate();
      toast.success("Account deleted");
      navigate({ to: "/", search: {} as never });
    },
    onError: () => toast.error("Could not delete account"),
  });

  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Sessions</h2>
        <ul className="space-y-2">
          {sessions.data?.map((s) => (
            <li key={s.id} className="rounded border p-3 text-sm">
              <div className="font-mono">
                {s.ip} · {new Date(s.created_at).toLocaleString()}
              </div>
              <div className="text-xs text-muted-foreground truncate">
                {s.user_agent}
              </div>
              {s.is_current && (
                <span className="text-xs text-emerald-600">
                  current session
                </span>
              )}
            </li>
          ))}
        </ul>
        <Button variant="outline" onClick={() => logoutOthers.mutate()}>
          Sign out other sessions
        </Button>
        <Button variant="outline" onClick={() => logout.mutate()}>
          Sign out
        </Button>
      </section>

      <section className="space-y-3 border-t pt-6">
        <h2 className="text-lg font-semibold text-destructive">
          Delete account
        </h2>
        <p className="text-sm text-muted-foreground">
          Permanently removes your profile, career entries, applications, and
          LLM configs.
        </p>
        <Label htmlFor="confirm">
          Type <code className="font-mono">delete</code> to confirm
        </Label>
        <Input
          id="confirm"
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
        />
        <Button
          variant="destructive"
          disabled={confirmText !== "delete" || del.isPending}
          onClick={() => del.mutate()}
        >
          Delete my account
        </Button>
      </section>
    </div>
  );
}
