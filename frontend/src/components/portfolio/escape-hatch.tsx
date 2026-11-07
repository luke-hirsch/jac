import { Button } from "@/components/ui/button";
import { useOwnerHome } from "@/components/portfolio/use-owner-home";
import { clearStamp } from "@/lib/portfolio/stamp";

/** Shown on every personalised view so the visitor is never trapped. "Start over"
 *  clears this owner's stamp and returns to their questionnaire; the optional
 *  "Feeling lucky again" reshuffles a lucky view in place (parent passes onShuffle). */
export function EscapeHatch({
  handle,
  onShuffle,
}: {
  handle: string;
  onShuffle?: () => void;
}) {
  const goHome = useOwnerHome(handle);
  return (
    <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1 border-b bg-muted/50 px-4 py-1.5 text-sm">
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
          clearStamp(handle);
          goHome();
        }}
      >
        Start over
      </Button>
    </div>
  );
}
