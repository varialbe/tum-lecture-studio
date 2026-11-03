import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Cpu, FileText, HardDrive, Upload } from "lucide-react";
import { api, type TranscriptSummary } from "../lib/api";
import { useStore } from "../lib/store";
import { Button, PageHeader } from "../components/ui";
import { JobRow } from "./Queue";
import { ago, bytes, duration } from "../lib/format";

interface LibRec {
  stream_id: number;
  variant: string;
  title: string;
  course_name: string;
  size: number;
  duration: number;
  exists: boolean;
}

export default function Dashboard() {
  const { session, jobs, system, libraryVersion, toast } = useStore();
  const [lib, setLib] = useState<LibRec[]>([]);
  const [transcripts, setTranscripts] = useState<TranscriptSummary[]>([]);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    api.get<{ recordings: LibRec[] }>("/api/library").then((r) => setLib(r.recordings)).catch(() => undefined);
    api.get<{ transcripts: TranscriptSummary[] }>("/api/transcripts").then((r) => setTranscripts(r.transcripts)).catch(() => undefined);
  }, [libraryVersion]);

  const active = useMemo(() => Object.values(jobs).filter((j) => j.status === "running" || j.status === "queued").sort((a, b) => a.created_at - b.created_at), [jobs]);
  const storage = lib.filter((r) => r.exists).reduce((s, r) => s + (r.size || 0), 0);
  const hours = lib.filter((r) => r.exists).reduce((s, r) => s + (r.duration || 0), 0);
  const hw = system?.hardware;

  const upload = async (file: File) => {
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await api.post<{ stream_id: number; variant: string }>("/api/library/import", fd);
      await api.post("/api/transcribe", { items: [{ stream_id: r.stream_id, variant: r.variant }] });
      toast(`${file.name} imported and queued for transcription`, "ok");
    } catch (e) {
      toast((e as Error).message, "bad");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div>
      <PageHeader
        title={session?.state === "authenticated" ? `Hello, ${session.display_name?.split(" ")[0]}` : "Lecture Studio"}
        sub="Download TUM-Live recordings and transcribe them on this computer."
      />

      <div className="mb-8 grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
        <Stat icon={<HardDrive className="size-4" />} label="Recordings on disk" value={`${lib.filter((r) => r.exists).length}`} note={`${bytes(storage)} · ${duration(hours)}`} />
        <Stat icon={<FileText className="size-4" />} label="Transcripts" value={`${transcripts.length}`} note={transcripts[0] ? `latest ${ago(transcripts[0].created_at)}` : "none yet"} />
        <Stat
          icon={<Cpu className="size-4" />}
          label="Speech-to-text"
          value={system?.settings.model ?? "–"}
          note={hw ? (hw.cuda_usable ? `${hw.cuda_devices[0]?.name} (CUDA)` : hw.apple_silicon ? "Apple Silicon" : "CPU") : ""}
        />
      </div>

      <section className="mb-8">
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className="text-[15px] font-semibold">In progress</h2>
          <Link to="/queue" className="text-[13px] text-muted hover:text-chalk">
            Open queue
          </Link>
        </div>
        {active.length ? (
          <ul className="flex flex-col gap-2">
            {active.slice(0, 4).map((j) => (
              <JobRow key={j.id} job={j} />
            ))}
          </ul>
        ) : (
          <div className="rounded-lg border border-dashed border-line px-5 py-6 text-muted">
            Nothing running. <Link to="/courses" className="text-accent hover:underline">Browse courses</Link> to pick lectures.
          </div>
        )}
      </section>

      <div className="grid gap-8 lg:grid-cols-2">
        <section>
          <h2 className="mb-3 text-[15px] font-semibold">Recent transcripts</h2>
          {transcripts.length ? (
            <ul className="divide-y divide-line rounded-lg border border-line">
              {transcripts.slice(0, 6).map((t) => (
                <li key={t.id}>
                  <Link to={`/transcripts/${t.id}`} className="flex items-baseline gap-3 px-4 py-2.5 hover:bg-board-2">
                    <span className="min-w-0 flex-1 truncate">{t.title}</span>
                    <span className="tnum shrink-0 text-[13px] text-muted">{duration(t.audio_seconds)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted">Transcripts appear here after a lecture is transcribed.</p>
          )}
        </section>
        <section>
          <h2 className="mb-3 text-[15px] font-semibold">Transcribe your own file</h2>
          <p className="mb-3 text-muted">Audio or video you already have on disk, e.g. a lecture recorded elsewhere.</p>
          <label className="inline-block">
            <input type="file" accept="audio/*,video/*" className="sr-only" disabled={uploading} onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            <Button busy={uploading} icon={<Upload className="size-4" />} onClick={(e) => (e.currentTarget.previousElementSibling as HTMLInputElement)?.click()}>
              Choose file
            </Button>
          </label>
        </section>
      </div>
    </div>
  );
}

function Stat({ icon, label, value, note }: { icon: React.ReactNode; label: string; value: string; note?: string }) {
  return (
    <div className="rounded-lg border border-line bg-board-2 px-4 py-3.5">
      <div className="flex items-center gap-2 text-[13px] text-muted">
        {icon}
        {label}
      </div>
      <div className="tnum mt-1 truncate text-[20px] font-semibold">{value}</div>
      {note && <div className="truncate text-[13px] text-muted">{note}</div>}
    </div>
  );
}
