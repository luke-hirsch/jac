import { useNavigate } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import { clearStamp } from "@/lib/portfolio/stamp";

/** Shown on every personalised view so the visitor is never trapped. "Start over"
 *  clears the stamp and returns to the questionnaire at "/"; the optional
 *  "Feeling lucky again" reshuffles a lucky view in place (parent passes onShuffle). */
export function EscapeHatch({ onShuffle }: { onShuffle?: () => void }) {
  const navigate = useNavigate();
  return (
    <div className="flex items-center justify-center gap-4 border-b bg-muted/50 px-4 py-1.5 text-sm">
      <span className="text-muted-foreground">
        You're seeing a personalised page.
      </span>
      {onShuffle ? (
        <Button
          variant="link"
          size="sm"
          className="h-auto p-0"
          onClick={onShuffle}
        >
          Feeling lucky again
        </Button>
      ) : null}
      <Button
        variant="link"
        size="sm"
        className="h-auto p-0"
        onClick={() => {
          clearStamp();
          navigate({ to: "/" });
        }}
      >
        Start over
      </Button>
    </div>
  );
}
