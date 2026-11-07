import { useEffect, useMemo } from "react";
import {
  createFileRoute,
  useLocation,
  useNavigate,
} from "@tanstack/react-router";
import { Questionnaire } from "@/components/portfolio/questionnaire";
import { ExploreResult } from "@/components/portfolio/explore-result";
import { useOwnerHome } from "@/components/portfolio/use-owner-home";
import { ApiError } from "@/lib/api";
import {
  exploreSearchSchema,
  hasAnswer,
  splitQuery,
  type ExploreSearch,
} from "@/lib/portfolio/questionnaire";
import { nativeStamp, readStamp, writeStamp } from "@/lib/portfolio/stamp";
import { useNativeMeta } from "@/lib/queries/portfolio";

declare module "@tanstack/history" {
  interface HistoryState {
    visitorQuery?: string; // splitQuery: the free text, kept out of the URL
  }
}

/** One owner's questionnaire home, portfolio.<base>/<handle>/: the questionnaire, or the result
 *  once answered. */
export const Route = createFileRoute("/$handle/")({
  validateSearch: exploreSearchSchema,
  component: HandleHome,
});

function HandleHome() {
  const handle = Route.useParams({ select: (p) => p.handle.toLowerCase() });
  const search = Route.useSearch();
  const q = useLocation({ select: (l) => l.state.visitorQuery });
  const navigate = useNavigate();
  const goHome = useOwnerHome(handle);

  // Return-visitor dispatch, from this owner's stamp. Only when there's no answer in the URL yet;
  // read once per URL, like the effect it feeds.
  const stamp = useMemo(
    () => (hasAnswer(search) ? null : readStamp(handle)),
    [search, handle],
  );
  const toLink = stamp?.kind === "link" ? stamp : null; // jumps to that slug
  const toResult =
    stamp?.kind === "native" && hasAnswer(stamp.search) ? stamp : null; // restores the result

  useEffect(() => {
    if (toLink) {
      navigate({
        to: "/$handle/$slug/",
        params: { handle, slug: toLink.slug },
        replace: true,
      });
    } else if (toResult) {
      goHome({ search: toResult.search, replace: true });
    }
  }, [toLink, toResult, navigate, goHome, handle]);

  if (toLink || toResult) return null;
  if (hasAnswer(search))
    return <ExploreResult handle={handle} search={{ ...search, q }} />;

  return (
    <AskOrMissing
      handle={handle}
      onDone={(s) => {
        writeStamp(handle, nativeStamp(s)); // written ONCE, at answer time
        const { url, q: visitorQuery } = splitQuery(s);
        goHome({ search: url, state: { visitorQuery } });
      }}
    />
  );
}

/** A 404 on the questionnaire's own meta query means there is nothing to ask about. The API
 *  answers an unpublished handle and an unknown one alike, so this page can't tell them apart
 *  either. Same query key as the Questionnaire: the cache makes it one request. */
function AskOrMissing({
  handle,
  onDone,
}: {
  handle: string;
  onDone: (s: ExploreSearch) => void;
}) {
  const meta = useNativeMeta(handle);
  if (meta.error instanceof ApiError && meta.error.status === 404) {
    return (
      <main className="min-h-screen grid place-items-center">
        <p className="text-muted-foreground">
          There's no portfolio at this address.
        </p>
      </main>
    );
  }
  return <Questionnaire handle={handle} onDone={onDone} />;
}
