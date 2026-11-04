export function FieldError({ msg }: { msg?: string }) {
  if (!msg) return null;
  return <p className="text-sm text-destructive">{msg}</p>;
}
