/**
 * Apple-Pages-style writing-tools popover for the letter body: appears at the textarea
 * selection with three preset rewrite styles, a free instruction, and a hand-off into
 * the refinement chat. Rewrite rides the user's default executor (the alias picker died
 * with the rework). Pure view — the caller owns the selection range, runs the mutation,
 * and splices the result back.
 */
import { useState } from "react";
import { MessageSquare, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { REWRITE_STYLES } from "@/lib/letter-chat";

/**
 * Where the selection end sits inside the textarea, relative to its offset parent:
 * a hidden mirror div gets the textarea's layout-relevant styles plus the text up to
 * `index`; a zero-width marker span's offset is the caret position. Runs once per
 * selection change — cheap enough to skip memoising.
 */
const MIRROR_PROPS = [
  "box-sizing",
  "font-family",
  "font-size",
  "font-weight",
  "line-height",
  "letter-spacing",
  "padding-top",
  "padding-right",
  "padding-bottom",
  "padding-left",
  "border-top-width",
  "border-right-width",
  "border-bottom-width",
  "border-left-width",
  "word-break",
  "overflow-wrap",
  "tab-size",
];

export function caretOffset(
  el: HTMLTextAreaElement,
  index: number,
): { top: number; left: number } {
  const mirror = document.createElement("div");
  const cs = window.getComputedStyle(el);
  for (const p of MIRROR_PROPS)
    mirror.style.setProperty(p, cs.getPropertyValue(p));
  mirror.style.position = "absolute";
  mirror.style.visibility = "hidden";
  mirror.style.whiteSpace = "pre-wrap";
  mirror.style.width = `${el.clientWidth}px`;
  mirror.textContent = el.value.slice(0, index);
  const marker = document.createElement("span");
  marker.textContent = "\u200b";
  mirror.appendChild(marker);
  (el.offsetParent ?? document.body).appendChild(mirror);
  const top = el.offsetTop + marker.offsetTop - el.scrollTop;
  const left = el.offsetLeft + marker.offsetLeft;
  mirror.remove();
  return { top, left };
}

export function RewritePopover({
  anchor,
  pending,
  onRewrite,
  onDiscuss,
  onClose,
}: {
  anchor: { top: number; left: number; flip?: boolean };
  pending: boolean;
  onRewrite: (instruction: string) => void;
  onDiscuss: () => void;
  onClose: () => void;
}) {
  const [instruction, setInstruction] = useState("");
  // The surrounding Card clips overflow, so a panel dropped below a low selection
  // gets cut off — `flip` renders it above the caret instead (translateY needs no
  // height measurement).
  const left = Math.max(0, anchor.left - 40);
  const placement = anchor.flip
    ? { top: anchor.top - 6, left, transform: "translateY(-100%)" }
    : { top: anchor.top + 22, left };
  return (
    <div
      className="absolute z-20 w-80 max-w-full space-y-2 rounded-lg border bg-popover p-2 shadow-lg"
      style={placement}
    >
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium">
          {pending ? "Rewriting…" : "Rewrite selection"}
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="ml-auto h-7 w-7"
          aria-label="Close"
          onClick={onClose}
        >
          <X className="h-4 w-4" />
        </Button>
      </div>
      <div className="flex flex-wrap gap-1">
        {REWRITE_STYLES.map((s) => (
          <Button
            key={s.key}
            size="sm"
            variant="secondary"
            disabled={pending}
            onClick={() => onRewrite(s.instruction)}
          >
            {s.label}
          </Button>
        ))}
      </div>
      <div className="flex gap-1">
        <Input
          className="h-8 text-xs"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && instruction.trim() && !pending)
              onRewrite(instruction);
          }}
          placeholder="Or tell it what to change…"
        />
        <Button
          size="sm"
          variant="outline"
          disabled={pending || !instruction.trim()}
          onClick={() => onRewrite(instruction)}
        >
          Go
        </Button>
      </div>
      <Button
        size="sm"
        variant="ghost"
        className="w-full justify-start"
        onClick={onDiscuss}
      >
        <MessageSquare className="mr-1 h-4 w-4" /> Discuss in chat
      </Button>
    </div>
  );
}
