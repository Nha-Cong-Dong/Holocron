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
    const isSpace = /\s/.test(char);
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

function normalizeQuote(quote: string): string {
  return quote.normalize("NFC").replace(/\s+/g, " ").trim();
}

/** Tách văn bản chunk (sau NFC) thành ba phần quanh lần xuất hiện đầu tiên của trích dẫn; `null` khi không thấy. */
export function locateQuote(rawText: string, quote: string): QuoteSegments | null {
  const source = rawText.normalize("NFC");
  const needle = normalizeQuote(quote);
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
