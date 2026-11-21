import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/** Consent before data leaves the EU (Art. 49(1)(a) DSGVO): an "I agree", not a notice to wave
 *  away. The page that shows it unlocks its inputs on the click, and the server stamps the
 *  consent on what's saved next: an API key (Account → LLM providers) or a token (Account →
 *  LLM → Claude). Deleting that withdraws it. */
export function TransferConsent({
  children,
  consent,
  onAgree,
}: {
  /** What goes where, and whose terms apply. */
  children: ReactNode;
  /** The sentence the user agrees to. */
  consent: string;
  onAgree: () => void;
}) {
  return (
    <Card className="border border-destructive/50">
      <CardHeader>
        <CardTitle className="text-base">Your data leaves the EU</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {children}
        <p>{consent}</p>
        <Button size="sm" onClick={onAgree}>
          I agree
        </Button>
      </CardContent>
    </Card>
  );
}
