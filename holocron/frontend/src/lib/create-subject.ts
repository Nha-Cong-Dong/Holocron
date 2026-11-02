/**
 * Luồng tạo môn của hộp thoại Thêm môn (design-handoff/README.md màn 2): gọi `POST /v1/subjects` (§6.8); backend báo
 * trùng mã (409, loại lỗi `duplicate`) thì tăng số của mã và thử lại đúng một lần. Hàm tạo môn được truyền vào làm
 * tham số để kiểm bằng test, không gọi mạng.
 */

import { toUiError, type UiError } from "@/lib/errors";
import { nextSubjectCode } from "@/lib/subject-code";
import type { Subject, SubjectCreate } from "@/types/holocron";

export type CreateSubjectFn = (payload: SubjectCreate) => Promise<Subject>;

export type CreateSubjectOutcome =
  /** `code`: mã đã gửi ở lần thành công (khác mã ban đầu khi đã thử lại). */
  | { ok: true; subject: Subject; code: string }
  /** `code`: mã của lần gửi cuối cùng, để ô mã môn hiện đúng mã vừa bị từ chối. */
  | { ok: false; error: UiError; code: string };

/**
 * Tạo môn; trùng mã thì thử lại một lần với mã kế tiếp không nằm trong `existingCodes` (và khác mã vừa trùng). Lỗi
 * khác, hoặc mã không tăng được (sai mẫu, hết số), thì không thử lại.
 */
export async function createWithRetry(
  create: CreateSubjectFn,
  payload: SubjectCreate,
  existingCodes: readonly string[],
): Promise<CreateSubjectOutcome> {
  try {
    return { ok: true, subject: await create(payload), code: payload.subject_code };
  } catch (caught) {
    const error = toUiError(caught);
    const next =
      error.kind === "duplicate"
        ? nextSubjectCode(payload.subject_code, [...existingCodes, payload.subject_code])
        : null;
    if (next === null) return { ok: false, error, code: payload.subject_code };
    try {
      return { ok: true, subject: await create({ ...payload, subject_code: next }), code: next };
    } catch (retryCaught) {
      return { ok: false, error: toUiError(retryCaught), code: next };
    }
  }
}
