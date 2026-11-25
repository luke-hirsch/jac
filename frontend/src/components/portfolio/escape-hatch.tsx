import { Link } from "@tanstack/react-router";
import { StudioButton } from "@/components/portfolio/studio";
import { useOwnerHome } from "@/components/portfolio/use-owner-home";
import { clearStamp } from "@/lib/portfolio/stamp";

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
      <div className="mx-auto flex max-w-[1160px] flex-wrap items-center justify-between gap-x-4 gap-y-1 px-[clamp(1rem,4vw,3rem)] py-3.5 text-sm">
        <span className="text-muted-foreground">
          {label}{" "}
          <Link
            to="/about/"
            search={{ from: handle }}
            className="underline underline-offset-[3px] hover:text-foreground"
          >
            What's this?
          </Link>
        </span>
        <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
          {onShuffle ? (
            <StudioButton
              variant="link"
              size="sm"
              className="h-auto p-0"
              onClick={onShuffle}
            >
              Feeling lucky again
            </StudioButton>
          ) : null}
          <StudioButton
            variant="link"
            size="sm"
            className="h-auto p-0"
            onClick={() => {
              clearStamp(handle);
              goHome();
            }}
          >
            Start over
          </StudioButton>
        </span>
      </div>
    </div>
  );
}
