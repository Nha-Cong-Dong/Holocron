/**
 * Trạng thái của trang môn `/subject` tính từ mã môn trên địa chỉ và danh sách môn đã tải (`SubjectsState`), cùng
 * breadcrumb của thanh trên. Logic thuần để component chỉ việc render theo `kind`.
 */

import type { UiError } from "@/lib/errors";
import { crumbTitleForPath } from "@/lib/navigation";
import { type PracticeSessionKind, parsePracticeSession, parseSubjectLocation, subjectHref } from "@/lib/subject-route";
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

/**
 * Bầu trời kiến thức chỉ hiện khi môn chắc chắn có trong danh sách môn (`ready`): mã sai, môn không có, danh sách
 * chưa tải xong hay không tải được thì không vẽ bầu trời và không gọi `GET /v1/vault/concept-pairs`.
 */
export function canShowSky(view: SubjectView): view is Extract<SubjectView, { kind: "ready" }> {
  return view.kind === "ready";
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
  /**
   * `true` ⇒ liên kết thay mục lịch sử hiện tại (`router.replace`) thay vì thêm mục mới: tên môn khi đang làm bài, để
   * Back sau đó rời trang môn chứ không quay lại đề đang làm. Vắng ⇒ thêm mục lịch sử như liên kết thường.
   */
  replace?: true;
}

export const HOME_CRUMB: BreadcrumbItem = { label: "Môn học", href: "/" };

/** Nhãn mục cuối khi trang môn chưa biết môn nào. */
export const UNKNOWN_SUBJECT_LABEL = "Chưa rõ môn";

/** Nhãn mục cuối của breadcrumb khi đang làm đề hoặc xem kết quả trong tab Ôn tập. */
export const SESSION_CRUMB_LABEL: Record<PracticeSessionKind, string> = { quiz: "Đang ôn", result: "Kết quả" };

/**
 * Breadcrumb của trang môn: "Môn học" (về trang chủ) › tên môn (trang đang mở). Đang làm đề hoặc xem kết quả
 * (`session` khác `null`, cần biết mã môn): tên môn thành liên kết về tab Ôn tập (không kèm `session`, thay mục lịch
 * sử hiện tại chứ không thêm mục mới), thêm mục "Đang ôn" hoặc "Kết quả".
 */
export function subjectBreadcrumb(view: SubjectView, session: PracticeSessionKind | null = null): BreadcrumbItem[] {
  const label = subjectTitle(view) ?? UNKNOWN_SUBJECT_LABEL;
  if (session === null || view.kind === "invalid_code") return [HOME_CRUMB, { label, href: null }];
  return [
    HOME_CRUMB,
    { label, href: subjectHref(view.code, "practice"), replace: true },
    { label: SESSION_CRUMB_LABEL[session], href: null },
  ];
}

/**
 * Breadcrumb của thanh trên theo đường dẫn: trang chủ không có breadcrumb; trang môn dùng `subjectBreadcrumb` với
 * `subjectCode` là mã môn đã kiểm mẫu lấy từ địa chỉ (`null` khi thiếu hoặc sai) và `session` là phần làm bài của tab
 * Ôn tập (mặc định không có); trang khác là "Môn học" › tiêu đề breadcrumb của trang (`crumbTitleForPath`).
 */
export function breadcrumbForPath(
  pathname: string,
  subjectCode: string | null,
  subjects: SubjectsState,
  session: PracticeSessionKind | null = null,
): BreadcrumbItem[] {
  const path = pathname.replace(/\/+$/, "") || "/";
  if (path === "/") return [];
  if (path === "/subject") return subjectBreadcrumb(subjectViewOf(subjectCode, subjects), session);
  return [HOME_CRUMB, { label: crumbTitleForPath(path), href: null }];
}

interface SearchParamsLike {
  get: (name: string) => string | null;
}

/**
 * Breadcrumb suy hoàn toàn từ địa chỉ hiện tại (đường dẫn + query) và danh sách môn: môn của trang `/subject` lấy từ
 * `?code=` chứ không từ state của khung, nên mã sai không hiện môn cũ và đổi môn không nháy tên môn trước; phần làm
 * bài lấy từ `?session=` của tab Ôn tập.
 */
export function breadcrumbForLocation(
  pathname: string,
  params: SearchParamsLike,
  subjects: SubjectsState,
): BreadcrumbItem[] {
  return breadcrumbForPath(pathname, parseSubjectLocation(params).code, subjects, parsePracticeSession(params));
}
