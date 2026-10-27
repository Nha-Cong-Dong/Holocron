/**
 * Hàm thuần dùng chung cho văn bản hiển thị, không gắn với màn hay contract nào: vị trí UTF-16 và surrogate, tách văn
 * bản thành đoạn thường và đoạn tô (dùng bởi `VerbatimQuote`).
 */

function isHighSurrogate(code: number): boolean {
  return code >= 0xd800 && code <= 0xdbff;
}

function isLowSurrogate(code: number): boolean {
  return code >= 0xdc00 && code <= 0xdfff;
}

/** Vị trí UTF-16 `offset` rơi giữa một cặp surrogate (cắt đôi một ký tự ngoài BMP). */
export function splitsSurrogate(text: string, offset: number): boolean {
  return (
    offset > 0 &&
    offset < text.length &&
    isHighSurrogate(text.charCodeAt(offset - 1)) &&
    isLowSurrogate(text.charCodeAt(offset))
  );
}

export interface HighlightSegment {
  /** Vị trí UTF-16 của đoạn trong văn bản gốc (dùng làm khoá React ổn định). */
  start: number;
  text: string;
  marked: boolean;
}

/**
 * Tách `text` thành đoạn thường và đoạn tô theo `ranges` (UTF-16). Ghép các đoạn lại luôn ra đúng `text`. Khoảng
 * sai dạng, rỗng, ngoài biên, chồng nhau hoặc cắt đôi cặp surrogate bị bỏ.
 */
export function highlightSegments(text: string, ranges: readonly (readonly [number, number])[]): HighlightSegment[] {
  const sorted = [...ranges]
    .filter(
      ([start, end]) =>
        Number.isInteger(start) &&
        Number.isInteger(end) &&
        start >= 0 &&
        start < end &&
        end <= text.length &&
        !splitsSurrogate(text, start) &&
        !splitsSurrogate(text, end),
    )
    .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const segments: HighlightSegment[] = [];
  let cursor = 0;
  for (const [start, end] of sorted) {
    if (start < cursor) continue;
    if (start > cursor) segments.push({ start: cursor, text: text.slice(cursor, start), marked: false });
    segments.push({ start, text: text.slice(start, end), marked: true });
    cursor = end;
  }
  if (cursor < text.length || segments.length === 0)
    segments.push({ start: cursor, text: text.slice(cursor), marked: false });
  return segments;
}
