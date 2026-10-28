/**
 * Trạng thái của trang môn `/subject` tính từ mã môn trên địa chỉ và danh sách môn đã tải (`SubjectsState`), cùng
 * breadcrumb của thanh trên. Logic thuần để component chỉ việc render theo `kind`.
 */

import type { UiError } from "@/lib/errors";
import { type SubjectsState, selectedSubject } from "@/lib/subjects";
import type { Subject } from "@/types/holocron";

export type SubjectView =
  /** Địa chỉ thiếu mã môn hoặc mã sai mẫu. */
  | { kind: "invalid_code" }
  /** Đang tải danh sách môn: tiêu đề tạm là mã môn. */
  | { kind: "loading"; code: string }
  /** Không tải được danh sách môn (backend chưa có danh sách, hoặc lỗi): vẫn mở được môn theo mã. */
  | { kind: "list_unavailable"; code: string; error: UiError }
  /** Danh sách đã tải nhưng không có môn này. */
  | { kind: "not_found"; code: string }
  | { kind: "ready"; code: string; subject: Subject };

/** `code` là mã đã kiểm mẫu (`parseSubjectLocation`); `null` ⇒ `invalid_code`. */
export function subjectViewOf(code: string | null, subjects: SubjectsState): SubjectView {
  if (code === null) return { kind: "invalid_code" };
  if (subjects.kind === "loading") return { kind: "loading", code };
  if (subjects.kind === "error") return { kind: "list_unavailable", code, error: subjects.error };
  const subject = selectedSubject(subjects, code);
  return subject === null ? { kind: "not_found", code } : { kind: "ready", code, subject };
}

/** Các phần (Tài liệu, Khái niệm, Ôn tập) chỉ hiện khi biết mã môn và môn không chắc chắn vắng mặt. */
export function canShowPanes(view: SubjectView): view is Exclude<SubjectView, { kind: "invalid_code" | "not_found" }> {
  return view.kind === "loading" || view.kind === "list_unavailable" || view.kind === "ready";
}

/** Tên hiển thị của môn; chưa tải được tên ⇒ mã môn; không có mã ⇒ `null`. */
export function subjectTitle(view: SubjectView): string | null {
  if (view.kind === "invalid_code") return null;
  if (view.kind === "ready") return view.subject.display_name.trim() || view.code;
  return view.code;
}

export interface BreadcrumbItem {
  label: string;
  /** `null` ⇒ không phải liên kết (mục cuối, trang đang mở). */
  href: string | null;
}

export const HOME_CRUMB: BreadcrumbItem = { label: "Môn học", href: "/" };

/** Nhãn mục cuối khi trang môn chưa biết môn nào. */
export const UNKNOWN_SUBJECT_LABEL = "Chưa rõ môn";

/** Breadcrumb của trang môn: "Môn học" (về trang chủ) › tên môn (trang đang mở). */
export function subjectBreadcrumb(view: SubjectView): BreadcrumbItem[] {
  return [HOME_CRUMB, { label: subjectTitle(view) ?? UNKNOWN_SUBJECT_LABEL, href: null }];
}
