import clsx from "clsx";
import { useCallback, useEffect, useState } from "react";
import { Check, Download, Trash2 } from "lucide-react";
import { api, type ModelInfo, type SystemInfo } from "../lib/api";
import { useStore } from "../lib/store";
import { Badge, Button, Input, PageHeader, ProgressBar, Select, Skeleton } from "../components/ui";
import { bytes } from "../lib/format";

const LANGS = [
  ["de", "German"],
  ["en", "English"],
  ["", "Detect automatically"],
];

export default function Models() {
  const { system, refreshSystem, toast, libraryVersion } = useStore();
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [form, setForm] = useState<SystemInfo["settings"] | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => api.get<{ models: ModelInfo[] }>("/api/models").then((r) => setModels(r.models)), []);
  useEffect(() => {
    load();
  }, [load, libraryVersion]);
  useEffect(() => {
    if (system) setForm(system.settings);
  }, [system]);
  // poll while a model download runs (progress comes from the backend's byte counter)
  useEffect(() => {
    if (!models?.some((m) => m.download?.status === "running")) return;
    const t = setInterval(load, 1000);
    return () => clearInterval(t);
  }, [models, load]);

  const save = async (values: Record<string, unknown>, quiet = false) => {
    setSaving(true);
    try {
      await api.put("/api/settings", { values });
      await refreshSystem();
      await load();
      if (!quiet) toast("Settings saved", "ok");
    } catch (e) {
      toast((e as Error).message, "bad");
    } finally {
      setSaving(false);
    }
  };

  const hw = system?.hardware;
  return (
    <div>
      <PageHeader title="Models & settings" sub="Transcription runs entirely on this computer. Nothing is sent to a cloud service." />

      {hw && system && (
        <section className="mb-8 rounded-lg border border-line bg-board-2 p-4">
          <h2 className="mb-2 font-semibold">This machine</h2>
          <dl className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-x-6 gap-y-2 text-[13px]">
            <Item k="Processor" v={hw.cpu} />
            <Item k="Memory" v={`${hw.ram_gb} GB`} />
            <Item k="GPU" v={hw.cuda_devices.length ? hw.cuda_devices.map((d) => `${d.name} (${d.vram_gb} GB)`).join(", ") : hw.apple_silicon ? "Apple Silicon (Metal)" : "none detected"} />
            <Item k="Acceleration" v={hw.cuda_usable ? "CUDA available" : hw.engines["mlx-whisper"] ? "Metal via MLX" : hw.in_docker && hw.apple_silicon ? "None inside Docker, use the host companion" : "CPU only"} />
          </dl>
          <p className="mt-3 text-[13px] text-chalk-2">
            Recommended: <span className="font-medium text-chalk">{system.recommendation.model}</span> ({system.recommendation.compute_type}). {system.recommendation.reason}
          </p>
        </section>
      )}

      <section className="mb-8">
        <h2 className="mb-3 text-[15px] font-semibold">Models</h2>
        {!models ? (
          <Skeleton className="h-64" />
        ) : (
          <div className="overflow-x-auto rounded-lg border border-line">
            <table className="w-full min-w-[860px] text-left">
              <thead className="bg-board-2 text-[12px] text-muted">
                <tr>
                  <th className="px-4 py-2 font-medium">Model</th>
                  <th className="px-3 py-2 text-right font-medium">Download</th>
                  <th className="px-3 py-2 text-right font-medium" title="Approximate peak memory in the default precision">Memory (approx.)</th>
                  <th className="px-3 py-2 font-medium">Runs on</th>
                  <th className="px-3 py-2 font-medium">Measured speed</th>
                  <th className="px-3 py-2 font-medium" />
                </tr>
              </thead>
              <tbody>
                {models.map((m) => (
                  <ModelRow key={m.id} m={m} onChanged={load} onDefault={() => save({ "stt.model": m.id, "stt.compute_type": m.compute_types[0] }, true)} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-2 text-[13px] text-muted">Speed appears after a model has transcribed something on this machine; it isn't estimated.</p>
      </section>

      {form && (
        <section className="mb-8 max-w-2xl">
          <h2 className="mb-3 text-[15px] font-semibold">Transcription</h2>
          <div className="flex flex-col gap-4 rounded-lg border border-line bg-board-2 p-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="flex flex-col gap-1">
                <span className="text-chalk-2">Language</span>
                <Select value={form.language ?? ""} onChange={(e) => setForm({ ...form, language: e.target.value })}>
                  {LANGS.map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </Select>
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-chalk-2">Precision</span>
                <Select value={form.compute_type} onChange={(e) => setForm({ ...form, compute_type: e.target.value })}>
                  {(models?.find((m) => m.id === form.model)?.compute_types ?? [form.compute_type]).map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </Select>
              </label>
            </div>
            <label className="flex flex-col gap-1">
              <span className="text-chalk-2">Technical vocabulary</span>
              <textarea
                value={form.vocabulary}
                onChange={(e) => setForm({ ...form, vocabulary: e.target.value })}
                rows={3}
                className="rounded-md border border-line-strong bg-board p-2.5 text-chalk focus:border-accent focus:outline-none"
              />
              <span className="text-[13px] text-muted">
                Comma-separated terms the model should expect (course names, English terms, symbols). Given as a hint only; it doesn't force words into the text.
              </span>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-chalk-2">STT companion URL (optional)</span>
              <Input value={form.remote_url ?? ""} onChange={(e) => setForm({ ...form, remote_url: e.target.value || null })} placeholder="http://host.docker.internal:8421" />
              <span className="text-[13px] text-muted">For Docker on a Mac: the native companion runs the model on the Apple GPU. Any OpenAI-compatible transcription server works.</span>
            </label>
            <div className="flex flex-col gap-2">
              <label className="flex items-center gap-2 text-chalk-2">
                <input type="checkbox" checked={form.vad} onChange={(e) => setForm({ ...form, vad: e.target.checked })} />
                Skip silence with voice activity detection (recommended)
              </label>
              <label className="flex items-center gap-2 text-chalk-2">
                <input type="checkbox" checked={form.auto_transcribe} onChange={(e) => setForm({ ...form, auto_transcribe: e.target.checked })} />
                Transcribe automatically after each download
              </label>
              <label className="flex items-start gap-2 text-chalk-2">
                <input type="checkbox" className="mt-0.5" checked={form.stream_capture} onChange={(e) => setForm({ ...form, stream_capture: e.target.checked })} />
                <span>
                  Record streams when the lecturer disabled downloads
                  <span className="block text-[13px] text-muted">
                    Saves the stream you can already watch on TUM-Live (yt-dlp, no re-encoding). For your own studying only: keep the files on this computer and don't share them.
                  </span>
                </span>
              </label>
            </div>
            <label className="flex max-w-xs flex-col gap-1">
              <span className="text-chalk-2">Default video variant</span>
              <Select value={form.download_variant} onChange={(e) => setForm({ ...form, download_variant: e.target.value })}>
                <option value="COMB">Combined (slides + camera)</option>
                <option value="PRES">Slides only</option>
                <option value="CAM">Camera only</option>
              </Select>
            </label>
            <div>
              <Button
                variant="primary"
                busy={saving}
                onClick={() =>
                  save({
                    "stt.language": form.language,
                    "stt.compute_type": form.compute_type,
                    "stt.vocabulary": form.vocabulary,
                    "stt.remote_url": form.remote_url,
                    "stt.vad": form.vad,
                    auto_transcribe: form.auto_transcribe,
                    "download.variant": form.download_variant,
                    "download.stream_capture": form.stream_capture,
                  })
                }
              >
                Save settings
              </Button>
            </div>
          </div>
        </section>
      )}
      {system && (
        <section className="max-w-2xl text-[13px] text-muted">
          <h2 className="mb-2 text-[15px] font-semibold text-chalk">Storage</h2>
          <dl className="grid grid-cols-[120px_1fr] gap-y-1">
            {Object.entries(system.paths).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="capitalize">{k}</dt>
                <dd className="truncate text-chalk-2" title={v}>
                  {v}
                </dd>
              </div>
            ))}
          </dl>
          <p className="mt-3">TUM Lecture Studio {system.version}</p>
        </section>
      )}
    </div>
  );
}

function Item({ k, v }: { k: string; v: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-muted">{k}</dt>
      <dd className="truncate" title={v}>
        {v}
      </dd>
    </div>
  );
}

function ModelRow({ m, onChanged, onDefault }: { m: ModelInfo; onChanged: () => void; onDefault: () => void }) {
  const { toast } = useStore();
  const running = m.download?.status === "running";
  const frac = running && m.download?.bytes_total ? m.download.bytes_done / m.download.bytes_total : null;
  const best = [...m.benchmarks].sort((a, b) => a.rtf - b.rtf)[0];
  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      onChanged();
    } catch (e) {
      toast((e as Error).message, "bad");
    }
  };
  return (
    <tr className={clsx("border-t border-line align-top", !m.compatible && "opacity-55")}>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2 font-medium">
          {m.label}
          {m.default && <Badge tone="accent">default</Badge>}
        </div>
        <div className="mt-0.5 max-w-[360px] text-[13px] text-muted">{m.notes}</div>
        {m.download?.status === "error" && <div className="mt-1 text-[13px] text-bad">{m.download.error}</div>}
      </td>
      <td className="tnum px-3 py-3 text-right whitespace-nowrap">{m.download_gb ? `${m.download_gb} GB` : "–"}</td>
      <td className="tnum px-3 py-3 text-right whitespace-nowrap">{m.memory_gb ? `~${m.memory_gb} GB` : "–"}</td>
      <td className="px-3 py-3">
        <div className="flex flex-wrap gap-1">
          {m.platforms.map((p) => (
            <Badge key={p}>{p === "apple" ? "Apple Silicon" : p === "cuda" ? "NVIDIA" : "CPU"}</Badge>
          ))}
        </div>
      </td>
      <td className="tnum px-3 py-3 text-[13px] whitespace-nowrap">
        {best ? (
          <span title={`${best.n} run(s), ${Math.round(best.audio_seconds / 60)} min audio, ${best.device}`}>
            {(1 / best.rtf).toFixed(1)}× real time
            <span className="block text-muted">
              {best.compute_type}, peak {bytes(best.peak_mem_mb * 1e6)}
            </span>
          </span>
        ) : (
          <span className="text-muted">not measured</span>
        )}
      </td>
      <td className="px-3 py-3">
        <div className="flex items-center justify-end gap-1.5">
          {running ? (
            <div className="w-36">
              <ProgressBar value={frac} />
              <div className="tnum mt-1 text-[12px] text-muted">
                {bytes(m.download?.bytes_done)} of {bytes(m.download?.bytes_total)}
              </div>
            </div>
          ) : m.installed ? (
            <>
              {!m.default && m.compatible && (
                <Button size="sm" icon={<Check className="size-3.5" />} onClick={onDefault}>
                  Use
                </Button>
              )}
              {m.engine !== "remote" && (
                <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => act(() => api.del(`/api/models/${m.id}`))} aria-label={`Remove ${m.label}`} />
              )}
            </>
          ) : (
            <Button size="sm" disabled={!m.compatible} icon={<Download className="size-3.5" />} onClick={() => act(() => api.post(`/api/models/${m.id}/download`))}>
              Download
            </Button>
          )}
        </div>
      </td>
    </tr>
  );
}
