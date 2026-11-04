import { z, ZodType } from "zod";

export function zodValidator<T>(schema: ZodType<T>) {
  return ({ value }: { value: T }) => {
    const result = schema.safeParse(value);
    if (result.success) return undefined;
    const fields: Record<string, string> = {};
    for (const issue of result.error.issues) {
      const key = issue.path.join(".");
      if (!fields[key]) fields[key] = issue.message;
    }
    return { fields };
  };
}

export { z };
