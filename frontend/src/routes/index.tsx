import { useEffect, useMemo } from "react";
import {
  createFileRoute,
  redirect,
  useLocation,
  useNavigate,
} from "@tanstack/react-router";
import { z } from "zod";
import { Questionnaire } from "@/components/portfolio/questionnaire";
import { ExploreResult } from "@/components/portfolio/explore-result";
import { ApiError } from "@/lib/api";
import {
  hasAnswer,
  splitQuery,
  type ExploreSearch,
} from "@/lib/portfolio/questionnaire";
import { appOrigin, siteHost } from "@/lib/host";
import { nativeStamp, readStamp, writeStamp } from "@/lib/portfolio/stamp";
import { useNativeMeta } from "@/lib/queries/portfolio";

declare module "@tanstack/history" {
  interface HistoryState {
    visitorQuery?: string; // splitQuery: the free text, kept out of the URL
  }
}

const exploreSearch = z.object({
  d: z.array(z.string()).optional(),
  lucky: z.boolean().optional(),
  focus: z.string().optional(),
  tone: z.string().optional(),
});

export const Route = createFileRoute("/")({
  validateSearch: exploreSearch,
  head: () => ({ meta: [{ name: "robots", content: "noindex" }] }),
  beforeLoad: () => {
    const host = siteHost();
    if (host.kind === "app") throw redirect({ to: "/applications" });
    if (host.kind === "apex") {
      // Dev only — prod apex is Django-rendered and never loads the SPA.
      window.location.replace(appOrigin());
      throw redirect({ to: "/" }); // unreachable; satisfies the type
    }
    // handle host → render the questionnaire/result below
  },
  component: HandleHome,
});

function HandleHome() {
  const search = Route.useSearch();
  const q = useLocation({ select: (l) => l.state.visitorQuery });
  const navigate = useNavigate();

  // Return-visitor dispatch - Origin-scoped
  // only when there's no answer in the URL yet; read once per URL, like the effect it feeds

  const stamp = useMemo(
    () => (hasAnswer(search) ? null : readStamp()),
    [search],
  );
  const toLink = stamp?.kind === "link" ? stamp : null; //jumps to that slug
  const toResult =
    stamp?.kind === "native" && hasAnswer(stamp.search) ? stamp : null; //restores the result

  useEffect(() => {
    if (toLink) {
      navigate({ to: "/$slug", params: { slug: toLink.slug }, replace: true });
    } else if (toResult) {
      navigate({ to: "/", search: toResult.search, replace: true });
    }
  }, [toLink, toResult, navigate]);

  if (toLink || toResult) return null;
  if (hasAnswer(search)) return <ExploreResult search={{ ...search, q }} />;

  return (
    <AskOrMissing
      onDone={(s) => {
        writeStamp(nativeStamp(s)); // written ONCE, at answer time
        const { url, q: visitorQuery } = splitQuery(s);
        navigate({ to: "/", search: url, state: { visitorQuery } });
      }}
    />
  );
}

/** A 404 on the questionnaire's own meta query means there is nothing to ask about. The API
 *  answers an unpublished handle and an unknown one alike, so this page can't tell them apart
 *  either. Same query key as the Questionnaire: the cache makes it one request. */
function AskOrMissing({ onDone }: { onDone: (s: ExploreSearch) => void }) {
  const meta = useNativeMeta();
  if (meta.error instanceof ApiError && meta.error.status === 404) {
    return (
      <main className="min-h-screen grid place-items-center">
        <p className="text-muted-foreground">
          There's no portfolio at this address.
        </p>
      </main>
    );
  }
  return <Questionnaire onDone={onDone} />;
}
