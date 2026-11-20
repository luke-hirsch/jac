import { toast } from "sonner";
import { Lines } from "@/components/toast-lines";
import { staleLines, useImportCareerChanges } from "@/lib/queries/skill";

/** Apply the career changes from a file Claude handed over (`parseImportFile`), and toast how
 *  it went: how many were applied, with `hint` beneath, or what the server refused. Shared by
 *  account → Claude and applications → Import from Claude. */
export function useImportChangesFile(hint?: string) {
  const importChanges = useImportCareerChanges();

  async function apply(changes: unknown[]) {
    let batch;
    try {
      batch = await importChanges.mutateAsync(changes);
    } catch (e) {
      toast.error("Could not import the changes", {
        description: <Lines lines={staleLines(e, "Try again in a moment.")} />,
        duration: 10_000,
      });
      return;
    }
    const n = batch.changes.length;
    toast.success(`${n} change${n === 1 ? "" : "s"} applied`, {
      description: hint,
    });
  }

  return { apply, isPending: importChanges.isPending };
}
