import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { PortfolioItem } from "@/lib/queries/portfolio";

function dates(item: PortfolioItem): string {
  if (!item.started && !item.ended) return "";
  const from = item.started?.slice(0, 4) ?? "";
  const to = item.ended ? item.ended.slice(0, 4) : "today";
  return from ? `${from} – ${to}` : "";
}

export function ItemCard({ item }: { item: PortfolioItem }) {
  if (item.type === "block" && item.kind === "image") {
    return (
      <Card className="overflow-hidden">
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
          </CardContent>
        ) : null}
      </Card>
    );
  }

  const when = dates(item);
  return (
    <Card>
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
      </CardContent>
    </Card>
  );
}
