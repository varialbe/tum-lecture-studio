import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api, type Job, type Session, type SystemInfo } from "./api";

interface Toast {
  id: number;
  text: string;
  tone: "ok" | "bad" | "info";
}

interface Store {
  session: Session | null;
  refreshSession: (force?: boolean) => Promise<void>;
  setSession: (s: Session) => void;
  jobs: Record<string, Job>;
  refreshJobs: () => Promise<void>;
  system: SystemInfo | null;
  refreshSystem: () => Promise<void>;
  semester: string | null;
  setSemester: (s: string) => void;
  connected: boolean;
  /** increments whenever library/transcripts change server-side */
  libraryVersion: number;
  toasts: Toast[];
  toast: (text: string, tone?: Toast["tone"]) => void;
}

const Ctx = createContext<Store | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [semester, setSemesterState] = useState<string | null>(() => {
    try {
      return localStorage.getItem("semester");
    } catch {
      return null;
    }
  });
  const [connected, setConnected] = useState(false);
  const [libraryVersion, setLibraryVersion] = useState(0);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastId = useRef(0);

  const toast = useCallback((text: string, tone: Toast["tone"] = "info") => {
    const id = ++toastId.current;
    setToasts((t) => [...t, { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5000);
  }, []);

  const refreshSession = useCallback(async (force = false) => {
    try {
      setSession(await api.get<Session>(`/api/session${force ? "?force=true" : ""}`));
    } catch {
      setSession({ state: "unknown", display_name: null, message: "Backend unreachable" });
    }
  }, []);
  const refreshJobs = useCallback(async () => {
    const r = await api.get<{ jobs: Job[] }>("/api/jobs");
    setJobs(Object.fromEntries(r.jobs.map((j) => [j.id, j])));
  }, []);
  const refreshSystem = useCallback(async () => {
    setSystem(await api.get<SystemInfo>("/api/system"));
  }, []);
  const setSemester = useCallback((s: string) => {
    setSemesterState(s);
    try {
      localStorage.setItem("semester", s);
    } catch {
      /* private mode */
    }
  }, []);

  useEffect(() => {
    refreshSession();
    refreshJobs().catch(() => undefined);
    refreshSystem().catch(() => undefined);
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout>;
    const connect = () => {
      es = new EventSource("/api/events");
      es.addEventListener("hello", () => {
        setConnected(true);
        refreshJobs().catch(() => undefined); // catch up on anything missed while disconnected
      });
      es.addEventListener("job", (e) => {
        const job = JSON.parse((e as MessageEvent).data) as Job;
        setJobs((prev) => ({ ...prev, [job.id]: job }));
        if (job.status === "completed" || job.status === "failed") setLibraryVersion((v) => v + 1);
      });
      es.addEventListener("job_removed", (e) => {
        const { id } = JSON.parse((e as MessageEvent).data);
        setJobs((prev) => {
          const n = { ...prev };
          delete n[id];
          return n;
        });
      });
      es.addEventListener("jobs_cleared", () => refreshJobs().catch(() => undefined));
      for (const ev of ["library", "transcript", "models"])
        es.addEventListener(ev, () => setLibraryVersion((v) => v + 1));
      es.onerror = () => {
        setConnected(false);
        es?.close();
        retry = setTimeout(connect, 2000);
      };
    };
    connect();
    return () => {
      es?.close();
      clearTimeout(retry);
    };
  }, [refreshJobs, refreshSession, refreshSystem]);

  const value = useMemo(
    () => ({
      session,
      refreshSession,
      setSession,
      jobs,
      refreshJobs,
      system,
      refreshSystem,
      semester,
      setSemester,
      connected,
      libraryVersion,
      toasts,
      toast,
    }),
    [session, refreshSession, jobs, refreshJobs, system, refreshSystem, semester, setSemester, connected,
      libraryVersion, toasts, toast],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore outside provider");
  return s;
}
