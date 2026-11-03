import clsx from "clsx";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AudioLines, Download, Inbox, RotateCcw, Trash2, X } from "lucide-react";
import { api, type Job } from "../lib/api";
import { useStore } from "../lib/store";
import { Badge, Button, Empty, PageHeader } from "../components/ui";
import { STAGE_LABEL, StageRail } from "../components/StageRail";
import { ago, bytes, duration, speed, VARIANT_LABEL } from "../lib/format";

type Tab = "active" | "done" | "failed";

export default function Queue() {
  const { jobs, toast } = useStore();
  const [tab, setTab] = useState<Tab>("active");
  const all = useMemo(() => Object.values(jobs).sort((a, b) => b.created_at - a.created_at), [jobs]);
  const groups: Record<Tab, Job[]> = {
    active: all.filter((j) => j.status === "running" || j.status === "queued").sort((a, b) => (a.status === "running" ? -1 : 1) - (b.status === "running" ? -1 : 1) || a.created_at - b.created_at),
    done: all.filter((j) => j.status === "completed"),
    failed: all.filter((j) => j.status === "failed" || j.status === "cancelled"),
  };
  const list = groups[tab];

  const act = async (fn: () => Promise<unknown>, msg?: string) => {
    try {
      await fn();
      if (msg) toast(msg);
    } catch (e) {
      toast((e as Error).message, "bad");
    }
  };

  return (
    <div>
      <PageHeader
        title="Queue"
        sub="Downloads and transcriptions run in the background and continue after a restart."
        actions={
          groups.done.length > 0 && (
            <Button variant="ghost" icon={<Trash2 className="size-4" />} onClick={() => act(() => api.post("/api/jobs/clear"), "Finished jobs cleared")}>
              Clear finished
            </Button>
          )
        }
      />
      <div role="tablist" className="mb-4 flex gap-1 border-b border-line">
        {(
          [
            ["active", "In progress"],
            ["done", "Finished"],
            ["failed", "Failed or cancelled"],
          ] as [Tab, string][]
        ).map(([t, label]) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={clsx("-mb-px border-b-2 px-3 py-2 text-sm", tab === t ? "border-accent font-medium text-chalk" : "border-transparent text-muted hover:text-chalk")}
          >
            {label} <span className="tnum text-muted">{groups[t].length}</span>
          </button>
        ))}
      </div>

      {list.length === 0 ? (
        <Empty icon={<Inbox className="size-6" />} title={tab === "active" ? "Nothing in progress" : tab === "done" ? "No finished jobs" : "No failed jobs"}>
          {tab === "active" && (
            <span>
              Pick lectures in <Link to="/courses" className="text-accent hover:underline">Courses</Link> to download or transcribe them.
            </span>
          )}
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          {list.map((j) => (
            <JobRow
              key={j.id}
              job={j}
              onCancel={() => act(() => api.post(`/api/jobs/${j.id}/cancel`), "Cancelled")}
              onRetry={() => act(() => api.post(`/api/jobs/${j.id}/retry`), "Retrying")}
              onRemove={() => act(() => api.del(`/api/jobs/${j.id}`))}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

export function JobRow({ job: j, onCancel, onRetry, onRemove }: { job: Job; onCancel?: () => void; onRetry?: () => void; onRemove?: () => void }) {
  const running = j.status === "running";
  const elapsed = j.started_at ? (j.finished_at ?? Date.now() / 1000) - j.started_at : null;
  const Icon = j.kind === "download" ? Download : AudioLines;
  let detail = "";
  if (j.kind === "download" && j.stage === "downloading") {
    detail = `${bytes(j.bytes_done)}${j.bytes_total ? ` of ${bytes(j.bytes_total)}` : ""}${j.speed ? ` · ${speed(j.speed)}` : ""}`;
    if (j.speed && j.bytes_total) detail += ` · ${duration((j.bytes_total - j.bytes_done) / j.speed)} left`;
  } else if (j.kind === "transcribe" && j.stage === "transcribing" && j.speed) {
    detail = `${j.speed.toFixed(1)}× real time`;
  }
  return (
    <li className="rounded-lg border border-line bg-board-2 px-4 py-3">
      <div className="flex items-start gap-3">
        <Icon className={clsx("mt-0.5 size-4 shrink-0", running ? "text-accent" : "text-muted")} aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="truncate font-medium">{j.title || "Untitled"}</span>
            <span className="truncate text-[13px] text-muted">
              {j.course_name} · {VARIANT_LABEL[j.variant ?? ""] ?? j.variant}
            </span>
          </div>
          <div className="mt-2.5 max-w-[640px]">
            <StageRail job={j} />
          </div>
          <div className="tnum mt-2 flex flex-wrap gap-x-3 text-[13px]">
            <span className={clsx(j.status === "failed" ? "text-bad" : j.status === "completed" ? "text-ok" : "text-chalk-2")}>
              {j.status === "failed" ? "Failed" : j.status === "cancelled" ? "Cancelled" : STAGE_LABEL[j.stage] ?? j.stage}
              {running && j.progress != null ? ` ${(j.progress * 100).toFixed(j.progress < 0.1 ? 1 : 0)}%` : ""}
            </span>
            {detail && <span className="text-muted">{detail}</span>}
            {elapsed != null && <span className="text-muted">{duration(elapsed)} elapsed</span>}
            {j.attempts > 1 && <Badge tone="warn">attempt {j.attempts}</Badge>}
            {!running && <span className="text-muted">{ago(j.finished_at ?? j.created_at)}</span>}
          </div>
          {(j.error || j.message) && (
            <p className={clsx("mt-1 text-[13px]", j.error ? "text-bad" : "text-muted")}>{j.error || j.message}</p>
          )}
          {j.kind === "transcribe" && j.status === "completed" && j.output_path && (
            <Link to={`/transcripts/${j.output_path}`} className="mt-1 inline-block text-[13px] text-accent hover:underline">
              Open transcript
            </Link>
          )}
        </div>
        <div className="flex shrink-0 gap-1">
          {(j.status === "running" || j.status === "queued") && onCancel && (
            <Button size="sm" variant="ghost" icon={<X className="size-3.5" />} onClick={onCancel}>
              Cancel
            </Button>
          )}
          {(j.status === "failed" || j.status === "cancelled") && onRetry && (
            <Button size="sm" variant="ghost" icon={<RotateCcw className="size-3.5" />} onClick={onRetry}>
              Retry
            </Button>
          )}
          {j.status !== "running" && j.status !== "queued" && onRemove && (
            <button onClick={onRemove} className="rounded p-1.5 text-muted hover:bg-board-3 hover:text-chalk" aria-label="Remove from list">
              <Trash2 className="size-3.5" />
            </button>
          )}
        </div>
      </div>
    </li>
  );
}
