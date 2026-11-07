import { useCallback } from "react";
import { useNavigate } from "@tanstack/react-router";
import type { ExploreSearch } from "@/lib/portfolio/questionnaire";

type HomeNav = {
  search?: Omit<ExploreSearch, "q">; // q never goes into the URL (splitQuery)
  state?: { visitorQuery?: string };
  replace?: boolean;
};

/** Back to an owner's questionnaire home, /<handle>/. The questionnaire's answer, "Start over",
 *  "Back to start" and a dead link all go there, so a visitor stays with the owner they came for. */
export function useOwnerHome(handle: string) {
  const navigate = useNavigate();
  return useCallback(
    ({ search = {}, state, replace }: HomeNav = {}) =>
      navigate({
        to: "/$handle/",
        params: { handle },
        search,
        state,
        replace,
      }),
    [navigate, handle],
  );
}
