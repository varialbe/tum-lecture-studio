import clsx from "clsx";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, AudioLines, Download, FileText, RefreshCw, Video } from "lucide-react";
import { api, type CourseSummary, type Job, type Recording } from "../lib/api";
import { useStore } from "../lib/store";
import { Badge, Button, Empty, ErrorBox, PageHeader, ProgressBar, Select, Skeleton } from "../components/ui";
import { STAGE_LABEL } from "../components/StageRail";
import { bytes, date, duration, time, VARIANT_LABEL } from "../lib/format";

interface Detail {
  course: CourseSummary;
  recordings: Recording[];
  upcoming: Recording[];
}

export default function CourseDetail() {
  const { semester = "", slug = "" } = useParams();
  const { jobs, toast, libraryVersion, system } = useStore();
  const [data, setData] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [variant, setVariant] = useState("COMB");
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    async (refresh = false) => {
      try {
        setError(null);
        setData(await api.get<Detail>(`/api/courses/${semester}/${slug}${refresh ? "?refresh=true" : ""}`));
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [semester, slug],
  );
  useEffect(() => {
    load();
  }, [load, libraryVersion]);
  useEffect(() => {
    if (system?.settings.download_variant) setVariant(system.settings.download_variant);
  }, [system?.settings.download_variant]);

  // live job overlay per stream
  const liveJobs = useMemo(() => {
    const m = new Map<number, Job[]>();
    for (const j of Object.values(jobs)) {
      if (j.course_slug !== slug && j.kind === "download") continue;
      const arr = m.get(j.stream_id) ?? [];
      arr.push(j);
      m.set(j.stream_id, arr);
    }
    for (const arr of m.values()) arr.sort((a, b) => b.created_at - a.created_at);
    return m;
  }, [jobs, slug]);

  const capture = !!system?.settings.stream_capture;
  const canGet = (r: Recording, v: string) => r.downloadable_variants.includes(v) || (capture && r.variants.includes(v));
  const vods = data?.recordings.filter((r) => r.is_vod) ?? [];
  const selectable = vods.filter((r) => canGet(r, variant));

  const download = async (ids: number[], v = variant) => {
    setBusy(true);
    try {
      const r = await api.post<{ results: { error?: string; duplicate?: string; job?: Job }[] }>("/api/downloads", {
        items: ids.map((id) => ({ semester, slug, stream_id: id, variant: v })),
      });
      const errs = r.results.filter((x) => x.error);
      const dup = r.results.filter((x) => x.duplicate).length;
      const ok = r.results.length - errs.length - dup;
      toast(
        [ok && `${ok} queued`, dup && `${dup} already downloaded or queued`, errs.length && `${errs.length} not permitted`].filter(Boolean).join(", "),
        errs.length ? "bad" : "ok",
      );
      setSelected(new Set());
    } catch (e) {
      toast((e as Error).message, "bad");
    } finally {
      setBusy(false);
    }
  };

  const transcribe = async (id: number, v: string) => {
    try {
      const r = await api.post<{ results: { error?: string; duplicate?: string }[] }>("/api/transcribe", { items: [{ stream_id: id, variant: v }] });
      const res = r.results[0];
      toast(res.error ?? (res.duplicate ? "Already transcribing" : "Transcription queued"), res.error ? "bad" : "ok");
    } catch (e) {
      toast((e as Error).message, "bad");
    }
  };

  if (error)
    return (
      <div>
        <BackLink />
        <ErrorBox error={error} action={<button className="text-accent hover:underline" onClick={() => load(true)}>Try again</button>} />
      </div>
    );
  if (!data)
    return (
      <div>
        <BackLink />
        <Skeleton className="mb-3 h-8 w-80" />
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="mb-2 h-12" />
        ))}
      </div>
    );

  const c = data.course;
  const allChecked = selectable.length > 0 && selectable.every((r) => selected.has(r.id));
  return (
    <div>
      <BackLink />
      <PageHeader
        title={c.name}
        sub={
          <span className="flex flex-wrap items-center gap-2">
            {c.tum_online_identifier || c.slug} · {c.semester_label}
            {c.priority && <Badge tone="accent">{c.priority}</Badge>}
            {c.downloads_enabled ? (
              <Badge tone="info">Downloads enabled</Badge>
            ) : capture ? (
              <Badge tone="warn">Stream recording</Badge>
            ) : (
              <Badge>Downloads disabled by the lecturer</Badge>
            )}
          </span>
        }
        actions={
          <>
            <Button variant="ghost" icon={<RefreshCw className="size-4" />} onClick={() => load(true)}>
              Refresh
            </Button>
            <Select value={variant} onChange={(e) => setVariant(e.target.value)} aria-label="Video variant">
              {["COMB", "PRES", "CAM"].map((v) => (
                <option key={v} value={v}>
                  {VARIANT_LABEL[v]}
                </option>
              ))}
            </Select>
            <Button
              variant="primary"
              icon={<Download className="size-4" />}
              busy={busy}
              disabled={!selected.size && !selectable.length}
              onClick={() => download(selected.size ? [...selected] : selectable.map((r) => r.id))}
            >
              {selected.size ? `Download ${selected.size} selected` : `Download all (${selectable.length})`}
            </Button>
          </>
        }
      />

      {vods.length === 0 ? (
        <Empty icon={<Video className="size-6" />} title="No recordings yet">
          {data.upcoming.length ? `${data.upcoming.length} lecture${data.upcoming.length === 1 ? " is" : "s are"} scheduled.` : "This course has no lectures on TUM-Live."}
        </Empty>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full min-w-[760px] text-left">
            <thead className="bg-board-2 text-[12px] text-muted">
              <tr>
                <th className="w-10 px-3 py-2">
                  <input
                    type="checkbox"
                    checked={allChecked}
                    onChange={() => setSelected(allChecked ? new Set() : new Set(selectable.map((r) => r.id)))}
                    aria-label="Select all downloadable recordings"
                  />
                </th>
                <th className="px-3 py-2 font-medium">Lecture</th>
                <th className="px-3 py-2 font-medium">Date</th>
                <th className="px-3 py-2 text-right font-medium">Length</th>
                <th className="px-3 py-2 font-medium">Variants</th>
                <th className="px-3 py-2 font-medium">Download</th>
                <th className="px-3 py-2 font-medium">Transcript</th>
              </tr>
            </thead>
            <tbody>
              {vods.map((r) => (
                <RecordingRow
                  key={r.id}
                  r={r}
                  variant={variant}
                  capture={capture}
                  jobs={liveJobs.get(r.id) ?? []}
                  checked={selected.has(r.id)}
                  onCheck={() =>
                    setSelected((s) => {
                      const n = new Set(s);
                      if (n.has(r.id)) n.delete(r.id);
                      else n.add(r.id);
                      return n;
                    })
                  }
                  onDownload={(v) => download([r.id], v)}
                  onTranscribe={(v) => transcribe(r.id, v)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.upcoming.length > 0 && (
        <p className="mt-4 text-[13px] text-muted">
          {data.upcoming.length} upcoming lecture{data.upcoming.length === 1 ? "" : "s"} not yet recorded.
        </p>
      )}
    </div>
  );
}

function BackLink() {
  return (
    <Link to="/courses" className="mb-3 inline-flex items-center gap-1 text-muted hover:text-chalk">
      <ArrowLeft className="size-4" aria-hidden /> Courses
    </Link>
  );
}

function RecordingRow({
  r,
  variant,
  capture,
  jobs,
  checked,
  onCheck,
  onDownload,
  onTranscribe,
}: {
  r: Recording;
  variant: string;
  capture: boolean;
  jobs: Job[];
  checked: boolean;
  onCheck: () => void;
  onDownload: (v: string) => void;
  onTranscribe: (v: string) => void;
}) {
  const permitted = r.downloadable_variants.includes(variant);
  const can = permitted || (capture && r.variants.includes(variant));
  const local = r.local;
  const downloaded = local?.downloaded ?? [];
  const dlJob = jobs.find((j) => j.kind === "download" && (j.status === "running" || j.status === "queued"));
  const failedDl = jobs.find((j) => j.kind === "download" && j.status === "failed" && j.variant === variant);
  const txJob = jobs.find((j) => j.kind === "transcribe" && (j.status === "running" || j.status === "queued"));
  const transcripts = local?.transcripts ?? [];
  const localVariant = downloaded.find((d) => d.variant === variant) ?? downloaded[0];

  return (
    <tr className={clsx("border-t border-line align-middle", checked && "bg-accent/5")}>
      <td className="px-3 py-2.5">
        <input type="checkbox" checked={checked} disabled={!can} onChange={onCheck} aria-label={`Select ${r.name}`} />
      </td>
      <td className="max-w-[340px] px-3 py-2.5">
        <div className="truncate font-medium" title={r.name}>
          {r.name || "Untitled lecture"}
        </div>
      </td>
      <td className="px-3 py-2.5 whitespace-nowrap text-chalk-2">
        {date(r.start)} <span className="text-muted">{time(r.start)}</span>
      </td>
      <td className="tnum px-3 py-2.5 text-right whitespace-nowrap text-chalk-2">{duration(r.duration)}</td>
      <td className="px-3 py-2.5">
        <div className="flex gap-1">
          {r.variants.map((v) => (
            <Badge key={v} tone={r.downloadable_variants.includes(v) ? "neutral" : undefined} title={r.downloadable_variants.includes(v) ? "Downloadable" : "Stream only"}>
              <span className={r.downloadable_variants.includes(v) ? "" : "opacity-50 line-through"}>{v}</span>
            </Badge>
          ))}
        </div>
      </td>
      <td className="w-[210px] px-3 py-2.5">
        {dlJob ? (
          <div className="flex flex-col gap-1">
            <ProgressBar value={dlJob.status === "running" ? dlJob.progress : null} />
            <span className="tnum text-[12px] text-muted">
              {STAGE_LABEL[dlJob.stage] ?? dlJob.stage}
              {dlJob.progress != null && dlJob.stage === "downloading" ? ` ${Math.round(dlJob.progress * 100)}%` : ""}
            </span>
          </div>
        ) : downloaded.length ? (
          <span className="flex items-center gap-1.5">
            <Badge tone="ok">{downloaded.map((d) => d.variant).join(", ")}</Badge>
            <span className="tnum text-[12px] text-muted">{bytes(localVariant?.size)}</span>
          </span>
        ) : can ? (
          <div className="flex items-center gap-2">
            <Button size="sm" icon={<Download className="size-3.5" />} onClick={() => onDownload(variant)} title={permitted ? "Download the file TUM-Live offers" : "Record the stream"}>
              {failedDl ? "Retry" : permitted ? "Download" : "Record"}
            </Button>
            {failedDl && <span className="truncate text-[12px] text-bad" title={failedDl.error ?? ""}>Failed</span>}
          </div>
        ) : (
          <span className="text-[13px] text-muted" title="The lecturer has not allowed downloads. You can turn on stream recording in Models & settings.">
            Stream only
          </span>
        )}
      </td>
      <td className="w-[180px] px-3 py-2.5">
        {txJob ? (
          <div className="flex flex-col gap-1">
            <ProgressBar value={txJob.status === "running" ? txJob.progress : null} />
            <span className="tnum text-[12px] text-muted">
              {STAGE_LABEL[txJob.stage] ?? txJob.stage}
              {txJob.progress != null && txJob.stage === "transcribing" ? ` ${Math.round(txJob.progress * 100)}%` : ""}
            </span>
          </div>
        ) : transcripts.length ? (
          <Link to={`/transcripts/${transcripts[0].id}`} className="inline-flex items-center gap-1.5 text-accent hover:underline">
            <FileText className="size-4" aria-hidden /> Open
          </Link>
        ) : localVariant ? (
          <Button size="sm" icon={<AudioLines className="size-3.5" />} onClick={() => onTranscribe(localVariant.variant)}>
            Transcribe
          </Button>
        ) : (
          <span className="text-[13px] text-muted">Download first</span>
        )}
      </td>
    </tr>
  );
}
