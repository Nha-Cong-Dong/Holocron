/**
 * Đích chuyển hướng của các route cũ (`/pairs`, `/practice`, `/vault`) sang tab tương ứng của trang môn. Bản đóng gói
 * là site tĩnh (`output: "export"`) nên không có chuyển hướng phía server: mỗi route cũ chỉ còn một trang đọc query rồi
 * `router.replace` tới địa chỉ hàm này trả về. Tham số sai mẫu bị bỏ (coi như không có); không ném lỗi.
 */

import { parsePairsLocation } from "@/lib/pairs";
import { isSubjectCode, subjectConceptsHref, subjectDocumentsHref, subjectHref } from "@/lib/subject-route";
import { parseVaultLocation, type VaultLocation } from "@/lib/vault";

interface SearchParamsLike {
  get: (name: string) => string | null;
}

/** Trang chủ: đích khi không biết môn nào. */
export const HOME_PATH = "/";

function validSubject(code: string | null): string | null {
  return code !== null && isSubjectCode(code) ? code : null;
}

/**
 * Địa chỉ trang môn ứng với một vị trí của màn `/vault` cũ: có môn ⇒ tab Tài liệu (kèm tài liệu và trang đang xem lại),
 * hoặc tab Khái niệm khi `tab = "chunks"` (danh sách đoạn nay nằm ở khung Đoạn văn của tab Khái niệm). Không có môn:
 * có tài liệu ⇒ trang chủ (không biết tài liệu thuộc môn nào); không có ⇒ môn gần nhất cùng phần đó, không có ⇒
 * trang chủ. Cũng là giá trị mặc định của các danh sách tài liệu khi trang chứa chúng không truyền `hrefFor`.
 */
export function vaultLocationTarget(location: Partial<VaultLocation>, recentSubject: string | null = null): string {
  const subject = validSubject(location.subject ?? null);
  const chunks = location.tab === "chunks";
  if (subject !== null) {
    return chunks ? subjectHref(subject, "concepts") : subjectDocumentsHref(subject, location);
  }
  const doc = location.doc ?? null;
  const recent = validSubject(recentSubject);
  if (doc !== null || recent === null) return HOME_PATH;
  return subjectHref(recent, chunks ? "concepts" : "documents");
}

/**
 * Đích chuyển hướng của một route cũ. `path` là đường dẫn đang mở (có hoặc không `/` cuối), `recentSubject` là môn mở
 * gần nhất đọc từ bộ nhớ trình duyệt (`readRecentSubject`).
 * - `/pairs?subject=&chapter=&status=&chunk=` ⇒ tab Khái niệm của môn, giữ bộ lọc và đoạn đang mở; thiếu môn ⇒ tab
 *   Khái niệm của môn gần nhất (bỏ bộ lọc vì đoạn thuộc môn khác), không có ⇒ trang chủ.
 * - `/practice` ⇒ tab Ôn tập của môn gần nhất, không có ⇒ trang chủ.
 * - `/vault?subject=&doc=&page=&tab=` ⇒ `vaultLocationTarget`.
 * - Đường dẫn khác ⇒ trang chủ.
 */
export function legacyRedirectTarget(path: string, params: SearchParamsLike, recentSubject: string | null): string {
  const normalized = path.replace(/\/+$/, "") || HOME_PATH;
  const recent = validSubject(recentSubject);
  switch (normalized) {
    case "/pairs": {
      const location = parsePairsLocation(params);
      if (location.subject !== null) return subjectConceptsHref(location.subject, location);
      return recent === null ? HOME_PATH : subjectHref(recent, "concepts");
    }
    case "/practice":
      return recent === null ? HOME_PATH : subjectHref(recent, "practice");
    case "/vault":
      return vaultLocationTarget(parseVaultLocation(params), recent);
    default:
      return HOME_PATH;
  }
}
