import { Link } from "@tanstack/react-router";
import { useOwnerHome } from "@/components/portfolio/use-owner-home";
import { Button } from "@/components/ui/button";
import { GUTTER } from "@/lib/layout";
import { clearStamp } from "@/lib/portfolio/stamp";
import { cn } from "@/lib/utils";

/** Shown on every personalised view so the visitor is never trapped. "What's this?" explains
 *  the page (/about/); "Start over" clears this owner's stamp and returns to their
 *  questionnaire; the optional "Feeling lucky again" reshuffles a lucky view in place (parent
 *  passes onShuffle). */
export function EscapeHatch({
  handle,
  onShuffle,
  label = "You're seeing a personalised page.",
}: {
  handle: string;
  onShuffle?: () => void;
  /** What this page is, when it isn't (yet) the personalised one. */
  label?: string;
}) {
  const goHome = useOwnerHome(handle);
  return (
    <div className="border-b">
      <div
        className={cn(
          "mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-4 gap-y-1 py-3.5 text-sm",
          GUTTER,
        )}
      >
        <span className="text-muted-foreground">
          {label}{" "}
          <Link
            to="/about/"
            search={{ from: handle }}
            className="underline underline-offset-4 hover:text-foreground"
          >
            What's this?
          </Link>
        </span>
        <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
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
        </span>
      </div>
    </div>
  );
}
