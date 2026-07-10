import { useEffect, useReducer, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  runReducer,
  useCancelGeneration,
  useGeneration,
  type RunState,
  type WsEvent,
} from "@/lib/queries/generations";
import { openGenerationSocket, type SocketStatus } from "@/lib/ws";

const INITIAL: RunState = {
  status: "pending",
  stage: "",
  result: null,
  error: "",
};

/** Everything a selected run needs while in flight: reducer state seeded from
 *  the REST snapshot, the live socket, a 1s clock for the elapsed/stale-queue
 *  display, and abort. */
export function useRunLifecycle(runId: number | null) {
  const qc = useQueryClient();
  const [state, dispatch] = useReducer(runReducer, INITIAL);
  // Starts as "connecting" so the closed-socket notice never flashes before the
  // socket effect has run.
  const [socket, setSocket] = useState<SocketStatus>({ kind: "connecting" });
  const snapshot = useGeneration(runId); // REST rehydrate (refresh-safe)
  const cancel = useCancelGeneration();

  // A 1s clock while a run is in flight, for the elapsed/stale-queue display.
  const active =
    runId != null && (state.status === "pending" || state.status === "running");
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, [active]);

  // Seed from the REST snapshot whenever it (re)loads.
  useEffect(() => {
    if (snapshot.data) {
      dispatch({
        event: "snapshot",
        status: snapshot.data.status,
        stage: snapshot.data.stage,
        result: snapshot.data.result,
        error: snapshot.data.error,
      });
    }
  }, [snapshot.data]);

  // Live socket while a run is selected; on a terminal event refresh the
  // application — the fill-if-empty hand-off may have landed content.
  useEffect(() => {
    if (runId == null) return;
    return openGenerationSocket(
      runId,
      (d) => {
        const e = d as WsEvent;
        dispatch(e);
        if (e.event === "done" || e.event === "failed") {
          qc.invalidateQueries({ queryKey: ["jac", "applications"] });
        }
      },
      setSocket,
    );
  }, [runId, qc]);

  function abort() {
    if (runId == null) return;
    cancel.mutate(runId, {
      onSuccess: (run) => {
        dispatch({
          event: "snapshot",
          status: run.status,
          stage: run.stage,
          result: run.result,
          error: run.error,
        });
        qc.invalidateQueries({ queryKey: ["jac", "applications"] });
        qc.invalidateQueries({ queryKey: ["jac", "generations", runId] });
      },
      onError: () => toast.error("Could not cancel the run"),
    });
  }

  return {
    state,
    socket,
    now,
    runCreatedAt: snapshot.data?.created_at ?? null,
    abort,
    aborting: cancel.isPending,
  };
}
