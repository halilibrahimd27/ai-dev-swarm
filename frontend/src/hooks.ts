import { useCallback, useEffect, useRef, useState } from "react";
import { api, type TranscriptEntry } from "./api";

export type Theme = "dark" | "light";

const THEME_KEY = "aidevswarm.theme";

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem(THEME_KEY);
    return saved === "light" || saved === "dark" ? saved : "dark";
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);
  const toggle = useCallback(() => setTheme((t) => (t === "dark" ? "light" : "dark")), []);
  return [theme, toggle];
}

interface PollState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  refresh: () => void;
}

/** Poll an async getter on an interval; refresh on demand. Pauses when the tab
 *  is hidden so a backgrounded dashboard stops hammering the API. */
export function usePoll<T>(fn: () => Promise<T>, intervalMs = 5000, deps: unknown[] = []): PollState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const refresh = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    const run = async () => {
      try {
        const value = await fnRef.current();
        if (alive) {
          setData(value);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setLoading(false);
      }
    };
    run();
    const id = window.setInterval(() => {
      if (!document.hidden) run();
    }, intervalMs);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, tick, ...deps]);

  return { data, error, loading, refresh };
}

export type SSEState = "connecting" | "open" | "closed";

/** Replay a project's transcript, then live-stream new entries over SSE.
 *  De-dupes by id and caps memory so a long session can't grow unbounded. */
export function useTranscript(projectId: string | null, cap = 1200) {
  const [entries, setEntries] = useState<TranscriptEntry[]>([]);
  const [status, setStatus] = useState<SSEState>("closed");
  const seen = useRef<Set<string>>(new Set());

  useEffect(() => {
    seen.current = new Set();
    setEntries([]);
    if (!projectId) {
      setStatus("closed");
      return;
    }
    let alive = true;
    setStatus("connecting");

    const push = (list: TranscriptEntry[]) => {
      const fresh = list.filter((e) => e.id && !seen.current.has(e.id));
      fresh.forEach((e) => seen.current.add(e.id));
      if (fresh.length) {
        setEntries((prev) => {
          const next = prev.concat(fresh);
          return next.length > cap ? next.slice(next.length - cap) : next;
        });
      }
    };

    // Replay history first, then attach the live stream.
    api
      .transcript(projectId)
      .then((hist) => {
        if (alive) push(hist);
      })
      .catch(() => {});

    const es = new EventSource(`/sse/transcript/${projectId}`);
    es.onopen = () => alive && setStatus("open");
    es.onmessage = (ev) => {
      try {
        push([JSON.parse(ev.data) as TranscriptEntry]);
      } catch {
        /* ignore malformed frame */
      }
    };
    es.onerror = () => alive && setStatus("connecting");

    return () => {
      alive = false;
      es.close();
      setStatus("closed");
    };
  }, [projectId, cap]);

  return { entries, status };
}
