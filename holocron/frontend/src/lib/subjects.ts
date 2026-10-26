/**
 * Kiểm dạng danh sách môn ở ranh giới (`GET /v1/subjects`, spec §6.8: `200 list[Subject]`). Phần tử null hoặc sai
 * dạng bị bỏ, phần tử đúng giữ nguyên thứ tự backend trả; thân không phải mảng thì không nhận.
 */

import type { UiError } from "@/lib/errors";
import { SUBJECT_CODE_PATTERN } from "@/lib/limits";
import type { ExamFormat, Subject } from "@/types/contract-pending";

const EXAM_FORMATS: readonly ExamFormat[] = ["mcq", "essay", "mixed"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function examFormatOf(value: unknown): ExamFormat | null {
  return EXAM_FORMATS.find((format) => format === value) ?? null;
}

/** Một phần tử `Subject` (§5.14); `null` khi thiếu field bắt buộc hay sai mẫu. */
export function parseSubject(value: unknown): Subject | null {
  if (!isRecord(value)) return null;
  const { subject_code, display_name, version, created_at, updated_at } = value;
  const examFormat = examFormatOf(value.exam_format);
  if (typeof subject_code !== "string" || !SUBJECT_CODE_PATTERN.test(subject_code)) return null;
  if (typeof display_name !== "string" || display_name.trim() === "") return null;
  if (examFormat === null) return null;
  if (typeof version !== "number" || !Number.isInteger(version) || version < 1) return null;
  if (typeof created_at !== "string" || typeof updated_at !== "string") return null;
  return { subject_code, display_name, exam_format: examFormat, version, created_at, updated_at };
}

/** Danh sách môn đã kiểm dạng; `null` khi thân không phải mảng. Mã môn trùng thì giữ phần tử đầu tiên. */
export function parseSubjectList(value: unknown): Subject[] | null {
  if (!Array.isArray(value)) return null;
  const seen = new Set<string>();
  const subjects: Subject[] = [];
  for (const raw of value) {
    const subject = parseSubject(raw);
    if (subject === null || seen.has(subject.subject_code)) continue;
    seen.add(subject.subject_code);
    subjects.push(subject);
  }
  return subjects;
}

/** Trạng thái tải danh sách môn, dùng chung cho thanh bên và các màn cần `exam_format`. */
export type SubjectsState =
  | { kind: "loading" }
  | { kind: "error"; error: UiError }
  | { kind: "ready"; subjects: Subject[] };

/** Môn đang chọn, khi danh sách đã tải; `null` khi chưa chọn, chưa tải xong hay mã không còn trong danh sách. */
export function selectedSubject(state: SubjectsState, subjectCode: string | null): Subject | null {
  if (state.kind !== "ready" || subjectCode === null) return null;
  return state.subjects.find((subject) => subject.subject_code === subjectCode) ?? null;
}
