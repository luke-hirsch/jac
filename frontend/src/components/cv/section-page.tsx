import { type ReactNode } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

export function SectionPage<EditorRow>({
  title,
  description,
  search,
  onSearchChange,
  filters,
  table,
  editor,
  open,
  editing,
  onOpenChange,
  onNew,
}: {
  title: string;
  description: string;
  search: string;
  onSearchChange: (v: string) => void;
  filters?: ReactNode;
  table: ReactNode;
  editor: (row: EditorRow | null, close: () => void) => ReactNode;
  open: boolean;
  editing: EditorRow | null;
  onOpenChange: (open: boolean) => void;
  onNew: () => void;
}) {
  return (
    <div className="space-y-4">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">{title}</h1>
          <p className="text-sm text-muted-foreground">{description}</p>
        </div>
        <Button onClick={onNew}>
          <Plus className="size-4" /> New
        </Button>
      </header>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          placeholder="Search…"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          className="max-w-xs"
        />
        {filters}
      </div>
      {table}
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent className="w-full sm:max-w-2xl overflow-y-auto">
          <SheetHeader>
            <SheetTitle>
              {editing ? "Edit" : "New"} {title.toLowerCase()}
            </SheetTitle>
          </SheetHeader>
          {/* Key by row id so the form fully remounts (and re-reads its
              defaultValues) whenever the edited row — or new/edit mode —
              changes. Without this the editor keeps a previous row's form
              state, so edits to fields like `name` silently don't apply. */}
          <div
            className="mt-4"
            key={
              editing
                ? `edit-${(editing as { id?: number }).id ?? "?"}`
                : "new"
            }
          >
            {editor(editing, () => onOpenChange(false))}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
