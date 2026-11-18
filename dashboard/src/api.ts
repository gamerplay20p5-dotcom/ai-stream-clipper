export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["X-ASC-CSRF"] = "1";
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* ignore */
    }
    if (res.status === 401 && !path.endsWith("/login")) window.dispatchEvent(new Event("asc:unauthorized"));
    throw new ApiError(res.status, detail);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const api = {
  get: <T,>(p: string) => request<T>("GET", p),
  post: <T,>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  put: <T,>(p: string, b: unknown) => request<T>("PUT", p, b),
  patch: <T,>(p: string, b: unknown) => request<T>("PATCH", p, b),
  del: <T,>(p: string) => request<T>("DELETE", p),
};

export type Session = {
  id: number;
  streamer_id: number;
  status: "live" | "ended" | "failed";
  title: string;
  started_at: string;
  ended_at: string | null;
  end_reason: string;
  covered_until: number;
  analyzed_until: number;
  scored_until: number;
  segment_count: number;
};

export type Streamer = {
  id: number;
  platform: "twitch" | "youtube" | "file";
  channel: string;
  url: string;
  display_name: string;
  enabled: boolean;
  status: "unknown" | "offline" | "live" | "error";
  stream_title: string;
  preferences: Record<string, unknown>;
  last_checked_at: string | null;
  last_live_at: string | null;
  last_error: string;
  live_session: Session | null;
  clip_count: number;
  sessions?: Session[];
};

export type Breakdown = Record<string, number | null>;

export type Clip = {
  id: number;
  session_id: number;
  streamer_id: number;
  streamer: { display_name: string; platform: string; channel: string } | null;
  status: "candidate" | "analyzing" | "rendering" | "ready" | "approved" | "rejected" | "failed";
  start: number;
  end: number;
  peak: number;
  duration: number;
  score: number;
  score_breakdown: Breakdown;
  reasons: string[];
  kinds: string[];
  transcript: string;
  title: string;
  description: string;
  title_suggestions: string[];
  hashtags: string[];
  caption_style: string;
  layout: string;
  vision_state: string;
  vision: Record<string, unknown> | null;
  version: number;
  has_video: boolean;
  has_thumbnail: boolean;
  file_size: number;
  render_seconds: number;
  error: string;
  created_at: string;
  updated_at: string;
};

export type Stats = {
  streamers_total: number;
  streamers_enabled: number;
  live_sessions: number;
  hours_analyzed: number;
  clips_total: number;
  clips_by_status: Record<string, number>;
  clips_approved: number;
  clips_last_24h: number[];
  pending_jobs: number;
  dead_jobs: number;
  workers_online: number;
  workers_total: number;
  top_clips: Clip[];
};

export type Job = {
  id: number;
  type: string;
  status: string;
  attempts: number;
  max_attempts: number;
  requires_gpu: boolean;
  worker_id: string;
  last_error: string;
  payload: Record<string, unknown>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type WorkerInfo = {
  id: string;
  host: string;
  host_label: string;
  roles: string[];
  capabilities: Record<string, unknown>;
  status: "online" | "offline";
  metrics: Record<string, number>;
  version: string;
  last_heartbeat_at: string;
};

export type Host = {
  label: string;
  hostname: string;
  ip: string;
  role: string;
  gpu: string | null;
  workers: WorkerInfo[];
  metrics: Record<string, number> | null;
};

export type Infra = {
  hosts: Host[];
  queues: Record<string, Record<string, number>>;
  processing: Record<string, { avg_s: number; count: number; max_s: number }>;
  failures: Job[];
  storage: { used_bytes: number; limit_bytes: number; disk_free_bytes: number; disk_total_bytes: number };
};

export type AISettings = {
  sensitivity: number;
  min_score: number;
  language: string;
  whisper_model: string;
  min_clip_seconds: number;
  max_clip_seconds: number;
  pre_context_seconds: number;
  post_context_seconds: number;
  max_clips_per_hour: number;
  dedup_overlap: number;
  vision_enabled: boolean;
  vision_provider: "heuristic" | "openvino_vlm";
  vision_frame_interval: number;
  vision_timeout_seconds: number;
  export_preset: string;
  export_crf: number;
  export_fps: number;
  caption_style: string;
  default_layout: string;
  show_title_overlay: boolean;
  max_parallel_renders: number;
  max_parallel_audio: number;
  retention_rejected_days: number;
  retention_unreviewed_days: number;
  retention_approved_days: number;
  auto_publish: false;
  weights: Record<string, number>;
};
