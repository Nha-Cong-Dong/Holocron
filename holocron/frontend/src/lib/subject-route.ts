/**
 * Địa chỉ của trang môn `/subject?code=<mã môn>&tab=<phần>` (design-handoff/README.md màn 3). Mã môn đi qua query
 * string, không dùng route động, để giữ được static export. Tham số riêng của từng phần (chương, trạng thái, đoạn đang
 * mở của phần Khái niệm) nối sau `tab`.
 */

import { SUBJECT_CODE_PATTERN } from "@/lib/limits";
import type { PairsLocation } from "@/lib/pairs";
import { parseSubjectTab, type SubjectTab } from "@/lib/tabs";
import { parseVaultLocation, type VaultLocation } from "@/lib/vault";

export const SUBJECT_PATH = "/subject";

/** Tham số dành riêng cho trang môn; tham số của phần không được ghi đè chúng. */
const RESERVED_PARAMS: ReadonlySet<string> = new Set(["code", "tab"]);

interface SearchParamsLike {
  get: (name: string) => string | null;
}

export interface SubjectLocation {
  /** Mã môn đúng mẫu; `null` khi thiếu hoặc sai mẫu. */
  code: string | null;
  tab: SubjectTab;
}

/** Mã môn đúng mẫu `SUBJECT_CODE_PATTERN` (chữ hoa rồi chữ số, ví dụ MAU101). */
export function isSubjectCode(value: string): boolean {
  return SUBJECT_CODE_PATTERN.test(value);
}

/** Đọc `?code=&tab=`; mã sai mẫu bị bỏ (coi như không có), tab lạ ⇒ Tài liệu. Không ném lỗi. */
export function parseSubjectLocation(params: SearchParamsLike): SubjectLocation {
  const code = params.get("code")?.trim() ?? "";
  return { code: isSubjectCode(code) ? code : null, tab: parseSubjectTab(params.get("tab")) };
}

export type SubjectExtraParams = Readonly<Record<string, string | null | undefined>>;

/**
 * Dựng địa chỉ trang môn. Mọi giá trị được `encodeURIComponent`; tham số rỗng hoặc `null` bị bỏ; `code`/`tab` trong
 * `extra` bị bỏ qua để không ghi đè môn và phần đang mở.
 */
export function subjectHref(code: string, tab: SubjectTab, extra: SubjectExtraParams = {}): string {
  const parts = [`code=${encodeURIComponent(code)}`, `tab=${encodeURIComponent(tab)}`];
  for (const [name, value] of Object.entries(extra)) {
    if (RESERVED_PARAMS.has(name) || value === null || value === undefined || value === "") continue;
    parts.push(`${encodeURIComponent(name)}=${encodeURIComponent(value)}`);
  }
  return `${SUBJECT_PATH}?${parts.join("&")}`;
}

/**
 * Địa chỉ phần Khái niệm của một môn, giữ bộ lọc và đoạn đang mở. Môn luôn là `code` của trang; `subject` trong
 * `location` bị bỏ qua.
 */
export function subjectConceptsHref(code: string, location: Partial<PairsLocation>): string {
  return subjectHref(code, "concepts", {
    chapter: location.chapter,
    status: location.status,
    chunk: location.chunk,
  });
}

/** Tài liệu và trang đang xem lại trong phần Tài liệu (`?doc=&page=`); không có ⇒ danh sách tài liệu. */
export interface SubjectDocumentLocation {
  doc: string | null;
  page: number | null;
}

/** Đọc `?doc=&page=` của phần Tài liệu theo đúng mẫu của màn `/vault`; giá trị sai mẫu bị bỏ, không ném lỗi. */
export function parseSubjectDocumentLocation(params: SearchParamsLike): SubjectDocumentLocation {
  const location = parseVaultLocation(params);
  return { doc: location.doc, page: location.doc === null ? null : location.page };
}

/**
 * Địa chỉ phần Tài liệu của một môn: danh sách tài liệu, hoặc xem lại một trang khi có `doc` (kèm `page`). Môn luôn
 * là `code` của trang; `subject`/`tab` trong `location` bị bỏ qua.
 */
export function subjectDocumentsHref(code: string, location: Partial<VaultLocation>): string {
  const doc = location.doc ?? null;
  const page = doc === null || location.page === undefined || location.page === null ? null : String(location.page);
  return subjectHref(code, "documents", { doc, page });
}

// ---------------------------------------------------------------------------- phiên làm bài trong tab Ôn tập

/**
 * Phần đang mở trong tab Ôn tập khi đang làm đề (`&session=quiz`) hoặc xem kết quả (`&session=result`). Chỉ phản chiếu
 * state của màn Ôn tập (nguồn chân lý nằm trong bộ nhớ), ghi bằng `router.replace` nên không thêm mục lịch sử.
 */
export type PracticeSessionKind = "quiz" | "result";

/** Đọc `?session=` của tab Ôn tập; tab khác, thiếu hoặc giá trị lạ ⇒ `null`. Không ném lỗi. */
export function parsePracticeSession(params: SearchParamsLike): PracticeSessionKind | null {
  if (parseSubjectTab(params.get("tab")) !== "practice") return null;
  const value = params.get("session")?.trim() ?? "";
  return value === "quiz" || value === "result" ? value : null;
}

/** Địa chỉ tab Ôn tập của một môn, kèm `session` khi đang làm đề hoặc xem kết quả; `null` ⇒ màn thiết lập đề. */
export function practiceSessionHref(code: string, session: PracticeSessionKind | null): string {
  return subjectHref(code, "practice", { session });
}
