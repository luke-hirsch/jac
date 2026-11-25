import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

/** Inside a card, every heading sits under the card's own title (an h3), whatever level the owner
 *  typed: a `#` in a text block must not make a second h1 on the page. */
const Heading = ({ children }: { children?: ReactNode }) => (
  <h4 className="font-display text-lg font-semibold text-foreground">
    {children}
  </h4>
);

const COMPONENTS: Components = {
  h1: Heading,
  h2: Heading,
  h3: Heading,
  h4: Heading,
  h5: Heading,
  h6: Heading,
  // An in-page jump stays; everything else opens in a new tab and doesn't tell the other site
  // where the visitor came from.
  a: ({ href, children }) =>
    href?.startsWith("#") ? (
      <a
        href={href}
        className="text-foreground underline underline-offset-[3px]"
      >
        {children}
      </a>
    ) : (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="text-foreground underline underline-offset-[3px]"
      >
        {children}
      </a>
    ),
  strong: ({ children }) => (
    <strong className="font-semibold text-foreground">{children}</strong>
  ),
  ul: ({ children }) => (
    <ul className="list-disc space-y-1 pl-5 marker:text-muted-foreground">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal space-y-1 pl-5 marker:text-muted-foreground">
      {children}
    </ol>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-studio-accent pl-4">
      {children}
    </blockquote>
  ),
  code: ({ children }) => (
    <code className="rounded bg-studio-raise px-1.5 py-0.5 text-[0.9em]">
      {children}
    </code>
  ),
  pre: ({ children }) => (
    <pre className="overflow-x-auto rounded-lg bg-studio-raise p-3 text-sm [&_code]:bg-transparent [&_code]:p-0">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm [&_td]:border-t [&_td]:py-1.5 [&_td]:pr-4 [&_th]:pr-4 [&_th]:pb-1.5 [&_th]:font-semibold">
        {children}
      </table>
    </div>
  ),
  hr: () => <hr className="border-border" />,
};

/** A text block's body: the owner's markdown, GitHub-flavoured. No raw HTML, and no images: a
 *  markdown image loads from wherever it points, for every visitor, and the portfolio loads no
 *  third party. Pictures are image blocks, uploaded to jac. */
export function StudioMarkdown({ source }: { source: string }) {
  return (
    <div className="flex flex-col gap-2.5 leading-relaxed text-studio-soft">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        disallowedElements={["img"]}
        unwrapDisallowed
        components={COMPONENTS}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
