import clsx from "clsx";
import { useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { AppWindow, BookOpen, Cpu, Download, FileText, LayoutDashboard, LogIn, Moon, Sun } from "lucide-react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { Select } from "./ui";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/courses", label: "Courses", icon: BookOpen },
  { to: "/queue", label: "Queue", icon: Download },
  { to: "/transcripts", label: "Transcripts", icon: FileText },
  { to: "/models", label: "Models & settings", icon: Cpu },
];

function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem("theme") || "dark";
    } catch {
      return "dark";
    }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))];
}

interface InstallPrompt extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: string }>;
}

/** PWA install: native prompt in Chrome/Edge; Safari has no prompt, so we explain the menu path. */
function useInstall() {
  const [prompt, setPrompt] = useState<InstallPrompt | null>(null);
  const [installed, setInstalled] = useState(
    () => window.matchMedia("(display-mode: standalone)").matches || (navigator as { standalone?: boolean }).standalone === true,
  );
  useEffect(() => {
    const onPrompt = (e: Event) => {
      e.preventDefault();
      setPrompt(e as InstallPrompt);
    };
    const onInstalled = () => setInstalled(true);
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);
  const isSafari = /^((?!chrome|android|crios|fxios|edg).)*safari/i.test(navigator.userAgent);
  return { prompt, installed, isSafari, clear: () => setPrompt(null) };
}

export function Layout() {
  const { session, jobs, semester, setSemester, connected, toasts } = useStore();
  const [semesters, setSemesters] = useState<{ key: string; label: string }[]>([]);
  const [theme, toggleTheme] = useTheme();
  const install = useInstall();
  const { toast } = useStore();
  const navigate = useNavigate();

  useEffect(() => {
    api
      .get<{ current: string; semesters: { key: string; label: string }[] }>("/api/semesters")
      .then((r) => {
        setSemesters(r.semesters.slice(0, 12));
        if (!semester) setSemester(r.current);
      })
      .catch(() => undefined);
  }, [semester, setSemester]);

  const active = useMemo(() => Object.values(jobs).filter((j) => j.status === "running" || j.status === "queued").length, [jobs]);

  const sessionChip =
    session?.state === "authenticated" ? (
      <span className="flex items-center gap-2 text-chalk-2">
        <span className="size-2 rounded-full bg-ok" aria-hidden />
        {session.display_name}
      </span>
    ) : session?.state === "expired" ? (
      <button onClick={() => navigate("/login")} className="flex items-center gap-2 text-warn hover:underline">
        <span className="size-2 rounded-full bg-warn" aria-hidden />
        Session expired, sign in again
      </button>
    ) : (
      <button onClick={() => navigate("/login")} className="flex items-center gap-2 text-chalk-2 hover:text-chalk">
        <LogIn className="size-4" aria-hidden />
        Sign in to TUM-Live
      </button>
    );

  return (
    <div className="flex h-full">
      <aside className="flex w-[210px] shrink-0 flex-col border-r border-line bg-board max-md:w-[60px]">
        <div className="flex h-14 items-center gap-2.5 px-4">
          <svg viewBox="0 0 32 32" className="size-7 shrink-0" aria-hidden>
            <rect width="32" height="32" rx="7" fill="var(--board-3)" />
            <path d="M8 22h16M8 16h11M8 10h16" stroke="var(--accent)" strokeWidth="2.6" strokeLinecap="round" />
          </svg>
          <div className="leading-tight max-md:hidden">
            <div className="font-semibold">Lecture Studio</div>
            <div className="text-[12px] text-muted">for TUM-Live</div>
          </div>
        </div>
        <nav className="mt-2 flex flex-1 flex-col gap-0.5 px-2" aria-label="Main">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              title={label}
              className={({ isActive }) =>
                clsx(
                  "flex h-9 items-center gap-2.5 rounded-md px-2.5 text-chalk-2 transition-colors hover:bg-board-3 hover:text-chalk",
                  isActive && "bg-board-3 font-medium text-chalk",
                )
              }
            >
              <Icon className="size-[17px] shrink-0" aria-hidden />
              <span className="flex-1 max-md:hidden">{label}</span>
              {to === "/queue" && active > 0 && (
                <span className="tnum rounded bg-accent px-1.5 text-[11px] font-semibold text-accent-ink max-md:hidden">{active}</span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="flex items-center justify-between border-t border-line px-4 py-3 text-[12px] text-muted max-md:justify-center max-md:px-0">
          <span className="flex items-center gap-1.5 max-md:hidden" title={connected ? "Live updates connected" : "Reconnecting to backend"}>
            <span className={clsx("size-1.5 rounded-full", connected ? "bg-ok" : "bg-warn")} aria-hidden />
            {connected ? "Live" : "Reconnecting"}
          </span>
          {!install.installed && (install.prompt || install.isSafari) && (
            <button
              onClick={async () => {
                if (install.prompt) {
                  await install.prompt.prompt();
                  install.clear();
                } else {
                  toast("In Safari: File → Add to Dock (macOS) or Share → Add to Home Screen (iOS).");
                }
              }}
              className="flex items-center gap-1.5 rounded p-1 hover:bg-board-3 hover:text-chalk"
              title="Install as an app"
            >
              <AppWindow className="size-4" aria-hidden />
              <span className="max-md:hidden">Install app</span>
            </button>
          )}
          <button onClick={toggleTheme} className="rounded p-1 hover:bg-board-3 hover:text-chalk" aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-line px-6 max-md:px-4">
          <label className="flex items-center gap-2 text-muted">
            <span className="max-sm:hidden">Semester</span>
            <Select value={semester ?? ""} onChange={(e) => setSemester(e.target.value)} className="h-8" aria-label="Semester">
              {semesters.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.label}
                </option>
              ))}
            </Select>
          </label>
          <div className="text-sm">{session ? sessionChip : null}</div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto px-6 py-6 max-md:px-4">
          <div className="mx-auto max-w-[1280px]">
            <Outlet />
          </div>
        </main>
      </div>
      <div className="pointer-events-none fixed right-4 bottom-4 z-50 flex flex-col gap-2" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={clsx(
              "pointer-events-auto max-w-sm rounded-lg border bg-board-2 px-4 py-2.5 shadow-lg",
              t.tone === "ok" ? "border-ok/40" : t.tone === "bad" ? "border-bad/50" : "border-line-strong",
            )}
          >
            {t.text}
          </div>
        ))}
      </div>
    </div>
  );
}
