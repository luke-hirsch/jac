import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function MarkdownPreview({ source }: { source: string }) {
  if (!source.trim()) {
    return (
      <p className="text-xs text-muted-foreground italic">
        Markdown preview appears here.
      </p>
    );
  }
  return (
    <div className="prose prose-sm max-w-none dark:prose-invert">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{source}</ReactMarkdown>
    </div>
  );
}
