import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/** Consent before data leaves the EU (Art. 49(1)(a) DSGVO): an "I agree", not a notice to wave
 *  away. Given once and kept, like a cookie choice (`lib/queries/consent.ts`): for API keys on
 *  Account → LLM providers, for Claude tokens on Account → LLM → Claude. Once given, the page
 *  shows `ConsentKept` instead. */
export function TransferConsent({
  children,
  consent,
  onAgree,
  pending = false,
}: {
  /** What goes where, and whose terms apply. */
  children: ReactNode;
  /** The sentence the user agrees to. */
  consent: string;
  onAgree: () => void;
  pending?: boolean;
}) {
  return (
    <Card className="border border-destructive/50">
      <CardHeader>
        <CardTitle className="text-base">Your data leaves the EU</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {children}
        <p>{consent}</p>
        <Button size="sm" onClick={onAgree} disabled={pending}>
          {pending ? "Saving…" : "I agree"}
        </Button>
      </CardContent>
    </Card>
  );
}

/** Where the card was, once the consent is kept: when it was given, and the way back out.
 *  Withdrawing is as easy as agreeing (Art. 7(3)), and deletes what it covered. */
export function ConsentKept({
  givenAt,
  withdrawWarning,
  onWithdraw,
  pending = false,
}: {
  /** null when only a saved key or token shows it (the kept record is still loading). */
  givenAt: string | null;
  /** The confirm text: what withdrawing deletes. */
  withdrawWarning: string;
  onWithdraw: () => void;
  pending?: boolean;
}) {
  const when = givenAt
    ? ` on ${new Date(givenAt).toLocaleDateString(undefined, { dateStyle: "medium" })}`
    : "";
  return (
    <p className="text-xs text-muted-foreground">
      You agreed{when} that this data may leave the EU.{" "}
      <button
        type="button"
        className="underline hover:text-foreground disabled:opacity-50"
        disabled={pending}
        onClick={() => {
          if (confirm(withdrawWarning)) onWithdraw();
        }}
      >
        {pending ? "Withdrawing…" : "Withdraw"}
      </button>
    </p>
  );
}
