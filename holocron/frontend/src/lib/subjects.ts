/**
 * Kiểm dạng danh sách môn ở ranh giới (`GET /v1/subjects`, spec §6.8: `200 list[Subject]`). Phần tử null hoặc sai
 * dạng bị bỏ, phần tử đúng giữ nguyên thứ tự backend trả; thân không phải mảng thì không nhận.
 */

import type { UiError } from "@/lib/errors";
import { SUBJECT_CODE_PATTERN, SUBJECT_DISPLAY_NAME_MAX_CHARS } from "@/lib/limits";
import type { ExamFormat, Subject, SubjectCreate } from "@/types/holocron";

/** Giá trị `exam_format` theo thứ tự khai báo (§5.14). */
export const EXAM_FORMATS: readonly ExamFormat[] = ["mcq", "essay", "mixed"];

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

/** Nhãn lựa chọn hình thức thi khi tạo môn (§5.14); mặc định của backend khi bỏ trống là `mixed`. */
export const EXAM_FORMAT_OPTION_VI: Record<ExamFormat, string> = {
  mcq: "Trắc nghiệm",
  essay: "Tự luận",
  mixed: "Kết hợp trắc nghiệm và tự luận",
};

export interface SubjectFormInput {
  subjectCode: string;
  displayName: string;
  examFormat: ExamFormat;
}

export type SubjectFormCheck =
  | { ok: true; payload: SubjectCreate }
  | { ok: false; errors: Partial<Record<"code" | "name", string>> };

/**
 * Kiểm form tạo môn và dựng body `POST /v1/subjects` (§6.8). Mã môn viết hoa trước khi kiểm (người dùng hay gõ chữ
 * thường); tên hiển thị bỏ trống thì không gửi (backend lấy mặc định); `exam_format` luôn gửi tường minh.
 */
export function checkSubjectForm(input: SubjectFormInput): SubjectFormCheck {
  const code = input.subjectCode.trim().toUpperCase();
  const name = input.displayName.trim();
  const errors: Partial<Record<"code" | "name", string>> = {};
  if (!SUBJECT_CODE_PATTERN.test(code)) {
    errors.code = "Mã môn gồm 2–6 chữ cái in hoa rồi 2–4 chữ số, ví dụ MAU101.";
  }
  if ([...name].length > SUBJECT_DISPLAY_NAME_MAX_CHARS) {
    errors.name = `Tên môn tối đa ${SUBJECT_DISPLAY_NAME_MAX_CHARS} ký tự.`;
  }
  if (errors.code !== undefined || errors.name !== undefined) return { ok: false, errors };
  const payload: SubjectCreate = { subject_code: code, exam_format: input.examFormat };
  if (name !== "") payload.display_name = name;
  return { ok: true, payload };
}
