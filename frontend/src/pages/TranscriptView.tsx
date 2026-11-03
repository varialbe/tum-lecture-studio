import clsx from "clsx";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, Captions, Check, Copy, Download, FileText, MonitorPlay, Pencil, Search, Trash2, X } from "lucide-react";
import { api, type Transcript } from "../lib/api";
import { useStore } from "../lib/store";
import { Badge, Button, ErrorBox, Input, Skeleton } from "../components/ui";
import { clock, duration } from "../lib/format";

const FORMATS = ["txt", "md", "srt", "vtt", "json"] as const;
type Mode = "video" | "script";
type Seg = Transcript["segments"][number];

function load<T extends string>(key: string, fallback: T): T {
  try {
    return (localStorage.getItem(key) as T) || fallback;
  } catch {
    return fallback;
  }
}
function save(key: string, v: string) {
  try {
    localStorage.setItem(key, v);
  } catch {
    /* private mode */
  }
}

/** Index of the last segment that has started at time t (binary search). */
function segmentAt(segs: Seg[], t: number): number {
  let lo = 0;
  let hi = segs.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (segs[mid].start <= t + 0.05) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found;
}

export default function TranscriptView() {
  const { id = "" } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { toast } = useStore();
  const [t, setT] = useState<Transcript | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<Mode>(() => load<Mode>("transcriptMode", "video"));
  const [subs, setSubs] = useState(() => load("subtitles", "on") === "on");
  const [follow, setFollow] = useState(true);
  const [time, setTime] = useState(0);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const mediaRef = useRef<HTMLVideoElement & HTMLAudioElement>(null);
  const listRef = useRef<HTMLOListElement>(null);
  const resumeAt = useRef<number | null>(null);

  useEffect(() => {
    api
      .get<Transcript>(`/api/transcripts/${id}`)
      .then(setT)
      .catch((e) => setError((e as Error).message));
  }, [id]);

  const segments = t?.segments ?? [];
  const current = segmentAt(segments, time);
  const activeIdx = current >= 0 && time <= segments[current].end + 1.5 ? segments[current].idx : null;

  // Smooth clock: rAF while playing (timeupdate alone fires only ~4x per second).
  useEffect(() => {
    const m = mediaRef.current;
    if (!m) return;
    let raf = 0;
    const tick = () => {
      setTime(m.currentTime);
      if (!m.paused) raf = requestAnimationFrame(tick);
    };
    const onPlay = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(tick);
    };
    const onSeek = () => setTime(m.currentTime);
    m.addEventListener("play", onPlay);
    m.addEventListener("seeked", onSeek);
    m.addEventListener("timeupdate", onSeek);
    return () => {
      cancelAnimationFrame(raf);
      m.removeEventListener("play", onPlay);
      m.removeEventListener("seeked", onSeek);
      m.removeEventListener("timeupdate", onSeek);
    };
  }, [t, mode]);

  // Subtitle track on/off
  useEffect(() => {
    const m = mediaRef.current as HTMLVideoElement | null;
    if (!m || mode !== "video") return;
    const apply = () => {
      for (const track of Array.from(m.textTracks)) track.mode = subs ? "showing" : "hidden";
    };
    apply();
    m.textTracks.addEventListener?.("addtrack", apply);
    return () => m.textTracks.removeEventListener?.("addtrack", apply);
  }, [subs, mode, t]);

  // Switching between video and script view keeps the position.
  const switchMode = (m: Mode) => {
    resumeAt.current = mediaRef.current?.currentTime ?? null;
    setMode(m);
    save("transcriptMode", m);
  };
  const onLoaded = () => {
    const m = mediaRef.current;
    const deepLink = Number(params.get("t"));
    if (m && resumeAt.current != null) {
      m.currentTime = resumeAt.current;
      resumeAt.current = null;
    } else if (m && deepLink) m.currentTime = deepLink;
  };

  // Keep the active line in view
  useEffect(() => {
    if (!follow || activeIdx == null || editing != null) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${activeIdx}"]`);
    if (!el || !listRef.current) return;
    const box = listRef.current.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    if (r.top < box.top + 40 || r.bottom > box.bottom - 40) {
      el.scrollIntoView({ block: "center", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    }
  }, [activeIdx, follow, editing]);

  const seek = useCallback((s: number) => {
    const m = mediaRef.current;
    if (!m) return;
    m.currentTime = s;
    setTime(s);
    m.play().catch(() => undefined);
  }, []);

  const needle = q.trim().toLowerCase();
  const visible = useMemo(() => (needle ? segments.filter((s) => s.text.toLowerCase().includes(needle)) : segments), [segments, needle]);

  const saveEdit = async (idx: number) => {
    try {
      const updated = await api.patch<Transcript>(`/api/transcripts/${id}`, { edits: [{ idx, text: draft }] });
      setT((old) => (old ? { ...old, segments: updated.segments, edited: 1 } : old));
      setEditing(null);
      // reload the subtitle track so it shows the edit
      const m = mediaRef.current;
      const track = m?.querySelector("track");
      if (track) track.src = `/api/transcripts/${id}/subtitles.vtt?v=${Date.now()}`;
    } catch (e) {
      toast((e as Error).message, "bad");
    }
  };

  const copyAll = async () => {
    await navigator.clipboard.writeText(segments.map((s) => s.text).join(" "));
    toast("Transcript copied", "ok");
  };

  const remove = async () => {
    if (!confirm("Delete this transcript? The exported files on disk stay where they are.")) return;
    await api.del(`/api/transcripts/${id}`);
    navigate("/transcripts");
  };

  if (error) return <ErrorBox error={error} />;
  if (!t)
    return (
      <div>
        <Skeleton className="mb-4 h-8 w-96" />
        <Skeleton className="h-96" />
      </div>
    );

  const media = `/api/media/${t.stream_id}/${t.variant}`;
  const rtf = t.audio_seconds ? t.processing_seconds / t.audio_seconds : null;
  const hasWords = segments.some((s) => s.words?.length);

  const list = (
    <ol
      ref={listRef}
      className={clsx(
        "overflow-y-auto rounded-lg border border-line",
        mode === "video" ? "max-h-[calc(100vh-260px)]" : "max-h-[calc(100vh-300px)]",
      )}
      aria-label="Transcript"
    >
      {visible.map((s) => (
        <Line
          key={s.idx}
          s={s}
          active={s.idx === activeIdx}
          time={time}
          big={mode === "script"}
          needle={needle}
          editing={editing === s.idx}
          draft={draft}
          canSeek={t.media_available}
          onSeek={() => seek(s.start)}
          onEdit={() => {
            setEditing(s.idx);
            setDraft(s.text);
          }}
          onDraft={setDraft}
          onSave={() => saveEdit(s.idx)}
          onCancel={() => setEditing(null)}
        />
      ))}
    </ol>
  );

  const searchBox = (
    <div className="relative mb-3">
      <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" aria-hidden />
      <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search in this lecture" className="w-full pl-9 pr-24" aria-label="Search in transcript" />
      {needle && <span className="tnum absolute top-1/2 right-3 -translate-y-1/2 text-[12px] text-muted">{visible.length} matches</span>}
    </div>
  );

  return (
    <div>
      <Link to="/transcripts" className="mb-3 inline-flex items-center gap-1 text-muted hover:text-chalk">
        <ArrowLeft className="size-4" aria-hidden /> Transcripts
      </Link>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-[22px] font-semibold tracking-[-0.01em]">{t.title}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[13px] text-muted">
            <span>{t.course_name}</span>
            <Badge>{t.model}</Badge>
            {t.language && <Badge>{t.language}</Badge>}
            <span className="tnum">
              {duration(t.audio_seconds)} audio, transcribed in {duration(t.processing_seconds)}
              {rtf != null && rtf > 0 && ` (${(1 / rtf).toFixed(1)}× real time)`}
            </span>
            {t.edited ? <Badge tone="info">edited</Badge> : null}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <div className="flex rounded-md border border-line-strong p-0.5" role="group" aria-label="View">
            <button
              onClick={() => switchMode("video")}
              aria-pressed={mode === "video"}
              className={clsx("inline-flex h-7 items-center gap-1.5 rounded px-2.5 text-[13px] font-medium", mode === "video" ? "bg-board-3 text-chalk" : "text-muted hover:text-chalk")}
            >
              <MonitorPlay className="size-3.5" aria-hidden /> Video
            </button>
            <button
              onClick={() => switchMode("script")}
              aria-pressed={mode === "script"}
              className={clsx("inline-flex h-7 items-center gap-1.5 rounded px-2.5 text-[13px] font-medium", mode === "script" ? "bg-board-3 text-chalk" : "text-muted hover:text-chalk")}
            >
              <FileText className="size-3.5" aria-hidden /> Script only
            </button>
          </div>
          <Button size="sm" variant="ghost" icon={<Copy className="size-3.5" />} onClick={copyAll}>
            Copy text
          </Button>
          {FORMATS.map((f) => (
            <a key={f} href={`/api/transcripts/${id}/export/${f}`} download className="inline-flex h-7 items-center gap-1 rounded-md border border-line-strong px-2.5 text-[13px] font-medium hover:bg-board-3">
              <Download className="size-3.5" aria-hidden />
              {f.toUpperCase()}
            </a>
          ))}
          <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={remove} aria-label="Delete transcript" />
        </div>
      </div>

      {mode === "video" ? (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
          <div className="lg:sticky lg:top-0 lg:self-start">
            {t.media_available ? (
              <video
                ref={mediaRef}
                src={media}
                controls
                preload="metadata"
                onLoadedMetadata={onLoaded}
                className="aspect-video w-full rounded-lg border border-line bg-black"
              >
                <track kind="subtitles" src={`/api/transcripts/${id}/subtitles.vtt`} srcLang={t.language || "de"} label="Transcript" default={subs} />
              </video>
            ) : (
              <NoMedia />
            )}
            <div className="mt-3 flex flex-wrap items-center gap-4 text-[13px] text-chalk-2">
              <button
                onClick={() => {
                  setSubs((v) => !v);
                  save("subtitles", subs ? "off" : "on");
                }}
                aria-pressed={subs}
                className={clsx("inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 font-medium", subs ? "border-accent/60 bg-accent/10 text-accent" : "border-line-strong hover:bg-board-3")}
                title="Show subtitles in the video"
              >
                <Captions className="size-4" aria-hidden /> Subtitles {subs ? "on" : "off"}
              </button>
              <FollowToggle follow={follow} setFollow={setFollow} />
              {hasWords && <span className="text-muted">Click any line to jump there.</span>}
            </div>
          </div>
          <div className="min-w-0">
            {searchBox}
            {list}
          </div>
        </div>
      ) : (
        <div className="mx-auto max-w-[860px]">
          <div className="sticky top-0 z-10 -mx-1 mb-3 flex flex-wrap items-center gap-3 rounded-lg border border-line bg-board-2/95 px-3 py-2 backdrop-blur">
            {t.media_available ? (
              <audio ref={mediaRef} src={media} controls preload="metadata" onLoadedMetadata={onLoaded} className="h-9 min-w-[260px] flex-1" />
            ) : (
              <span className="flex-1 text-[13px] text-muted">Recording not on disk; playback unavailable.</span>
            )}
            <span className="tnum text-[13px] text-muted">{clock(time)}</span>
            <FollowToggle follow={follow} setFollow={setFollow} />
          </div>
          {searchBox}
          {list}
        </div>
      )}
    </div>
  );
}

function NoMedia() {
  return (
    <div className="grid aspect-video place-items-center rounded-lg border border-dashed border-line px-6 text-center text-muted">
      The recording isn't on disk anymore, so playback is unavailable. The transcript is still searchable.
    </div>
  );
}

function FollowToggle({ follow, setFollow }: { follow: boolean; setFollow: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-[13px] text-chalk-2">
      <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
      Follow playback
    </label>
  );
}

interface LineProps {
  s: Seg;
  active: boolean;
  time: number;
  big: boolean;
  needle: string;
  editing: boolean;
  draft: string;
  canSeek: boolean;
  onSeek: () => void;
  onEdit: () => void;
  onDraft: (v: string) => void;
  onSave: () => void;
  onCancel: () => void;
}

function Line({ s, active, time, big, needle, editing, draft, canSeek, onSeek, onEdit, onDraft, onSave, onCancel }: LineProps) {
  return (
    <li
      data-idx={s.idx}
      onClick={editing || !canSeek ? undefined : onSeek}
      className={clsx(
        "group relative flex gap-3 border-b border-line/60 px-3 py-2 last:border-b-0",
        canSeek && !editing && "cursor-pointer hover:bg-board-2",
        active && "bg-accent/10 before:absolute before:inset-y-0 before:left-0 before:w-[3px] before:bg-accent",
      )}
    >
      <button
        onClick={(e) => {
          e.stopPropagation();
          onSeek();
        }}
        disabled={!canSeek}
        className={clsx("tnum w-14 shrink-0 pt-0.5 text-left text-[13px] hover:underline disabled:no-underline", active ? "font-medium text-accent" : "text-muted")}
        aria-label={`Play from ${clock(s.start)}`}
      >
        {clock(s.start)}
      </button>
      {editing ? (
        <div className="flex flex-1 flex-col gap-2" onClick={(e) => e.stopPropagation()}>
          <textarea
            value={draft}
            onChange={(e) => onDraft(e.target.value)}
            className="min-h-[64px] w-full rounded-md border border-accent bg-board-2 p-2 text-chalk focus:outline-none"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) onSave();
              if (e.key === "Escape") onCancel();
            }}
          />
          <div className="flex gap-1.5">
            <Button size="sm" variant="primary" icon={<Check className="size-3.5" />} onClick={onSave}>
              Save
            </Button>
            <Button size="sm" variant="ghost" icon={<X className="size-3.5" />} onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <>
          <p className={clsx("flex-1", big ? "text-[16px] leading-[1.7]" : "leading-relaxed", !active && "text-chalk-2")}>
            {active && s.words?.length && !needle ? <Words words={s.words} time={time} /> : highlight(s.text, needle)}
          </p>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onEdit();
            }}
            className="h-6 shrink-0 rounded p-1 text-muted opacity-0 group-hover:opacity-100 hover:bg-board-3 hover:text-chalk focus:opacity-100"
            aria-label="Edit this line"
          >
            <Pencil className="size-3.5" />
          </button>
        </>
      )}
    </li>
  );
}

/** Karaoke-style: spoken words in chalk, the current word marked, upcoming words dimmed. */
function Words({ words, time }: { words: [number, number, string][]; time: number }) {
  return (
    <>
      {words.map(([start, end, w], i) => {
        const state = time >= end ? "past" : time >= start ? "now" : "next";
        return (
          <span
            key={i}
            className={clsx(
              "rounded-[3px] transition-colors duration-100",
              state === "now" && "bg-accent text-accent-ink",
              state === "past" && "text-chalk",
              state === "next" && "text-chalk-2/70",
            )}
          >
            {w}
          </span>
        );
      })}
    </>
  );
}

function highlight(text: string, needle: string) {
  if (!needle) return text;
  const out: React.ReactNode[] = [];
  const lower = text.toLowerCase();
  let i = 0;
  let k = 0;
  for (;;) {
    const j = lower.indexOf(needle, i);
    if (j < 0) break;
    out.push(text.slice(i, j), <mark key={k++}>{text.slice(j, j + needle.length)}</mark>);
    i = j + needle.length;
  }
  out.push(text.slice(i));
  return out;
}
