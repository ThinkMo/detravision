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
  rtsp_configured: boolean;
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
  snapshot_url: string | null;
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
  runtime: "pt" | "onnx-fp32" | "tensorrt-fp16";
  classes: string[];
  map50: number | null;
  is_active: boolean;
  created_at: string;
  training_job_id?: number | null;
  source?: string | null;
  base_model?: string | null;
  imgsz?: number | null;
  metrics?: TrainingMetrics | null;
  deployment_runtime?: string | null;
}

export type TrainingStatus = "queued" | "running" | "evaluating" | "stopping" | "stopped" | "completed" | "failed" | "interrupted" | "cancelled";

export interface TrainingMetricSet {
  precision: number | null;
  recall: number | null;
  map50: number | null;
  map50_95: number | null;
}

export interface TrainingMetrics extends TrainingMetricSet {
  classes?: Record<string, TrainingMetricSet>;
}

export type TrainingProfile = "balanced" | "small_object" | "high_recall" | "stable_finetune" | "custom" | "legacy";
export type TrainingAdvancedValue = string | number | boolean;
export type TrainingAdvancedConfig = Record<string, TrainingAdvancedValue>;
export type TrainingAcceptanceMetric = "precision" | "recall" | "map50" | "map50_95";
export type TrainingAcceptanceThresholds = Partial<Record<TrainingAcceptanceMetric, number | null>>;

export interface TrainingAcceptanceConfig {
  schema_version: number;
  overall: TrainingAcceptanceThresholds;
  classes: Record<string, TrainingAcceptanceThresholds>;
}

export interface TrainingProfileCatalogItem {
  id: Exclude<TrainingProfile, "legacy">;
  label: string;
  description: string;
  recommended: { imgsz: 640 | 960; epochs: number; patience: number };
  overrides: TrainingAdvancedConfig;
}

export interface TrainingAdvancedFieldDescriptor {
  type: "number" | "integer" | "boolean" | "string";
  label: string;
  help: string;
  default: TrainingAdvancedValue;
  min?: number;
  max?: number;
  choices?: TrainingAdvancedValue[];
}

export interface TrainingAcceptanceTemplate {
  id: string;
  label: string;
  required_classes: string[];
  config: TrainingAcceptanceConfig;
}

export interface TrainingAcceptanceCheck {
  scope: "overall" | "class";
  class_name: string | null;
  metric: TrainingAcceptanceMetric;
  threshold: number;
  actual: number | null;
  passed: boolean;
  reason: "threshold_met" | "threshold_not_met" | "metric_missing" | "metric_invalid" | string;
}

export interface TrainingCatalog {
  scheduler_state?: "healthy" | "unavailable" | "disabled" | "unknown";
  models: Array<{ id: string; series: string; size: string; available: boolean }>;
  executors: Array<{ id: "local" | "remote"; enabled: boolean }>;
  dataset: { id: string; available: boolean; has_test_split: boolean; classes: string[] };
  datasets: TrainingDataset[];
  default_dataset_key: string | null;
  config_schema_version?: number;
  profile_version?: number;
  training_profiles?: TrainingProfileCatalogItem[];
  advanced_config_schema?: {
    groups: Array<{ id: string; label: string; fields: string[] }>;
    fields: Record<string, TrainingAdvancedFieldDescriptor>;
  };
  acceptance_schema?: {
    metrics: TrainingAcceptanceMetric[];
    templates: TrainingAcceptanceTemplate[];
  };
}

export interface TrainingDataset {
  id: number;
  key: string;
  name: string;
  root_alias: string;
  directory: string;
  train_split: string;
  val_split: string;
  test_split: string;
  classes: string[];
  enabled: boolean;
  available: boolean;
  reason: string | null;
  version: number;
  status: "editing" | "published";
  parent_id: number | null;
  root_dataset_id: number | null;
  revision: number;
  published_at: string | null;
  manifest_digest: string | null;
}

export interface TrainingDatasetImage {
  id: number;
  dataset_id: number;
  split: "train" | "val" | "test";
  image_key: string;
  display_name: string;
  width: number;
  height: number;
  review_status: "pending_annotation" | "annotated" | "confirmed_empty";
  validation_code: string | null;
  revision: number;
}

export interface AnnotationBoxInput { class_id: number; x_center: number; y_center: number; width: number; height: number; }
export interface TrainingDatasetImageDetail extends TrainingDatasetImage { boxes: AnnotationBoxInput[]; }
export interface TrainingDatasetImagePage { items: TrainingDatasetImage[]; total: number; page: number; page_size: number; }
export interface DatasetValidationIssue { code: string; severity: "error" | "warning"; split: string | null; image_id: number | null; message: string; }
export interface DatasetValidationReport { errors: DatasetValidationIssue[]; warnings: DatasetValidationIssue[]; counts: Record<string, unknown>; digest: string | null; }

export interface TrainingDatasetInput {
  name: string;
  root_alias: string;
  directory: string;
  train_split: string;
  val_split: string;
  test_split: string;
  classes: string[];
}

export interface TrainingJob {
  id: number;
  created_by?: number;
  executor: "local";
  base_model: string;
  imgsz: 640 | 960;
  epochs: number;
  batch: "1" | "2" | "4" | "8" | "auto";
  device: "auto" | "cpu" | "0";
  patience: number;
  dataset_key?: string;
  dataset_classes?: string[];
  dataset_manifest_digest?: string | null;
  config_schema_version?: number;
  training_profile?: TrainingProfile;
  status: TrainingStatus;
  current_epoch: number;
  total_epochs: number;
  progress?: number;
  runtime_state?: "waiting" | "healthy" | "heartbeat_delayed" | "disconnected" | TrainingStatus;
  recovery_hint?: "wait_for_recovery" | "check_worker" | "resume_from_last" | null;
  stop_requested?: boolean;
  has_best: boolean;
  has_last: boolean;
  precision?: number | null;
  recall?: number | null;
  map50?: number | null;
  map50_95?: number | null;
  no_helmet_precision?: number | null;
  no_helmet_recall?: number | null;
  accepted?: boolean | null;
  metrics?: TrainingMetrics | null;
  error_code?: string | null;
  error_message?: string | null;
  resume_from_id?: number | null;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface TrainingJobDetail extends TrainingJob {
  recent_log?: string;
  log?: string;
  confusion_matrix_url?: string | null;
  log_url?: string | null;
  registered_model_id?: number | null;
  advanced_config?: TrainingAdvancedConfig | null;
  acceptance_config?: TrainingAcceptanceConfig | null;
  acceptance_checks?: TrainingAcceptanceCheck[] | null;
  results_curve_url?: string | null;
}

export interface TrainingJobPage {
  items: TrainingJob[];
  total: number;
  page: number;
  page_size: number;
  scheduler_state?: "healthy" | "unavailable" | "disabled" | "unknown";
}

export interface TrainingJobCreate {
  dataset_key: string;
  executor: "local";
  base_model: string;
  imgsz: 640 | 960;
  epochs: number;
  batch: "1" | "2" | "4" | "8" | "auto";
  device: "auto" | "cpu" | "0";
  patience: number;
  mode: "basic" | "advanced";
  profile: Exclude<TrainingProfile, "legacy">;
  advanced_config: Partial<TrainingAdvancedConfig>;
  acceptance_config: TrainingAcceptanceConfig;
  acceptance_template?: string | null;
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

export interface UserRecord extends CurrentUser {
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export type BatchJobStatus = "queued" | "processing" | "completed" | "partial_failed" | "failed";
export type BatchImageStatus = "queued" | "processing" | "completed" | "failed";

export interface BatchDetection {
  label: string;
  confidence: number;
  bbox: number[];
}

export interface BatchJob {
  id: number;
  user_id: number;
  status: BatchJobStatus;
  image_total: number;
  success_count: number;
  failure_count: number;
  expires_at: string;
  created_at: string;
  updated_at: string;
}

export interface BatchImageResult {
  id: number;
  display_name: string;
  status: BatchImageStatus;
  error_code: string | null;
  error_message: string | null;
  model_version_id: number | null;
  detections: BatchDetection[];
  preview_url: string;
  result_url: string;
  created_at: string;
  updated_at: string;
}

export interface BatchJobDetail extends BatchJob {
  images: BatchImageResult[];
}

export interface BatchJobPage {
  items: BatchJob[];
  total: number;
  page: number;
  page_size: number;
}

export interface ApiErrorBody {
  code?: string;
  message?: string;
  detail?: string;
  field_errors?: Record<string, string[]>;
}
