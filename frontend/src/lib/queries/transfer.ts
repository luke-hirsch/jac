import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { downloadText } from "@/lib/export";

/** What jac moves as files, each with its own download and import: the career (the
 *  `cv_import` shape, backend jac/career_export.py) and the applications, whose CV picks name
 *  career entries by natural key (jac/application_transfer.py). An import only ever fills an
 *  empty career or an empty application list. */
export type TransferKind = "career" | "applications";

const BASE: Record<TransferKind, string> = {
  career: "/api/jac/career/",
  applications: "/api/jac/applications/",
};

export type ImportResult = {
  created: Record<string, number>;
  warnings: string[];
};

export function fetchExport(kind: TransferKind): Promise<unknown> {
  return api(`${BASE[kind]}export/`);
}

/** A multipart upload, not a JSON body: Django refuses a body over 2.5 MB, and a list of
 *  applications with their postings can be that. */
export function postImport(
  kind: TransferKind,
  file: File,
): Promise<ImportResult> {
  const body = new FormData();
  body.append("file", file);
  return api<ImportResult>(`${BASE[kind]}import/`, { method: "POST", body });
}

/** `career-YYYY-MM-DD.json` on the local date: `toISOString()` is UTC, still yesterday for the
 *  first hour or two after midnight in Berlin. */
export function exportFileName(kind: TransferKind, now = new Date()): string {
  const day = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-");
  return `${kind}-${day}.json`;
}

/** The success toast: what landed. Domains and locations are references, not entries. */
export function importedSummary(
  kind: TransferKind,
  created: Record<string, number>,
): string {
  if (kind === "applications") {
    const n = created.applications ?? 0;
    return `${n} application${n === 1 ? "" : "s"} imported`;
  }
  const n = Object.entries(created)
    .filter(([section]) => section !== "domains" && section !== "locations")
    .reduce((sum, [, count]) => sum + count, 0);
  return `${n} career entr${n === 1 ? "y" : "ies"} imported`;
}

/** Fetch the export and hand it to the browser as a file: indent 2 + a newline, the layout
 *  `cv_export --file` writes, so a diff of the two shows only data. */
export function useExport(kind: TransferKind) {
  return useMutation({
    mutationFn: async () =>
      downloadText(
        `${JSON.stringify(await fetchExport(kind), null, 2)}\n`,
        exportFileName(kind),
        "application/json",
      ),
  });
}

export function useImport(kind: TransferKind) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => postImport(kind, file),
    // On a 409 too: then another tab filled it, and this page is the one behind.
    onSettled: () => qc.invalidateQueries({ queryKey: ["jac"] }),
  });
}
