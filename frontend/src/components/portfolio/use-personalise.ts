import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  personaliseView,
  type PersonaliseView,
} from "@/lib/portfolio/personalise";
import {
  needsPersonalise,
  type ExploreSearch,
} from "@/lib/portfolio/questionnaire";
import {
  PERSONALISE_GIVE_UP_MS,
  personaliseJobQuery,
  personaliseStartQuery,
} from "@/lib/queries/portfolio";

/** True from `startedAt + ms` on. A timer, not a poll: the page must flip even when nothing else
 *  changes. */
function useExpired(startedAt: number | undefined, ms: number): boolean {
  const [expiredFor, setExpiredFor] = useState<number | undefined>();
  useEffect(() => {
    if (startedAt === undefined) return;
    const left = Math.max(0, startedAt + ms - Date.now());
    const timer = setTimeout(() => setExpiredFor(startedAt), left);
    return () => clearTimeout(timer);
  }, [startedAt, ms]);
  return startedAt !== undefined && expiredFor === startedAt;
}

/** A questionnaire visit's AI job, from queueing to the result: see personaliseView. */
export function usePersonalise(
  handle: string,
  search: ExploreSearch,
): PersonaliseView {
  const start = useQuery(personaliseStartQuery(handle, search));
  const expired = useExpired(start.data?.startedAt, PERSONALISE_GIVE_UP_MS);
  const job = useQuery({
    ...personaliseJobQuery(start.data?.id),
    enabled: Boolean(start.data?.id) && !expired,
  });

  // Sticky per job: the wake step stays ticked off after the server is up.
  const [wokeFor, setWokeFor] = useState<string | undefined>();
  const id = start.data?.id;
  if (job.data?.waking && id !== undefined && wokeFor !== id) setWokeFor(id);

  return personaliseView({
    needed: needsPersonalise(search),
    hasQuery: Boolean(search.q?.trim()),
    startFailed: start.isError,
    job: job.data,
    woke: id !== undefined && wokeFor === id,
    expired,
  });
}
