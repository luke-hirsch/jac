import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ContentPicker } from "@/components/portfolio/content-picker";
import { useFullList, type DomainRow } from "@/lib/queries/jac";
import {
  useCreateLink,
  useUpdateLink,
  type PortfolioLinkRow,
} from "@/lib/queries/portfolio";
import { toggleName } from "@/lib/portfolio/link-form";

const RESERVED = new Set(["links", "blocks"]); // the /portfolio tab paths

type Draft = {
  slug: string;
  title: string;
  intro: string;
  featured: string[];
  domains: string[];
  hide_explore: boolean;
};

function draftFrom(link: PortfolioLinkRow | null): Draft {
  return {
    slug: link?.slug ?? "",
    title: link?.title ?? "",
    intro: link?.intro ?? "",
    featured: link?.content.featured ?? [],
    domains: link?.content.domains ?? [],
    hide_explore: link?.content.hide_explore ?? false,
  };
}

/** Create (link=null) or edit a manual link. Application links are editable too — the
 *  slug field is read-only for them (server-owned). */
export function LinkEditor({
  open,
  link,
  onOpenChange,
}: {
  open: boolean;
  link: PortfolioLinkRow | null;
  onOpenChange: (open: boolean) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => draftFrom(link));
  const domains = useFullList<DomainRow>("domains");
  const create = useCreateLink();
  const update = useUpdateLink();
  const isApplication = link?.kind === "application";
  const busy = create.isPending || update.isPending;

  function set<K extends keyof Draft>(k: K, v: Draft[K]) {
    setDraft((d) => ({ ...d, [k]: v }));
  }

  function submit() {
    const slug = draft.slug.trim();
    if (!isApplication && RESERVED.has(slug)) {
      toast.error(`"${slug}" is reserved — pick another slug.`);
      return;
    }
    const content = {
      featured: draft.featured,
      domains: draft.domains,
      hide_explore: draft.hide_explore,
    };
    const onSuccess = () => {
      toast.success(link ? "Link updated" : "Link created");
      onOpenChange(false);
    };
    const onError = () => toast.error("Couldn't save the link");
    if (link) {
      // A frozen application link keeps its slug; only send editable fields.
      const input = isApplication
        ? { title: draft.title, intro: draft.intro, content }
        : { slug, title: draft.title, intro: draft.intro, content };
      update.mutate({ id: link.id, input }, { onSuccess, onError });
    } else {
      create.mutate(
        { slug, title: draft.title, intro: draft.intro, content },
        { onSuccess, onError },
      );
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="grid max-h-[calc(100dvh-2rem)] grid-rows-[auto_minmax(0,1fr)_auto] gap-4 overflow-hidden sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{link ? "Edit link" : "New portfolio link"}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 overflow-y-auto px-1">
          <div className="grid gap-2">
            <Label htmlFor="slug">Slug</Label>
            <Input
              id="slug"
              value={draft.slug}
              disabled={isApplication}
              placeholder="for-jane"
              onChange={(e) => set("slug", e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Public at /portfolio/{draft.slug || "…"}
            </p>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="title">Title</Label>
            <Input
              id="title"
              value={draft.title}
              onChange={(e) => set("title", e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="intro">Intro</Label>
            <Textarea
              id="intro"
              value={draft.intro}
              onChange={(e) => set("intro", e.target.value)}
            />
          </div>

          <div className="grid gap-2">
            <Label>Featured content</Label>
            <ContentPicker
              selected={draft.featured}
              onChange={(f) => set("featured", f)}
            />
          </div>

          <div className="grid gap-2">
            <Label>Explore domains (deepen the "more" section)</Label>
            <div className="flex flex-wrap gap-2">
              {(domains.data ?? []).map((d) => (
                <label
                  key={d.id}
                  className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-sm"
                >
                  <Checkbox
                    checked={draft.domains.includes(d.name)}
                    onCheckedChange={() =>
                      set("domains", toggleName(draft.domains, d.name))
                    }
                  />
                  {d.name}
                </label>
              ))}
            </div>
          </div>

          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={draft.hide_explore}
              onCheckedChange={(v) => set("hide_explore", v === true)}
            />
            Hide the "more to explore" section (featured only)
          </label>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={busy} onClick={submit}>
            {link ? "Save" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
