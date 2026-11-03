import clsx from "clsx";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { BookOpen, Download, LayoutGrid, List, RefreshCw, Search, Star } from "lucide-react";
import { api, ApiError, type CourseSummary, type Recording } from "../lib/api";
import { useStore } from "../lib/store";
import { Badge, Button, Empty, ErrorBox, Input, PageHeader, Select, Skeleton } from "../components/ui";
import { VARIANT_LABEL } from "../lib/format";

type Filter = "all" | "downloadable" | "favorites";

export default function Courses() {
  const { semester, session, system, toast, libraryVersion } = useStore();
  const [courses, setCourses] = useState<CourseSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [view, setView] = useState<"grid" | "list">(() => {
    try {
      return (localStorage.getItem("courseView") as "grid" | "list") || "grid";
    } catch {
      return "grid";
    }
  });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [variant, setVariant] = useState("COMB");
  const [queueing, setQueueing] = useState(false);
  const [remoteQuery, setRemoteQuery] = useState<string | null>(null);

  const load = useCallback(
    async (refresh = false, query: string | null = remoteQuery) => {
      if (!semester) return;
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({ semester });
        if (refresh) params.set("refresh", "true");
        if (query) params.set("q", query);
        const r = await api.get<{ courses: CourseSummary[] }>(`/api/courses?${params}`);
        setCourses(r.courses);
      } catch (e) {
        const err = e as ApiError;
        setError(err.code === "auth_required" ? "Your TUM-Live session expired. Sign in again to see your courses." : err.message);
      } finally {
        setLoading(false);
      }
    },
    [semester, remoteQuery],
  );

  useEffect(() => {
    setCourses(null);
    setSelected(new Set());
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [semester, session?.state]);
  useEffect(() => {
    if (courses) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [libraryVersion]);
  useEffect(() => {
    if (system?.settings.download_variant) setVariant(system.settings.download_variant);
  }, [system?.settings.download_variant]);
  useEffect(() => {
    try {
      localStorage.setItem("courseView", view);
    } catch {
      /* ignore */
    }
  }, [view]);

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (courses ?? []).filter((c) => {
      if (filter === "downloadable" && !c.downloadable_count) return false;
      if (filter === "favorites" && !c.favorite) return false;
      if (!needle) return true;
      return [c.name, c.slug, c.tum_online_identifier].join(" ").toLowerCase().includes(needle);
    });
  }, [courses, q, filter]);
  const priority = visible.filter((c) => c.priority || c.favorite);
  const others = visible.filter((c) => !c.priority && !c.favorite);

  const toggleFav = async (c: CourseSummary) => {
    await api.post("/api/favorites", { key: c.key, on: !c.favorite });
    setCourses((cs) => cs?.map((x) => (x.key === c.key ? { ...x, favorite: !c.favorite } : x)) ?? null);
  };
  const toggleSel = (key: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(key)) n.delete(key);
      else n.add(key);
      return n;
    });

  const queueSelected = async () => {
    setQueueing(true);
    let queued = 0;
    let skipped = 0;
    try {
      for (const key of selected) {
        const c = courses?.find((x) => x.key === key);
        if (!c) continue;
        const sem = `${c.year}${c.term}`;
        const d = await api.get<{ recordings: Recording[] }>(`/api/courses/${sem}/${c.slug}`);
        const items = d.recordings
          .filter((r) => r.is_vod && r.downloadable_variants.includes(variant))
          .map((r) => ({ semester: sem, slug: c.slug, stream_id: r.id, variant }));
        skipped += d.recordings.filter((r) => r.is_vod && !r.downloadable_variants.includes(variant)).length;
        if (items.length) {
          const res = await api.post<{ results: { job?: unknown; duplicate?: string }[] }>("/api/downloads", { items });
          queued += res.results.filter((x) => x.job && !x.duplicate).length;
        }
      }
      toast(`${queued} lecture${queued === 1 ? "" : "s"} added to the queue${skipped ? `, ${skipped} without download permission skipped` : ""}.`, "ok");
      setSelected(new Set());
    } catch (e) {
      toast((e as Error).message, "bad");
    } finally {
      setQueueing(false);
    }
  };

  const signedIn = session?.state === "authenticated";
  return (
    <div>
      <PageHeader
        title="Courses"
        sub={
          courses
            ? `${courses.length} course${courses.length === 1 ? "" : "s"} visible${signedIn ? " to your account" : " without signing in (public only)"}`
            : "Loading the course catalog from TUM-Live"
        }
        actions={
          <Button variant="ghost" icon={<RefreshCw className={clsx("size-4", loading && "animate-spin")} />} onClick={() => load(true)} disabled={loading}>
            Refresh
          </Button>
        }
      />

      <div className="mb-5 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[240px] flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" aria-hidden />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by name or module ID" className="w-full pl-9" aria-label="Filter courses" />
        </div>
        <Select value={filter} onChange={(e) => setFilter(e.target.value as Filter)} aria-label="Show">
          <option value="all">All courses</option>
          <option value="downloadable">With downloads</option>
          <option value="favorites">Favorites</option>
        </Select>
        <div className="flex rounded-md border border-line-strong" role="group" aria-label="Layout">
          <button onClick={() => setView("grid")} aria-pressed={view === "grid"} className={clsx("grid size-9 place-items-center rounded-l-md", view === "grid" ? "bg-board-3" : "text-muted")} aria-label="Grid">
            <LayoutGrid className="size-4" />
          </button>
          <button onClick={() => setView("list")} aria-pressed={view === "list"} className={clsx("grid size-9 place-items-center rounded-r-md", view === "list" ? "bg-board-3" : "text-muted")} aria-label="List">
            <List className="size-4" />
          </button>
        </div>
      </div>

      {selected.size > 0 && (
        <div className="sticky top-0 z-10 mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-accent/40 bg-board-2 px-4 py-2.5">
          <span className="font-medium">{selected.size} selected</span>
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set(visible.map((c) => c.key)))}>
            Select all
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
            Deselect all
          </Button>
          <div className="flex-1" />
          <Select value={variant} onChange={(e) => setVariant(e.target.value)} className="h-8" aria-label="Video variant">
            {["COMB", "PRES", "CAM"].map((v) => (
              <option key={v} value={v}>
                {VARIANT_LABEL[v]}
              </option>
            ))}
          </Select>
          <Button size="sm" variant="primary" busy={queueing} icon={<Download className="size-4" />} onClick={queueSelected}>
            Download all lectures
          </Button>
        </div>
      )}

      {error && (
        <div className="mb-5">
          <ErrorBox
            error={error}
            action={
              session?.state !== "authenticated" ? (
                <Link to="/login" className="text-accent hover:underline">
                  Sign in
                </Link>
              ) : (
                <button onClick={() => load(true)} className="text-accent hover:underline">
                  Try again
                </button>
              )
            }
          />
        </div>
      )}

      {!courses && !error && (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(270px,1fr))] gap-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-[132px]" />
          ))}
        </div>
      )}

      {courses && visible.length === 0 && (
        <Empty icon={<BookOpen className="size-6" />} title={q ? `No course matches "${q}"` : "No courses in this semester"}>
          {q && signedIn ? (
            <Button
              className="mt-3"
              onClick={() => {
                setRemoteQuery(q);
                load(true, q);
              }}
            >
              Search all of TUM-Live for "{q}"
            </Button>
          ) : !signedIn ? (
            <span>
              Only public courses are listed without an account. <Link to="/login" className="text-accent hover:underline">Sign in</Link> to see yours.
            </span>
          ) : (
            "Try another semester."
          )}
        </Empty>
      )}

      {priority.length > 0 && (
        <Section title="Priority and favorites" count={priority.length}>
          <CourseCollection items={priority} view={view} selected={selected} onSelect={toggleSel} onFav={toggleFav} />
        </Section>
      )}
      {others.length > 0 && (
        <Section title={priority.length ? "All other courses" : "All courses"} count={others.length}>
          <CourseCollection items={others} view={view} selected={selected} onSelect={toggleSel} onFav={toggleFav} />
        </Section>
      )}
      {courses && system && signedIn && (
        <MissingPriority courses={courses} priorities={system.priority_courses} />
      )}
    </div>
  );
}

function MissingPriority({ courses, priorities }: { courses: CourseSummary[]; priorities: { id: string; label: string }[] }) {
  const missing = priorities.filter((p) => !courses.some((c) => c.priority === p.id));
  if (!missing.length) return null;
  return (
    <p className="mt-6 text-[13px] text-muted">
      Not found on TUM-Live for this semester: {missing.map((m) => m.label).join(", ")}. They may be listed under a different name, a
      different semester, or not be recorded.
    </p>
  );
}

function Section({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  return (
    <section className="mb-7">
      <h2 className="mb-3 flex items-baseline gap-2 text-[15px] font-semibold">
        {title} <span className="tnum text-[13px] font-normal text-muted">{count}</span>
      </h2>
      {children}
    </section>
  );
}

interface CollectionProps {
  items: CourseSummary[];
  view: "grid" | "list";
  selected: Set<string>;
  onSelect: (key: string) => void;
  onFav: (c: CourseSummary) => void;
}

function CourseCollection({ items, view, selected, onSelect, onFav }: CollectionProps) {
  if (view === "list")
    return (
      <div className="overflow-hidden rounded-lg border border-line">
        <table className="w-full text-left">
          <thead className="bg-board-2 text-[12px] text-muted">
            <tr>
              <th className="w-10 px-3 py-2" />
              <th className="px-3 py-2 font-medium">Course</th>
              <th className="px-3 py-2 font-medium max-md:hidden">Module</th>
              <th className="px-3 py-2 text-right font-medium">Recordings</th>
              <th className="px-3 py-2 font-medium max-sm:hidden">Downloads</th>
              <th className="w-10" />
            </tr>
          </thead>
          <tbody>
            {items.map((c) => (
              <tr key={c.key} className="border-t border-line hover:bg-board-2">
                <td className="px-3 py-2">
                  <input type="checkbox" checked={selected.has(c.key)} onChange={() => onSelect(c.key)} aria-label={`Select ${c.name}`} />
                </td>
                <td className="px-3 py-2">
                  <Link to={`/courses/${c.year}${c.term}/${c.slug}`} className="font-medium hover:text-accent">
                    {c.name}
                  </Link>
                  {c.priority && <span className="ml-2"><Badge tone="accent">{c.priority}</Badge></span>}
                </td>
                <td className="px-3 py-2 text-muted max-md:hidden">{c.tum_online_identifier || c.slug}</td>
                <td className="tnum px-3 py-2 text-right">{c.recording_count ?? "–"}</td>
                <td className="px-3 py-2 max-sm:hidden">
                  <AccessBadge c={c} />
                </td>
                <td className="px-2">
                  <FavButton c={c} onFav={onFav} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(270px,1fr))] gap-3">
      {items.map((c) => (
        <article
          key={c.key}
          className={clsx(
            "group relative flex flex-col rounded-lg border bg-board-2 p-4 transition-colors",
            selected.has(c.key) ? "border-accent/70" : "border-line hover:border-line-strong",
          )}
        >
          <div className="flex items-start gap-3">
            <input type="checkbox" className="mt-1" checked={selected.has(c.key)} onChange={() => onSelect(c.key)} aria-label={`Select ${c.name}`} />
            <div className="min-w-0 flex-1">
              <Link to={`/courses/${c.year}${c.term}/${c.slug}`} className="line-clamp-2 font-semibold leading-snug after:absolute after:inset-0 hover:text-accent">
                {c.name}
              </Link>
              <div className="mt-1 truncate text-[13px] text-muted">
                {c.tum_online_identifier || c.slug} · {c.semester_label}
              </div>
            </div>
            <div className="relative z-[1]">
              <FavButton c={c} onFav={onFav} />
            </div>
          </div>
          <div className="mt-auto flex flex-wrap items-center gap-1.5 pt-4">
            {c.priority && <Badge tone="accent">{c.priority}</Badge>}
            <span className="tnum text-[13px] text-chalk-2">
              {c.recording_count == null ? "–" : `${c.recording_count} recording${c.recording_count === 1 ? "" : "s"}`}
            </span>
            <span className="flex-1" />
            <AccessBadge c={c} />
            {!!c.downloaded_count && <Badge tone="ok">{c.downloaded_count} local</Badge>}
          </div>
        </article>
      ))}
    </div>
  );
}

function AccessBadge({ c }: { c: CourseSummary }) {
  if (!c.vod_enabled) return <Badge title="The lecturer has not enabled recordings">No recordings</Badge>;
  if (c.downloadable_count > 0) return <Badge tone="info">Downloadable</Badge>;
  return <Badge title="The lecturer has disabled downloads for this course">Stream only</Badge>;
}

function FavButton({ c, onFav }: { c: CourseSummary; onFav: (c: CourseSummary) => void }) {
  return (
    <button
      onClick={() => onFav(c)}
      className="rounded p-1 text-muted hover:bg-board-3 hover:text-accent"
      aria-label={c.favorite ? `Remove ${c.name} from favorites` : `Add ${c.name} to favorites`}
      aria-pressed={c.favorite}
    >
      <Star className={clsx("size-4", c.favorite && "fill-accent text-accent")} />
    </button>
  );
}
