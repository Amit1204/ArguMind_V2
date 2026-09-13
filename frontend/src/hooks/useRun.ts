import { useEffect, useState } from "react";
import { ApiError, getRun, isRunning, type RunDetail } from "../api/client";

// Loads a run and polls it every `intervalMs` while it is queued or running.
export function useRun(runId: string | null, intervalMs = 2000) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) {
      setRun(null);
      setError(null);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const load = async () => {
      try {
        const detail = await getRun(runId);
        if (cancelled) return;
        setRun(detail);
        setError(null);
        if (isRunning(detail.status)) timer = setTimeout(load, intervalMs);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof ApiError ? `${e.message} (HTTP ${e.status})` : "backend unreachable");
        timer = setTimeout(load, intervalMs * 2); // keep trying: the run may still be executing
      }
    };
    void load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [runId, intervalMs]);

  return { run, error };
}
