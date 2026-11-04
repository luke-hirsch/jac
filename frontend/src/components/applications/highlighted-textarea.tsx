/**
 * A textarea with a highlighted tail. A textarea can't style ranges, so this is the
 * standard mirror trick: an absolutely positioned div renders the same text with the
 * overflow wrapped in a <mark>, and the textarea sits on top with a transparent
 * background. The two MUST share every metric that affects wrapping — font, size, line
 * height, padding, border width, width — or the highlight drifts from the text.
 *
 * The mirror's text is transparent: only the <mark>'s background shows through, so the
 * glyphs are drawn once (by the textarea) and a pixel of drift can't ghost the text.
 *
 * If the mirror turns out to drift in practice (log it in Results), the fallback is a
 * read-only "this will not fit on page 1" block under the textarea: less pretty, zero
 * alignment risk.
 *
 * `children` is the overlay slot, and it is not cosmetic: this wrapper is `relative`, so
 * it becomes the textarea's `offsetParent`, and `caretOffset` (rewrite-popover.tsx)
 * returns coordinates relative to exactly that. Anything positioned from those
 * coordinates — the rewrite popover — MUST render inside this box or it lands offset by
 * the label's height.
 */
import { useRef, type KeyboardEvent, type ReactNode, type RefObject } from "react";

import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

/** The wrapping metrics of `ui/textarea.tsx`, mirrored: same border box, padding and
 *  type scale. Change that component's spacing and this has to follow. `border-transparent`
 *  is not decoration — without the same border box the two drift by a pixel per side. */
const MIRROR =
  "w-full border border-transparent px-0 py-3 text-base md:text-sm " +
  "whitespace-pre-wrap break-words";

export function HighlightedTextarea({
  id,
  value,
  onChange,
  overflowAt,
  rows = 14,
  className,
  textareaRef,
  onSelect,
  onKeyDown,
  placeholder,
  readOnly,
  children,
}: {
  id?: string;
  value: string;
  onChange: (v: string) => void;
  /** Character index where the text stops fitting, or null. */
  overflowAt: number | null;
  rows?: number;
  className?: string;
  textareaRef?: RefObject<HTMLTextAreaElement | null>;
  onSelect?: () => void;
  onKeyDown?: (e: KeyboardEvent<HTMLTextAreaElement>) => void;
  placeholder?: string;
  readOnly?: boolean;
  /** Overlay slot — see the offsetParent note above. */
  children?: ReactNode;
}) {
  const mirror = useRef<HTMLDivElement>(null);
  const own = useRef<HTMLTextAreaElement>(null);
  const ref = textareaRef ?? own;
  const cut = overflowAt != null && overflowAt < value.length ? overflowAt : null;

  return (
    <div className={cn("relative", className)}>
      <div
        ref={mirror}
        aria-hidden
        className={cn(
          MIRROR,
          "pointer-events-none absolute inset-0 overflow-hidden text-transparent",
        )}
      >
        {cut == null ? (
          value
        ) : (
          <>
            {value.slice(0, cut)}
            <mark className="bg-amber-200/70 text-transparent dark:bg-amber-500/30">
              {value.slice(cut)}
            </mark>
          </>
        )}
      </div>
      <Textarea
        id={id}
        ref={ref}
        rows={rows}
        value={value}
        placeholder={placeholder}
        readOnly={readOnly}
        onChange={(e) => onChange(e.target.value)}
        onSelect={onSelect}
        onKeyDown={onKeyDown}
        onScroll={() => {
          if (mirror.current && ref.current)
            mirror.current.scrollTop = ref.current.scrollTop;
        }}
        className={cn("relative", readOnly && "cursor-progress opacity-70")}
      />
      {children}
    </div>
  );
}
