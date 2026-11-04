import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { type ApplicationRow } from "@/lib/queries/applications";

export function PostingCard({ app }: { app: ApplicationRow }) {
  const [open, setOpen] = useState(false);
  const posting = app.posting_detail;
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Job posting</CardTitle>
        <div className="flex items-center gap-2">
          <Badge variant="outline">{posting.language}</Badge>
          {!posting.active && <Badge variant="destructive">inactive</Badge>}
        </div>
      </CardHeader>
      <CardContent>
        <pre
          className={`whitespace-pre-wrap font-sans text-sm text-muted-foreground ${
            open ? "" : "max-h-32 overflow-hidden"
          }`}
        >
          {posting.posting_text}
        </pre>
        <Button variant="ghost" size="sm" onClick={() => setOpen(!open)}>
          {open ? "Collapse" : "Show full posting"}
        </Button>
      </CardContent>
    </Card>
  );
}
