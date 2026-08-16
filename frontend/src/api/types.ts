export interface SiteSettings {
  site_name: string;
  logo_url: string | null;
}

export interface CurrentUser {
  id: number;
  username: string;
  role: "admin" | "user";
  must_change_password: boolean;
}

export interface Camera {
  id: number;
  name: string;
  rtsp_url: string;
  enabled: boolean;
  sample_fps: number;
  roi: number[][];
  detect_classes: string[];
  alarm_group_id: number | null;
  created_at: string;
}

export interface Event {
  id: number;
  camera_id: number;
  event_type: string;
  confidence: number;
  bbox: number[];
  snapshot_path: string | null;
  status: "new" | "ack" | "closed";
  ack_by: number | null;
  ack_user_id: number | null;
  created_at: string;
}

export interface RuntimeStatus {
  enabled: boolean;
  active_cameras: number[];
  queued_frames: number;
  dropped_frames: number;
  processed_frames: number;
  created_events: number;
  capture_errors: Record<string, string>;
}

export interface DashboardSummary {
  camera_total: number;
  camera_active: number;
  events_today: number;
  events_new: number;
  recent_events: Event[];
  runtime: RuntimeStatus;
}

export interface Person {
  id: number;
  name: string;
  phone: string | null;
  feishu_id: string | null;
  dept: string | null;
  created_at: string;
}

export interface AlarmGroup {
  id: number;
  name: string;
  created_at: string;
  members: Person[];
}

export interface ModelVersion {
  id: number;
  name: string;
  file_path: string;
  runtime: "pt" | "onnx-fp32" | "tensorrt-fp16";
  classes: string[];
  map50: number | null;
  is_active: boolean;
  created_at: string;
}

export interface AuditLog {
  id: number;
  actor_id: number | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  outcome: string;
  details: string | null;
  ip: string | null;
  created_at: string;
}

export interface ApiErrorBody {
  code?: string;
  message?: string;
  detail?: string;
  field_errors?: Record<string, string[]>;
}
