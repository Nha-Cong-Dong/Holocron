/**
 * Vị trí tô sáng của một trích dẫn trên văn bản gốc của đoạn (T2.3; I3). Backend kiểm `definition_quote` là chuỗi con
 * của `raw_text` sau khi cả hai đưa về NFC và gộp khoảng trắng, nên vị trí tìm bằng đúng phép chuẩn hoá đó rồi ánh xạ
 * ngược về vị trí UTF-16 trong `raw_text` nguyên bản (`Selection` của đường tự bôi đọc offset trên chính chuỗi này).
 * Không tìm thấy ⇒ `null`: giao diện không tô và KHÔNG hiện như đã kiểm chứng.
 */

import { normalizeQuote, PY_COLLAPSE_CHARS, type TextRange } from "@/lib/pairs";

/** Tên vùng tô của CSS Custom Highlight API (`::highlight(holocron-quote)` trong CSS module). */
export const QUOTE_HIGHLIGHT_NAME = "holocron-quote";

/** Một ký tự gốc kèm các dấu kết hợp theo sau, hoặc chuỗi dấu kết hợp mồ côi ở đầu. */
const CLUSTER = /\P{M}\p{M}*|\p{M}+/gu;

/**
 * Khoảng `[start, end)` của `quote` trong `rawText` (vị trí UTF-16 của `rawText` nguyên bản); `null` khi trích dẫn
 * rỗng hoặc không là chuỗi con sau NFC và gộp khoảng trắng. Xuất hiện nhiều lần ⇒ lần đầu. Khoảng nới ra tới hết cụm
 * ký tự-dấu ở hai đầu để không cắt đôi một chữ có dấu.
 */
export function findQuoteRange(rawText: string, quote: string): TextRange | null {
  const needle = normalizeQuote(quote);
  if (needle === "") return null;
  let text = "";
  const starts: number[] = [];
  const ends: number[] = [];
  let lastWasSpace = false;
  for (const match of rawText.matchAll(CLUSTER)) {
    const cluster = match[0];
    const from = match.index;
    const to = from + cluster.length;
    if (PY_COLLAPSE_CHARS.includes(cluster)) {
      if (lastWasSpace) {
        ends[ends.length - 1] = to;
      } else {
        text += " ";
        starts.push(from);
        ends.push(to);
        lastWasSpace = true;
      }
      continue;
    }
    lastWasSpace = false;
    const composed = cluster.normalize("NFC");
    for (let unit = 0; unit < composed.length; unit += 1) {
      starts.push(from);
      ends.push(to);
    }
    text += composed;
  }
  const at = text.indexOf(needle);
  if (at === -1) return null;
  const first = starts[at];
  const last = ends[at + needle.length - 1];
  if (first === undefined || last === undefined) return null;
  return { start: first, end: last };
}

/**
 * Trình duyệt có CSS Custom Highlight API (`CSS.highlights` và `Highlight`) không. Thiếu ⇒ không tô, hiện trích dẫn
 * riêng. Nhận đối tượng toàn cục để test được mà không cần trình duyệt.
 */
export function canHighlight(scope: object): boolean {
  const css = "CSS" in scope ? scope.CSS : undefined;
  const hasRegistry = typeof css === "object" && css !== null && "highlights" in css && css.highlights != null;
  return hasRegistry && "Highlight" in scope && typeof scope.Highlight === "function";
}
