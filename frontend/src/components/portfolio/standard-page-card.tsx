import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ContentPicker } from "@/components/portfolio/content-picker";
import {
  isStandardEmpty,
  standardBody,
  standardChanged,
  standardDraft,
  type StandardDraft,
} from "@/lib/portfolio/standard-page";
import {
  useSaveStandardPage,
  useStandardPage,
  type StandardPageRow,
} from "@/lib/queries/portfolio";

/** The owner's standard page: what their portfolio shows a visitor who skips the questions or
 *  answers them without a pick. Edited like a link, saved on its own endpoint. */
export function StandardPageCard() {
  const page = useStandardPage();
  if (page.isPending) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }
  if (!page.data) {
    return (
      <p className="text-sm text-destructive">
        Couldn't load the standard page.
      </p>
    );
  }
  return <StandardPageForm row={page.data} />;
}

function StandardPageForm({ row }: { row: StandardPageRow }) {
  const [draft, setDraft] = useState<StandardDraft>(() => standardDraft(row));
  const save = useSaveStandardPage();

  function set<K extends keyof StandardDraft>(k: K, v: StandardDraft[K]) {
    setDraft((d) => ({ ...d, [k]: v }));
  }

  function submit() {
    save.mutate(standardBody(draft), {
      onSuccess: () => toast.success("Standard page saved"),
      onError: () => toast.error("Couldn't save the standard page"),
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Standard page</CardTitle>
        <CardDescription>
          What your portfolio shows a visitor who skips the questions, or
          answers them without picking a topic.
        </CardDescription>
        {isStandardEmpty(draft) ? (
          <CardAction>
            <Badge variant="secondary">Not set up yet</Badge>
          </CardAction>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <code className="min-w-0 flex-1 truncate">{row.url}</code>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              navigator.clipboard.writeText(row.url);
              toast.success("Address copied");
            }}
          >
            Copy
          </Button>
          <Button variant="ghost" size="sm" asChild>
            <a href={row.url} target="_blank" rel="noreferrer">
              Open
            </a>
          </Button>
        </div>

        <div className="grid gap-2">
          <Label htmlFor="standard-title">Title</Label>
          <Input
            id="standard-title"
            value={draft.title}
            placeholder="Your display name"
            onChange={(e) => set("title", e.target.value)}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="standard-intro">Intro</Label>
          <Textarea
            id="standard-intro"
            value={draft.intro}
            placeholder="Your profile bio"
            onChange={(e) => set("intro", e.target.value)}
          />
        </div>

        <div className="grid gap-2">
          <Label>Highlights</Label>
          <ContentPicker
            selected={draft.featured}
            onChange={(f) => set("featured", f)}
          />
          <p className="text-xs text-muted-foreground">
            With nothing picked here, your favourites are the Highlights. A
            visitor who picks a topic sees the Highlights tagged with it, or
            your favourites in it when none are.
          </p>
        </div>
      </CardContent>
      <CardFooter className="justify-end">
        <Button
          disabled={save.isPending || !standardChanged(row, draft)}
          onClick={submit}
        >
          Save
        </Button>
      </CardFooter>
    </Card>
  );
}
