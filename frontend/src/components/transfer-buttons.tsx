import { useRef } from "react";
import { toast } from "sonner";
import { Lines } from "@/components/toast-lines";
import { Button } from "@/components/ui/button";
import { errorLines } from "@/lib/queries/skill";
import {
  importedSummary,
  useExport,
  useImport,
  type TransferKind,
} from "@/lib/queries/transfer";

/** Download the career or the applications as the file the matching ImportButton takes. */
export function ExportButton({
  kind,
  label,
}: {
  kind: TransferKind;
  label: string;
}) {
  const download = useExport(kind);
  return (
    <Button
      variant="outline"
      size="sm"
      disabled={download.isPending}
      onClick={() =>
        download.mutate(undefined, {
          onError: (e) => toast.error(errorLines(e, "Download failed.")[0]),
        })
      }
    >
      {download.isPending ? "Preparing…" : label}
    </Button>
  );
}

/** Pick a file an ExportButton wrote and import it: the server refuses unless the career (or
 *  the application list) is empty, and says why. */
export function ImportButton({
  kind,
  label,
}: {
  kind: TransferKind;
  label: string;
}) {
  const upload = useImport(kind);
  const fileRef = useRef<HTMLInputElement>(null);

  // mutateAsync, not mutate's callbacks: the refetch that ends the import can unmount the card
  // this sits in (the empty-career card does), and callbacks passed to mutate() don't fire then.
  async function onFile(file: File) {
    let result;
    try {
      result = await upload.mutateAsync(file);
    } catch (e) {
      toast.error("Could not import the file", {
        description: <Lines lines={errorLines(e, "Try again in a moment.")} />,
        duration: 10_000,
      });
      return;
    }
    const summary = importedSummary(kind, result.created);
    if (result.warnings.length)
      toast.warning(`${summary}, with warnings`, {
        description: <Lines lines={result.warnings} />,
        duration: 10_000,
      });
    else toast.success(summary);
  }

  return (
    <>
      <input
        ref={fileRef}
        type="file"
        accept=".json,application/json"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = ""; // the same file again is a new pick
          if (file) onFile(file);
        }}
      />
      <Button
        variant="outline"
        disabled={upload.isPending}
        onClick={() => fileRef.current?.click()}
      >
        {upload.isPending ? "Importing…" : label}
      </Button>
    </>
  );
}
