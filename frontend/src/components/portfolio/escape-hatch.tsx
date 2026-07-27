import { useNavigate } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import { clearStamp } from "@/lib/portfolio/stamp";

/** Shown on every personalised view: the visitor can always step out to the general
 *  site. Clearing the stamp is what stops "/" from redirecting them back. */
export function EscapeHatch() {
  const navigate = useNavigate();
  return (
    <div className="flex items-center justify-center gap-3 border-b bg-muted/50 px-4 py-1.5 text-sm">
      <span className="text-muted-foreground">
        You're seeing a personalised page.
      </span>
      <Button
        variant="link"
        size="sm"
        className="h-auto p-0"
        onClick={() => {
          clearStamp();
          navigate({ to: "/explore", search: {} });
        }}
      >
        View the general site
      </Button>
    </div>
  );
}
