/**
 * Xem đoạn nguồn của một trích dẫn: tìm vị trí `verbatim_quote` trong văn bản chunk để tô đậm (kế hoạch UI mục 5.5).
 *
 * So khớp theo đúng quy tắc của I3 (spec §0): đưa cả hai chuỗi về Unicode NFC rồi gộp mọi chuỗi khoảng trắng thành
 * một dấu cách, sau đó mới tìm chuỗi con. Kết quả được ánh xạ ngược về văn bản chunk (sau NFC, giữ nguyên xuống dòng),
 * nên đoạn hiển thị là nguyên văn tài liệu, không phải bản đã gộp khoảng trắng. Không tìm thấy thì trả `null` để giao
 * diện nói rõ là không định vị được; trích dẫn vẫn hiện nguyên văn ở khối trích dẫn.
 */

export interface QuoteSegments {
  before: string;
  match: string;
  after: string;
}

/**
 * Một ký tự khoảng trắng theo đúng định nghĩa `\s` của Python (`str.isspace`), mà backend dùng khi gộp khoảng trắng
 * (`_collapse_ws` trong `domain/schemas.py`). Khác `\s` của JavaScript ở hai chỗ: có thêm U+001C–U+001F và U+0085;
 * KHÔNG coi U+FEFF là khoảng trắng. Lệch định nghĩa thì giao diện có thể báo khớp một trích dẫn mà backend từ chối,
 * hoặc ngược lại.
 */
const WHITESPACE_CLASS = "\t\n\v\f\r\u001c-\u001f \u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000";
const WHITESPACE_CHAR = new RegExp(`^[${WHITESPACE_CLASS}]$`);
const WHITESPACE_RUN = new RegExp(`[${WHITESPACE_CLASS}]+`, "g");
const EDGE_WHITESPACE = new RegExp(`^[${WHITESPACE_CLASS}]+|[${WHITESPACE_CLASS}]+$`, "g");

/** `char` (một code point) là khoảng trắng theo định nghĩa của backend. */
export function isBackendWhitespace(char: string): boolean {
  return WHITESPACE_CHAR.test(char);
}

/**
 * Dạng so khớp trích dẫn của I3, giống `normalize_quote` của backend: Unicode NFC, gộp mọi chuỗi khoảng trắng thành
 * một dấu cách, bỏ khoảng trắng hai đầu.
 */
export function normalizeForMatch(text: string): string {
  return text.normalize("NFC").replace(WHITESPACE_RUN, " ").replace(EDGE_WHITESPACE, "");
}

interface Collapsed {
  text: string;
  /** `starts[i]`: vị trí trong chuỗi gốc của ký tự thứ `i` của chuỗi đã gộp. */
  starts: number[];
  /** `ends[i]`: vị trí ngay sau ký tự gốc cuối cùng ứng với ký tự `i`. */
  ends: number[];
}

/** Gộp khoảng trắng liên tiếp thành một dấu cách, ghi lại ánh xạ vị trí về chuỗi gốc. */
function collapseWhitespace(source: string): Collapsed {
  const starts: number[] = [];
  const ends: number[] = [];
  let text = "";
  let index = 0;
  for (const char of source) {
    const width = char.length;
    const isSpace = isBackendWhitespace(char);
    if (isSpace && text.endsWith(" ")) {
      ends[ends.length - 1] = index + width;
    } else {
      text += isSpace ? " " : char;
      for (let unit = 0; unit < width; unit += 1) {
        starts.push(index);
        ends.push(index + width);
      }
    }
    index += width;
  }
  return { text, starts, ends };
}

/** Tách văn bản chunk (sau NFC) thành ba phần quanh lần xuất hiện đầu tiên của trích dẫn; `null` khi không thấy. */
export function locateQuote(rawText: string, quote: string): QuoteSegments | null {
  const source = rawText.normalize("NFC");
  const needle = normalizeForMatch(quote);
  if (needle === "") return null;
  const collapsed = collapseWhitespace(source);
  const at = collapsed.text.indexOf(needle);
  if (at < 0) return null;
  const start = collapsed.starts[at];
  const end = collapsed.ends[at + needle.length - 1];
  if (start === undefined || end === undefined) return null;
  return { before: source.slice(0, start), match: source.slice(start, end), after: source.slice(end) };
}

/** Số chunk mỗi lần đọc khi tìm chunk của trích dẫn (giới hạn trên của `limit` ở §6.6). */
export const CHUNK_PAGE_LIMIT = 200;

/** Số lần đọc tối đa khi tìm một chunk theo mã, để không quét mãi một kho rất lớn. */
export const MAX_CHUNK_PAGES = 5;

/**
 * Vị trí đọc tiếp khi tìm chunk theo trang (`offset`/`limit` của backend, §6.6); `null` khi đã hết dữ liệu hoặc đã
 * đọc đủ số lần cho phép. `pagesRead` tính cả lần vừa đọc.
 */
export function nextChunkOffset(offset: number, received: number, total: number, pagesRead: number): number | null {
  const next = offset + received;
  if (received === 0 || next >= total || pagesRead >= MAX_CHUNK_PAGES) return null;
  return next;
}

/**
 * Lần tìm dừng vì đã đọc đủ số lần cho phép trong khi backend vẫn còn chunk chưa đọc. Khi đó chưa tìm thấy chunk không
 * có nghĩa là chunk không còn trong kho; giao diện phải nói là chưa tìm thấy trong phần đã đọc.
 */
export function chunkScanTruncated(offset: number, received: number, total: number, pagesRead: number): boolean {
  return received > 0 && offset + received < total && pagesRead >= MAX_CHUNK_PAGES;
}
