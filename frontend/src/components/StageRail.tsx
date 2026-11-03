import clsx from "clsx";
import type { Job } from "../lib/api";

// The pipeline a lecture travels through. Downloads and transcriptions are separate jobs,
// but the rail shows where each one sits in the whole journey.
const STEPS = [
  { key: "queue", label: "Queued", stages: ["queued"] },
  { key: "fetch", label: "Download", stages: ["authenticating", "preparing", "downloading", "verifying", "download_complete"] },
  { key: "audio", label: "Audio", stages: ["extracting_audio"] },
  { key: "stt", label: "Transcribe", stages: ["loading_model", "transcribing"] },
  { key: "export", label: "Export", stages: ["exporting", "complete"] },
];

export const STAGE_LABEL: Record<string, string> = {
  queued: "Queued",
  authenticating: "Authenticating",
  preparing: "Server preparing file",
  downloading: "Downloading",
  verifying: "Verifying",
  download_complete: "Download complete",
  extracting_audio: "Extracting audio",
  loading_model: "Loading model",
  transcribing: "Transcribing",
  exporting: "Exporting",
  complete: "Complete",
  failed: "Failed",
  cancelled: "Cancelled",
};

function stepIndex(job: Job): number {
  if (job.status === "completed") return job.kind === "download" ? 1 : 4;
  const i = STEPS.findIndex((s) => s.stages.includes(job.stage));
  if (i >= 0) return i;
  // failed/cancelled: stay where the job was; best guess from kind
  return job.kind === "download" ? 1 : 3;
}

export function StageRail({ job }: { job: Job }) {
  const current = stepIndex(job);
  const span = job.kind === "download" ? [0, 1] : [0, 4];
  const failed = job.status === "failed" || job.status === "cancelled";
  const done = job.status === "completed";
  return (
    <ol className="flex w-full items-center gap-1" aria-label="Pipeline stages">
      {STEPS.map((s, i) => {
        const inScope = i >= span[0] && i <= span[1] && !(job.kind === "transcribe" && i === 1);
        const state = !inScope
          ? "skip"
          : i < current || (done && i <= current)
            ? "done"
            : i === current
              ? failed
                ? "fail"
                : "active"
              : "todo";
        const progress = state === "active" && job.progress != null ? job.progress : null;
        return (
          <li key={s.key} className="flex min-w-0 flex-1 flex-col gap-1" aria-current={state === "active" ? "step" : undefined}>
            <div className={clsx("relative h-[5px] overflow-hidden rounded-full", state === "skip" ? "bg-transparent border border-dashed border-line" : "bg-board-3")}>
              {state === "done" && <div className="absolute inset-0 bg-ok/80" />}
              {state === "fail" && <div className="absolute inset-0 bg-bad" />}
              {state === "active" &&
                (progress != null && job.status === "running" ? (
                  <div className="absolute inset-y-0 left-0 bg-accent transition-[width] duration-500" style={{ width: `${progress * 100}%` }} />
                ) : (
                  <div className={clsx("absolute inset-0", job.status === "running" ? "indeterminate" : "bg-line-strong")} />
                ))}
            </div>
            <span
              className={clsx(
                "truncate text-[11px]",
                state === "active" ? "text-chalk" : state === "fail" ? "text-bad" : state === "skip" ? "text-muted/50" : "text-muted",
              )}
            >
              {s.label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
