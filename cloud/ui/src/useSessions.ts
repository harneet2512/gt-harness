import { useCallback, useEffect, useState } from "react";
import { listSessions, type Session } from "./api";

const REFRESH_MS = 5000;

export interface SessionList {
  sessions: readonly Session[];
  error: string | null;
  reload: () => void;
}

/**
 * Every session this user has, newest first, kept warm. One poller for the
 * page: the resume rail, the repo chip and the landing's default repository
 * are three readings of the same list.
 */
export function useSessions(): SessionList {
  const [sessions, setSessions] = useState<readonly Session[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSessions(await listSessions());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
    const poll = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(poll);
  }, [load]);

  return { sessions, error, reload: () => void load() };
}
