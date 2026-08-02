import { useEffect, useState } from "react";
import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";
import { z } from "zod";
import { Questionnaire } from "@/components/portfolio/questionnaire";
import { ExploreResult } from "@/components/portfolio/explore-result";
import { hasAnswer } from "@/lib/portfolio/questionnaire";
import { appOrigin, siteHost } from "@/lib/host";
import { nativeStamp, readStamp, writeStamp } from "@/lib/portfolio/stamp";

const exploreSearch = z.object({
  d: z.array(z.string()).optional(),
  lucky: z.boolean().optional(),
  q: z.string().optional(),
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
  const navigate = useNavigate();
  const [checked, setChecked] = useState(false);

  // Return-visitor dispatch - Origin-scoped

  useEffect(() => {
    // only when there's no answer in the URL yet
    if (hasAnswer(search)) {
      setChecked(true);
      return;
    }
    const stamp = readStamp();
    if (
      stamp?.kind === "link" //jumps to that slug
    ) {
      navigate({ to: "/$slug", params: { slug: stamp.slug }, replace: true });
    } else if (
      stamp?.kind === "native" &&
      hasAnswer(stamp.search) //restores the result
    ) {
      navigate({ to: "/", search: stamp.search, replace: true });
    } else {
      setChecked(true);
    }
  }, [search, navigate]);

  if (!checked) return null;
  if (hasAnswer(search)) return <ExploreResult search={search} />;

  return (
    <Questionnaire
      onDone={(s) => {
        writeStamp(nativeStamp(s)); // written ONCE, at answer time
        navigate({ to: "/", search: s });
      }}
    />
  );
}
