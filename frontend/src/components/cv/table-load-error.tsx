import { QueryError } from "@/components/query-error";
import { TableCell, TableRow } from "@/components/ui/table";

/** The table row for a list that failed to load, in place of its "No … yet" row. */
export function TableLoadError({
  list,
  what,
  colSpan,
}: {
  list: { error: unknown; isFetching: boolean; refetch: () => unknown };
  what: string;
  colSpan: number;
}) {
  return (
    <TableRow>
      <TableCell colSpan={colSpan}>
        <QueryError
          error={list.error}
          what={what}
          onRetry={() => list.refetch()}
          retrying={list.isFetching}
        />
      </TableCell>
    </TableRow>
  );
}
