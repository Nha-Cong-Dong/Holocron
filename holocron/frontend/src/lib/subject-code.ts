/**
 * Gợi ý mã môn từ tên môn khi tạo môn (§6.8): người dùng chỉ cần gõ tên, mã môn được điền sẵn và vẫn sửa được ở
 * "Tuỳ chọn nâng cao". Mã luôn khớp `SUBJECT_CODE_PATTERN` (2–6 chữ cái in hoa rồi 2–4 chữ số).
 */

import { SUBJECT_CODE_PATTERN } from "@/lib/limits";

/** Phần chữ dự phòng ("Môn học") khi tên không có chữ cái Latin nào hoặc chỉ có một chữ cái. */
const FALLBACK_LETTERS = "MH";
const MIN_LETTERS = 2;
const MAX_LETTERS = 6;
const MAX_SUFFIX = 9999;

/** Bỏ dấu tiếng Việt (`đ` → `d`, `Đ` → `D`) và chuẩn hoá NFC trước khi tách từ. */
export function stripDiacritics(text: string): string {
  return text
    .normalize("NFD")
    .replace(/\p{M}+/gu, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .normalize("NFC");
}

/** Phần chữ của mã: chữ cái đầu mỗi từ (tên nhiều từ) hoặc các chữ đầu (tên một từ), 2–6 chữ in hoa. */
export function subjectCodeLetters(name: string): string {
  const words = stripDiacritics(name)
    .toUpperCase()
    .split(/[^A-Z]+/)
    .filter((word) => word !== "");
  const letters = words.length >= 2 ? words.map((word) => word[0] ?? "").join("") : (words[0] ?? "");
  const trimmed = letters.slice(0, MAX_LETTERS);
  if (trimmed.length >= MIN_LETTERS) return trimmed;
  return `${trimmed}${FALLBACK_LETTERS}`.slice(0, MIN_LETTERS);
}

/**
 * Mã môn gợi ý cho `name`, không trùng mã nào trong `existingCodes` (so không phân biệt hoa thường). Số đuôi bắt đầu
 * từ 01 và tăng dần khi trùng, ví dụ "Vi xử lý" → "VXL01", đã có "VXL01" → "VXL02".
 */
export function suggestSubjectCode(name: string, existingCodes: readonly string[]): string {
  const letters = subjectCodeLetters(name);
  const taken = new Set(existingCodes.map((code) => code.trim().toUpperCase()));
  for (let suffix = 1; suffix <= MAX_SUFFIX; suffix += 1) {
    const candidate = `${letters}${String(suffix).padStart(2, "0")}`;
    if (!taken.has(candidate) && SUBJECT_CODE_PATTERN.test(candidate)) return candidate;
  }
  return `${letters}01`;
}

const CODE_PARTS = /^([A-Z]{2,6})([0-9]{2,4})$/;

/**
 * Mã kế tiếp khi backend báo trùng mã (409): giữ phần chữ, tăng phần số (giữ độ rộng tối thiểu, ví dụ "VXL01" →
 * "VXL02", "AB099" → "AB100"), bỏ qua mã đã có trong `takenCodes`. `null` khi mã không đúng mẫu hoặc hết số 4 chữ số.
 */
export function nextSubjectCode(code: string, takenCodes: readonly string[]): string | null {
  const match = CODE_PARTS.exec(code.trim().toUpperCase());
  const letters = match?.[1];
  const digits = match?.[2];
  if (letters === undefined || digits === undefined) return null;
  const taken = new Set(takenCodes.map((item) => item.trim().toUpperCase()));
  for (let suffix = Number(digits) + 1; suffix <= MAX_SUFFIX; suffix += 1) {
    const candidate = `${letters}${String(suffix).padStart(digits.length, "0")}`;
    if (!taken.has(candidate) && SUBJECT_CODE_PATTERN.test(candidate)) return candidate;
  }
  return null;
}
