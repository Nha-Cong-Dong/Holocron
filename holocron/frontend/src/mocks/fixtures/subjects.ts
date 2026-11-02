/**
 * Dữ liệu mẫu cho `GET /v1/subjects` theo spec §5.14 và §6.8 (M3 của kế hoạch UI).
 * Mã môn trung tính tự đặt, không gắn với môn thật; sắp theo `subject_code` như §6.8.
 */

import type { Subject } from "@/types/holocron";

export const SUBJECT_FIXTURES: Subject[] = [
  {
    subject_code: "MAU101",
    display_name: "Môn mẫu trắc nghiệm",
    exam_format: "mcq",
    version: 1,
    created_at: "2026-10-20T08:00:00Z",
    updated_at: "2026-10-20T08:00:00Z",
  },
  {
    subject_code: "MAU202",
    display_name: "Môn mẫu tự luận",
    exam_format: "essay",
    version: 2,
    created_at: "2026-10-21T09:30:00Z",
    updated_at: "2026-10-22T10:15:00Z",
  },
];
