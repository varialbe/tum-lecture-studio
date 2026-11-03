import clsx from "clsx";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, Check, Copy, Download, Pencil, Search, Trash2, X } from "lucide-react";
import { api, type Transcript } from "../lib/api";
import { useStore } from "../lib/store";
import { Badge, Button, ErrorBox, Input, Skeleton } from "../components/ui";
import { clock, duration } from "../lib/format";

const FORMATS = ["txt", "md", "srt", "vtt", "json"] as const;

export default function TranscriptView() {
  const { id = "" } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { toast } = useStore();
  const [t, setT] = useState<Transcript | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [current, setCurrent] = useState(-1);
  const [follow, setFollow] = useState(true);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const videoRef = useRef<HTMLVideoElement>(null);
  const listRef = useRef<HTMLOListElement>(null);

  useEffect(() => {
    api
      .get<Transcript>(`/api/transcripts/${id}`)
      .then(setT)
      .catch((e) => setError((e as Error).message));
  }, [id]);

  // deep link ?t=seconds from search results
  useEffect(() => {
    const ts = Number(params.get("t"));
    if (t && ts && videoRef.current) videoRef.current.currentTime = ts;
  }, [t, params]);

  const segments = t?.segments ?? [];
  const needle = q.trim().toLowerCase();
  const matches = useMemo(
    () => (needle ? segments.filter((s) => s.text.toLowerCase().includes(needle)).map((s) => s.idx) : []),
    [segments, needle],
  );

  const onTime = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    const time = v.currentTime;
    // binary search for the active segment
    let lo = 0;
    let hi = segments.length - 1;
    let found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (segments[mid].start <= time) {
        found = mid;
        lo = mid + 1;
      } else hi = mid - 1;
    }
    if (found !== current) setCurrent(found);
  }, [segments, current]);

  useEffect(() => {
    if (!follow || current < 0 || editing != null) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${segments[current]?.idx}"]`);
    el?.scrollIntoView({ block: "center", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }, [current, follow, segments, editing]);

  const seek = (s: number) => {
    const v = videoRef.current;
    if (v) {
      v.currentTime = s;
      v.play().catch(() => undefined);
    }
  };

  const saveEdit = async (idx: number) => {
    try {
      const updated = await api.patch<Transcript>(`/api/transcripts/${id}`, { edits: [{ idx, text: draft }] });
      setT((old) => (old ? { ...old, segments: updated.segments, edited: 1 } : old));
      setEditing(null);
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

  const rtf = t.audio_seconds ? t.processing_seconds / t.audio_seconds : null;
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
              {rtf != null && ` (${(1 / rtf).toFixed(1)}× real time)`}
            </span>
            {t.edited ? <Badge tone="info">edited</Badge> : null}
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5">
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

      <div className="grid gap-5 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <div className="lg:sticky lg:top-0 lg:self-start">
          {t.media_available ? (
            <video
              ref={videoRef}
              src={`/api/media/${t.stream_id}/${t.variant}`}
              controls
              preload="metadata"
              onTimeUpdate={onTime}
              className="aspect-video w-full rounded-lg border border-line bg-black"
            />
          ) : (
            <div className="grid aspect-video place-items-center rounded-lg border border-dashed border-line px-6 text-center text-muted">
              The recording isn't on disk anymore, so playback is unavailable. The transcript is still searchable.
            </div>
          )}
          <label className="mt-3 flex items-center gap-2 text-[13px] text-chalk-2">
            <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
            Follow playback
          </label>
        </div>

        <div className="min-w-0">
          <div className="relative mb-3">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" aria-hidden />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search in this lecture" className="w-full pl-9 pr-24" aria-label="Search in transcript" />
            {needle && <span className="tnum absolute top-1/2 right-3 -translate-y-1/2 text-[12px] text-muted">{matches.length} matches</span>}
          </div>
          <ol ref={listRef} className="max-h-[calc(100vh-260px)] overflow-y-auto rounded-lg border border-line" aria-label="Transcript">
            {segments
              .filter((s) => !needle || matches.includes(s.idx))
              .map((s) => {
                const active = segments[current]?.idx === s.idx;
                return (
                  <li
                    key={s.idx}
                    data-idx={s.idx}
                    className={clsx("group flex gap-3 border-b border-line/60 px-3 py-2 last:border-b-0", active && "bg-accent/10")}
                  >
                    <button
                      onClick={() => seek(s.start)}
                      className={clsx("tnum w-14 shrink-0 pt-0.5 text-left text-[13px] hover:underline", active ? "text-accent" : "text-muted")}
                      aria-label={`Play from ${clock(s.start)}`}
                    >
                      {clock(s.start)}
                    </button>
                    {editing === s.idx ? (
                      <div className="flex flex-1 flex-col gap-2">
                        <textarea
                          value={draft}
                          onChange={(e) => setDraft(e.target.value)}
                          className="min-h-[64px] w-full rounded-md border border-accent bg-board-2 p-2 text-chalk focus:outline-none"
                          autoFocus
                          onKeyDown={(e) => {
                            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) saveEdit(s.idx);
                            if (e.key === "Escape") setEditing(null);
                          }}
                        />
                        <div className="flex gap-1.5">
                          <Button size="sm" variant="primary" icon={<Check className="size-3.5" />} onClick={() => saveEdit(s.idx)}>
                            Save
                          </Button>
                          <Button size="sm" variant="ghost" icon={<X className="size-3.5" />} onClick={() => setEditing(null)}>
                            Cancel
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <p className="flex-1 leading-relaxed">{highlight(s.text, needle)}</p>
                        <button
                          onClick={() => {
                            setEditing(s.idx);
                            setDraft(s.text);
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
              })}
          </ol>
        </div>
      </div>
    </div>
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
