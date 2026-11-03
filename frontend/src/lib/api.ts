// Typed client for the local backend. Mutating requests carry X-Studio (CSRF guard).

export type SessionState = "anonymous" | "authenticated" | "expired" | "unknown";
export interface Session {
  state: SessionState;
  display_name: string | null;
  message?: string;
}

export interface LoginResult {
  state: SessionState | "needs_input" | "error";
  display_name?: string | null;
  flow_id?: string | null;
  message?: string;
  inputs?: { name: string; type: string; label: string }[];
  choices?: { name: string; label: string }[];
}

export interface CourseSummary {
  key: string;
  id: number;
  name: string;
  slug: string;
  year: number;
  term: string;
  semester_label: string;
  tum_online_identifier: string;
  vod_enabled: boolean;
  downloads_enabled: boolean;
  visibility: string;
  pinned: boolean;
  source: string[];
  recording_count: number | null;
  downloadable_count: number;
  downloaded_count?: number;
  transcribed_count?: number;
  favorite: boolean;
  priority: string | null;
}

export interface LocalState {
  downloaded: { variant: string; size: number }[];
  transcripts: { id: string; variant: string; model: string }[];
  jobs: { id: string; kind: string; status: string; stage: string; progress: number | null; variant: string }[];
}

export interface Recording {
  id: number;
  course_id: number;
  name: string;
  description: string;
  start: string | null;
  end: string | null;
  duration: number | null;
  recording: boolean;
  live_now: boolean;
  is_planned: boolean;
  variants: string[];
  downloadable_variants: string[];
  is_vod: boolean;
  local?: LocalState;
}

export interface Job {
  id: string;
  kind: "download" | "transcribe";
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  stage: string;
  stream_id: number;
  course_slug: string | null;
  course_name: string | null;
  year: number | null;
  term: string | null;
  title: string | null;
  variant: string | null;
  params: Record<string, unknown>;
  progress: number | null;
  bytes_done: number;
  bytes_total: number | null;
  speed: number | null;
  message: string | null;
  error: string | null;
  output_path: string | null;
  attempts: number;
  created_at: number;
  updated_at: number;
  started_at: number | null;
  finished_at: number | null;
}

export interface TranscriptSummary {
  id: string;
  stream_id: number;
  variant: string;
  course_name: string | null;
  title: string | null;
  engine: string;
  model: string;
  language: string | null;
  audio_seconds: number;
  processing_seconds: number;
  created_at: number;
  edited: number;
  segment_count?: number;
}
export interface Transcript extends TranscriptSummary {
  segments: { idx: number; start: number; end: number; text: string }[];
  media_available: boolean;
}

export interface ModelInfo {
  id: string;
  engine: string;
  repo: string;
  label: string;
  params: string;
  download_gb: number;
  memory_gb: number;
  platforms: string[];
  notes: string;
  compatible: boolean;
  installed: boolean;
  size_on_disk: number;
  download: { status: string; bytes_done: number; bytes_total: number | null; error?: string } | null;
  compute_types: string[];
  default: boolean;
  benchmarks: {
    engine: string;
    model: string;
    compute_type: string;
    device: string;
    n: number;
    rtf: number;
    peak_mem_mb: number;
    audio_seconds: number;
  }[];
}

export interface SystemInfo {
  version: string;
  hardware: {
    os: string;
    arch: string;
    cpu: string;
    cpu_cores: number;
    ram_gb: number;
    in_docker: boolean;
    apple_silicon: boolean;
    cuda_devices: { name: string; vram_gb: number }[];
    cuda_usable: boolean;
    engines: Record<string, boolean>;
  };
  recommendation: { model: string; compute_type: string; reason: string };
  settings: {
    model: string;
    compute_type: string;
    language: string;
    vocabulary: string;
    vad: boolean;
    beam_size: number;
    remote_url: string | null;
    auto_transcribe: boolean;
    download_variant: string;
  };
  paths: Record<string, string>;
  priority_courses: { id: string; label: string }[];
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code?: string,
  ) {
    super(message);
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} as Record<string, string> };
  if (method !== "GET") (init.headers as Record<string, string>)["X-Studio"] = "1";
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = res.statusText;
    let code: string | undefined;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
      code = j.code;
    } catch {
      /* not json */
    }
    throw new ApiError(detail, res.status, code);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(url: string) => request<T>("GET", url),
  post: <T>(url: string, body?: unknown) => request<T>("POST", url, body ?? {}),
  put: <T>(url: string, body?: unknown) => request<T>("PUT", url, body),
  patch: <T>(url: string, body?: unknown) => request<T>("PATCH", url, body),
  del: <T>(url: string) => request<T>("DELETE", url),
};
