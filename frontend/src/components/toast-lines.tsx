/** One message per line, for a toast that has several to say. */
export function Lines({ lines }: { lines: string[] }) {
  return (
    <div className="space-y-0.5">
      {lines.map((line, i) => (
        <p key={i}>{line}</p>
      ))}
    </div>
  );
}
