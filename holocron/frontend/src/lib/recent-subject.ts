/**
 * Môn mở gần nhất, lưu trong `localStorage` để trang chủ biết thẻ nào mang nút chính. Đọc SAU khi component mount
 * (tránh lệch giữa HTML dựng sẵn và lần vẽ đầu). Kho không có, bị chặn hay ném lỗi ⇒ coi như chưa có môn gần nhất.
 */

import { SUBJECT_CODE_PATTERN } from "@/lib/limits";
import type { KeyValueStorage } from "@/lib/theme";

export const RECENT_SUBJECT_STORAGE_KEY = "holocron.recentSubject";

/** Mã môn gần nhất đã lưu; `null` khi chưa có, sai mẫu mã môn hoặc kho ném lỗi. */
export function readRecentSubject(storage: KeyValueStorage | null): string | null {
  if (storage === null) return null;
  try {
    const value = storage.getItem(RECENT_SUBJECT_STORAGE_KEY);
    return value !== null && SUBJECT_CODE_PATTERN.test(value) ? value : null;
  } catch {
    return null;
  }
}

/** Ghi mã môn vừa mở; trả `false` khi mã sai mẫu hoặc kho không ghi được. */
export function writeRecentSubject(storage: KeyValueStorage | null, subjectCode: string): boolean {
  if (storage === null || !SUBJECT_CODE_PATTERN.test(subjectCode)) return false;
  try {
    storage.setItem(RECENT_SUBJECT_STORAGE_KEY, subjectCode);
    return true;
  } catch {
    return false;
  }
}
