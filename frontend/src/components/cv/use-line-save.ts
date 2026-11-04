/**
 * Line-by-line saving for the CV CRUD editors: each field of an EXISTING row is
 * PATCHed on its own as the user leaves it (blur for text, change for discrete
 * widgets), with per-field saving/saved/error feedback. Disabled in create mode
 * (`id == null`) — there the whole-form submit still does the work.
 */
import { useRef, useState } from "react";
import {
  drfFieldError,
  shouldSend,
  type FieldSaveState,
} from "@/lib/field-save";
import { useUpdate, type ResourceKey } from "@/lib/queries/jac";

export function useLineSave<Row>(
  resource: ResourceKey,
  id: number | null,
  initial: Record<string, unknown>,
) {
  const update = useUpdate<Row, Record<string, unknown>>(resource);
  const [fields, setFields] = useState<Record<string, FieldSaveState>>({});
  // What the server last accepted per field — blur fires on every tab-through,
  // so unchanged values must not re-PATCH. Seeded from the row's initial values.
  const lastSent = useRef<Record<string, unknown>>({ ...initial });

  const enabled = id != null;

  /** PATCH one field. `clientErrors` = the field's validator errors — an
   *  invalid value is never sent (its zod message is already showing). */
  function save(field: string, value: unknown, clientErrors: Array<unknown> = []) {
    if (id == null) return;
    if (!shouldSend(value, lastSent.current[field], clientErrors)) return;
    setFields((s) => ({ ...s, [field]: { state: "saving" } }));
    update.mutate(
      { id, body: { [field]: value } },
      {
        onSuccess: () => {
          lastSent.current[field] = value;
          setFields((s) => ({ ...s, [field]: { state: "saved" } }));
        },
        onError: (e) => {
          setFields((s) => ({
            ...s,
            [field]: { state: "error", message: drfFieldError(e, field) },
          }));
        },
      },
    );
  }

  return { enabled, fields, save };
}

export type LineSave = ReturnType<typeof useLineSave>;
