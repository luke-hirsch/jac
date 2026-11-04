import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { anchorId, linkTarget, outboundUrl } from "@/lib/portfolio/content";
import type { PortfolioItem } from "@/lib/queries/portfolio";

function dates(item: PortfolioItem): string {
  if (!item.started && !item.ended) return "";
  const from = item.started?.slice(0, 4) ?? "";
  const to = item.ended ? item.ended.slice(0, 4) : "today";
  return from ? `${from} – ${to}` : "";
}

/** A block's nested links. Each title is clickable when it has somewhere to go:
 *  on-page jump to the item's own card, else out to the entry's url (see linkTarget).
 *  The anchor scrolls smoothly instead of hard-jumping — href stays for middle-click. */
function LinkedItems({
  items,
  anchors,
}: {
  items: PortfolioItem[];
  anchors: Set<string>;
}) {
  return (
    <div className="mt-2 space-y-1 border-l-2 pl-3">
      {items.map((li) => {
        const target = linkTarget(li, anchors);
        const outbound = outboundUrl(li, target);
        return (
          <div key={li.id} className="text-sm">
            {target === null ? (
              <span className="font-medium">{li.title}</span>
            ) : target.kind === "anchor" ? (
              <a
                href={target.href}
                className="font-medium underline underline-offset-2"
                onClick={(e) => {
                  const el = document.getElementById(anchorId(li.id));
                  if (!el) return; // no card after all — let the browser try the hash
                  e.preventDefault();
                  el.scrollIntoView({ behavior: "smooth", block: "start" });
                }}
              >
                {li.title}
              </a>
            ) : (
              <a
                href={target.href}
                target="_blank"
                rel="noreferrer"
                className="font-medium underline underline-offset-2"
              >
                {li.title} ↗
              </a>
            )}
            {li.subtitle ? (
              <span className="text-muted-foreground"> · {li.subtitle}</span>
            ) : null}
            {outbound ? (
              <a
                href={outbound}
                target="_blank"
                rel="noreferrer"
                aria-label={`${li.title} (external link)`}
                className="text-muted-foreground ml-1"
              >
                ↗
              </a>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

export function ItemCard({
  item,
  anchors,
}: {
  item: PortfolioItem;
  /** Ids that render as a card on this page — nested links jump there instead of
   *  leaving the site. Defaults to empty (a card rendered outside a full page). */
  anchors?: Set<string>;
}) {
  const jumpTargets = anchors ?? new Set<string>();
  if (item.type === "block" && item.kind === "image") {
    return (
      <Card id={anchorId(item.id)} className="overflow-hidden scroll-mt-6">
        {item.image_url ? (
          <img
            src={item.image_url}
            alt={item.alt_text || item.title}
            className="w-full object-cover"
          />
        ) : null}
        {item.title || item.body ? (
          <CardContent className="pt-4 space-y-1">
            {item.title ? <p className="font-medium">{item.title}</p> : null}
            {item.body ? (
              <p className="text-sm text-muted-foreground">{item.body}</p>
            ) : null}
            {item.links?.length ? (
              <LinkedItems items={item.links} anchors={jumpTargets} />
            ) : null}
          </CardContent>
        ) : null}
      </Card>
    );
  }

  const when = dates(item);
  return (
    <Card id={anchorId(item.id)} className="scroll-mt-6">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{item.title}</CardTitle>
        {(item.subtitle || when) && (
          <p className="text-sm text-muted-foreground">
            {[item.subtitle, when].filter(Boolean).join(" · ")}
          </p>
        )}
      </CardHeader>
      <CardContent className="space-y-2">
        {/* Block bodies are markdown-authored but render as plain text for now —
            upgrade to a markdown renderer once the public styling settles. */}
        {(item.body || item.description) && (
          <p className="text-sm whitespace-pre-wrap">
            {item.body || item.description}
          </p>
        )}
        {item.url ? (
          <a
            href={item.url}
            target="_blank"
            rel="noreferrer"
            className="text-sm underline"
          >
            {item.url}
          </a>
        ) : null}
        {item.domains.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {item.domains.map((d) => (
              <Badge key={d} variant="secondary">
                {d}
              </Badge>
            ))}
          </div>
        )}
        {item.links?.length ? (
          <LinkedItems items={item.links} anchors={jumpTargets} />
        ) : null}
      </CardContent>
    </Card>
  );
}
