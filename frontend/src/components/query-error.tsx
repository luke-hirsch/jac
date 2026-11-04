import { Button } from "@/components/ui/button";
import { loadFailureMessage } from "@/lib/api";

/** A failed fetch, said out loud: without this it reads as "nothing here yet", or as a blank page. */
export function QueryError({
  error,
  what,
  onRetry,
  retrying,
}: {
  error: unknown;
  what: string;
  onRetry: () => void;
  retrying: boolean;
}) {
  return (
    <div role="alert" className="space-y-2 text-sm">
      <p className="text-muted-foreground">{loadFailureMessage(error, what)}</p>
      <Button variant="outline" size="sm" onClick={onRetry} disabled={retrying}>
        {retrying ? "Retrying…" : "Try again"}
      </Button>
    </div>
  );
}
