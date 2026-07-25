/** Types mirroring backend/app/schemas.py (camelCase left as backend sends snake_case). */

export interface ClassGroup {
  id: number;
  name: string;
  school_year: string;
  student_count: number;
}

export interface Student {
  id: number;
  external_id: string;
  first_name: string;
  last_name: string;
  class_id: number | null;
  is_active: boolean;
  display_name: string;
  class_name: string | null;
}

export interface Task {
  id: number;
  external_id: string;
  title: string;
  subject: string;
  topic: string;
  description: string;
  expected_answer: string;
}

export interface FormTemplate {
  id: number;
  name: string;
  description: string;
  page_width_mm: number;
  page_height_mm: number;
  aspect_ratio: number;
  is_default: boolean;
}

export interface SessionStats {
  total: number;
  ok: number;
  duplicates: number;
  unidentified: number;
  low_quality: number;
  rescan_required: number;
  recognized: number;
  needs_review: number;
  blank: number;
  failed_ocr: number;
  pending_ocr: number;
  average_quality: number;
  sheets_per_minute: number;
}

export interface ScanSession {
  id: number;
  class_id: number | null;
  task_id: number | null;
  template_id: number | null;
  title: string;
  expected_sheet_count: number;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  class_name: string | null;
  task_title: string | null;
  stats: SessionStats;
}

export interface Recognition {
  id: number;
  recognized_text: string;
  provider: string;
  model_name: string;
  overall_confidence: number;
  line_results_json: unknown[] | null;
  warnings: string[] | null;
  status: string;
  error_message: string | null;
  attempts: number;
}

export interface ReviewDecision {
  id: number;
  teacher_text: string;
  decision: string;
  comment: string;
  reviewed_at: string;
}

export interface ScannedSheet {
  id: number;
  session_id: number;
  student_id: number | null;
  task_id: number | null;
  sheet_uid: string | null;
  qr_status: string;
  scan_status: string;
  quality_score: number;
  sharpness_score: number;
  glare_score: number;
  duplicate_of_id: number | null;
  warnings: string[] | null;
  sequence_number: number;
  created_at: string;
  student_name: string | null;
  student_external_id: string | null;
  class_name: string | null;
  task_title: string | null;
  recognition: Recognition | null;
  review: ReviewDecision | null;
  thumbnail_path: string | null;
  normalized_image_path: string | null;
  enhanced_image_path: string | null;
  answer_crop_path: string | null;
}

export interface Dashboard {
  last_session: ScanSession | null;
  sheets_today: number;
  needs_review: number;
  average_speed: number;
  hardware_events: { id: number; level: string; code: string; message: string; created_at: string }[];
  total_sessions: number;
  total_sheets: number;
  storage_bytes: number;
}

export interface CameraProfile {
  id: number;
  name: string;
  device_id: string;
  device_label: string;
  width: number;
  height: number;
  work_area_polygon: number[][] | null;
  qr_region: Record<string, number> | null;
  background_reference_path: string | null;
  template_id: number | null;
  is_active: boolean;
}

/** WebSocket messages from /api/ws/sessions/{id}/scan */
export interface ScanStateMessage {
  type: "state";
  state: string;
  action: string;
  prompt: string;
  color: string;
  hints: string[];
  blockingReasons: string[];
  progress: number;
  overlay: {
    quad: number[][] | null;
    workArea: number[][] | null;
    detection: Record<string, unknown>;
    metrics: Record<string, number>;
    frameIndex: number;
    analysisMs: number;
    candidates: number;
  };
  counters: Record<string, number>;
  speed: number;
}

export interface ScanResultMessage {
  type: "scan_result";
  result: {
    success: boolean;
    sheetId: number | null;
    reason: string;
    warnings: string[];
    studentLabel: string;
    sheetUid: string;
    quality: number;
    qrStatus: string;
    scanStatus: string;
    duplicateOf: number | null;
    thumbnail: string | null;
  };
  counters: Record<string, number>;
  speed: number;
  state: string;
  prompt: string;
}
