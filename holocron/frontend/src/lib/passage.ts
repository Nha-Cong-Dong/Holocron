/**
 * Logic thuần của khung "Tự tạo khái niệm" (design-handoff/README.md màn 7): tách văn bản của một đoạn thành các câu
 * để chọn nhanh bằng chip (thay cho bôi chuột, dùng được bằng bàn phím), đi tới đoạn trước/sau trong cùng chương, chú
 * thích "Chương N · tr. X" và dựng yêu cầu tạo khái niệm.
 *
 * I3: mỗi câu là đúng một lát `raw_text.slice(start, end)` (vị trí UTF-16), không NFC, không gộp khoảng trắng, không
 * cắt; nhãn chip cắt ngắn chỉ để hiển thị, giá trị gửi đi luôn là lát đầy đủ. Backend vẫn là nơi kiểm cuối cùng.
 */

import { charLength } from "@/lib/documents";
import type { UiError } from "@/lib/errors";
import {
  type ChunkMapStatus,
  type LengthCheck,
  lookupChunk,
  PY_STRIP_CHARS,
  quoteLengthOk,
  termLengthOk,
} from "@/lib/pairs";
import { chapterLabel } from "@/lib/sky";
import { type ChunkView, pageRangeLabel } from "@/lib/vault";

/** Số ký tự (code point) tối đa của nhãn chip câu; câu dài hơn bị cắt và thêm "…" (câu đủ nằm ở `title`). */
export const SENTENCE_LABEL_MAX_CHARS = 44;

export interface SentenceRange {
  /** Vị trí UTF-16 trong `raw_text`, `start < end`. */
  start: number;
  end: number;
  /** Đúng `raw_text.slice(start, end)`. */
  text: string;
  /** Nhãn chip (cắt ngắn khi câu dài hơn `SENTENCE_LABEL_MAX_CHARS`). */
  label: string;
}

/** Dấu kết thúc câu: sau nó là khoảng trắng hoặc hết văn bản thì câu kết thúc. */
const SENTENCE_END = new Set([".", "!", "?", "…"]);
/** Chữ thường ở đầu dòng: dòng trước bị ngắt giữa câu (văn bản PDF gói dòng), chưa phải câu mới. */
const LOWERCASE_START = /^\p{Ll}/u;
const COMBINING_MARK = /^\p{M}$/u;

function isStripChar(char: string): boolean {
  return PY_STRIP_CHARS.includes(char);
}

/** Bỏ khoảng trắng (theo tập của Python) ở hai đầu của khoảng `[start, end)`; trả `null` khi khoảng chỉ có khoảng trắng. */
function trimRange(text: string, start: number, end: number): { start: number; end: number } | null {
  let from = start;
  let to = end;
  while (from < to && isStripChar(text.charAt(from))) from += 1;
  while (to > from && isStripChar(text.charAt(to - 1))) to -= 1;
  return from < to ? { start: from, end: to } : null;
}

/** Ký tự đầu tiên không phải khoảng trắng từ vị trí `from`. */
function nextVisible(text: string, from: number): string {
  let index = from;
  while (index < text.length && isStripChar(text.charAt(index))) index += 1;
  return text.slice(index, index + 2);
}

/** Ký tự cuối cùng không phải khoảng trắng trước vị trí `before`. */
function previousVisible(text: string, before: number): string {
  let index = before - 1;
  while (index >= 0 && isStripChar(text.charAt(index))) index -= 1;
  return index >= 0 ? text.charAt(index) : "";
}

/** Nhãn chip: câu ngắn giữ nguyên; câu dài lấy `max - 2` code point đầu (kèm dấu tổ hợp theo sau) rồi thêm "…". */
export function sentenceLabel(sentence: string, max: number = SENTENCE_LABEL_MAX_CHARS): string {
  if (charLength(sentence) <= max) return sentence;
  const chars = Array.from(sentence);
  let cut = Math.max(1, max - 2);
  // Không tách chữ cái khỏi dấu tổ hợp đi sau nó (văn bản NFD).
  while (cut < chars.length && COMBINING_MARK.test(chars[cut] ?? "")) cut += 1;
  return `${chars.slice(0, cut).join("").trimEnd()}…`;
}

/**
 * Các câu của văn bản đoạn, theo thứ tự xuất hiện. Câu kết thúc ở dấu `.`, `!`, `?`, `…` đứng trước khoảng trắng hoặc
 * cuối văn bản, hoặc ở chỗ xuống dòng (trừ khi dòng sau bắt đầu bằng chữ thường và dòng trước chưa có dấu kết câu:
 * đó là dòng bị gói giữa câu). Khoảng trắng hai đầu mỗi câu bị bỏ khỏi khoảng; câu không qua `quoteLengthOk` (quá ngắn
 * hay quá dài so với giới hạn của backend) bị bỏ. Văn bản rỗng ⇒ không có câu.
 */
export function sentenceRanges(rawText: string): SentenceRange[] {
  const result: SentenceRange[] = [];
  let segmentStart = 0;

  function close(end: number): void {
    const range = trimRange(rawText, segmentStart, end);
    segmentStart = end;
    if (range === null) return;
    const text = rawText.slice(range.start, range.end);
    if (!quoteLengthOk(text).ok) return;
    result.push({ start: range.start, end: range.end, text, label: sentenceLabel(text) });
  }

  for (let index = 0; index < rawText.length; index += 1) {
    const char = rawText.charAt(index);
    if (char === "\n") {
      const continues =
        LOWERCASE_START.test(nextVisible(rawText, index + 1)) && !SENTENCE_END.has(previousVisible(rawText, index));
      if (!continues) close(index + 1);
      continue;
    }
    if (SENTENCE_END.has(char)) {
      const after = rawText.charAt(index + 1);
      if (after === "" || isStripChar(after)) close(index + 1);
    }
  }
  close(rawText.length);
  return result;
}

// ------------------------------------------------------------------------------------ đi giữa các đoạn

export interface PassageNav {
  /** Đoạn trước/sau trong cùng danh sách; `null` ở hai đầu. */
  previous: string | null;
  next: string | null;
  /** Vị trí từ 1 và tổng số đoạn của danh sách (để hiện "Đoạn 3/12"). */
  position: number;
  total: number;
}

/** Đoạn trước/sau của `currentId` trong `chunks` (đoạn của một chương, giữ thứ tự backend); `null` khi không có. */
export function passageNav(chunks: readonly Pick<ChunkView, "chunkId">[], currentId: string): PassageNav | null {
  const index = chunks.findIndex((chunk) => chunk.chunkId === currentId);
  if (index === -1) return null;
  return {
    previous: chunks[index - 1]?.chunkId ?? null,
    next: chunks[index + 1]?.chunkId ?? null,
    position: index + 1,
    total: chunks.length,
  };
}

export interface OpenPassage {
  /** Đoạn hiện trong khung tự tạo; `null` khi môn chưa có đoạn nào. */
  chunk: ChunkView | null;
  /** Vì sao không mở được đoạn `?chunk=` (đang mở đoạn đầu tiên thay vào); `null` khi mở đúng đoạn. */
  note: "not_loaded" | "missing" | null;
}

/**
 * Đoạn mở ở khung tự tạo: đoạn `?chunk=` khi tra được trong map; không có trong map (map chưa tải hết) mà đó là kết
 * quả tìm vừa chọn ⇒ dùng thẳng đoạn backend trả trong kết quả tìm; còn lại ⇒ đoạn đầu tiên của môn kèm lý do.
 */
export function resolveOpenPassage(
  mapStatus: ChunkMapStatus,
  map: ReadonlyMap<string, ChunkView>,
  openChunkId: string | null,
  picked: ChunkView | null,
): OpenPassage {
  const first = map.values().next().value ?? null;
  if (openChunkId === null) return { chunk: first, note: null };
  const lookup = lookupChunk(mapStatus, map, openChunkId);
  if (lookup.kind === "found") return { chunk: lookup.chunk, note: null };
  if (picked !== null && picked.chunkId === openChunkId) return { chunk: picked, note: null };
  return { chunk: first, note: lookup.kind === "missing" ? "missing" : "not_loaded" };
}

/** Đoạn của chương `chapterCode` trong map, giữ thứ tự backend. */
export function chunksOfChapter(map: ReadonlyMap<string, ChunkView>, chapterCode: string): ChunkView[] {
  return [...map.values()].filter((chunk) => chunk.chapterCode === chapterCode);
}

/**
 * Dòng chú thích thu gọn của một khái niệm khi đoạn nguồn tra được: "Chương 2 · tr. 14" (hai số do backend trả). Chỉ
 * dùng khi đã tra được đoạn; không tra được thì KHÔNG hiện số trang.
 */
export function citationCaption(chapterCode: string, chunk: Pick<ChunkView, "pageNumber" | "pageEnd">): string {
  return `${chapterLabel(chapterCode)} · ${pageRangeLabel(chunk)}`;
}

// ------------------------------------------------------------------------------------ tạo khái niệm

export interface PassageSelection {
  start: number;
  end: number;
}

export interface ConceptCreateRequest {
  chunkId: string;
  term: string;
  definitionQuote: string;
}

export type ConceptCreateResult =
  | { ok: true; request: ConceptCreateRequest }
  | { ok: false; field: "term" | "quote"; message: string };

function failed(field: "term" | "quote", check: LengthCheck): ConceptCreateResult | null {
  return check.ok ? null : { ok: false, field, message: check.message };
}

/**
 * Yêu cầu `POST /v1/vault/concept-pairs` từ vùng chọn trong văn bản đoạn: `definitionQuote` là ĐÚNG lát
 * `raw_text.slice(start, end)` (không cắt theo nhãn chip, không chuẩn hoá); `term` bỏ khoảng trắng hai đầu. Vùng chọn
 * ngoài biên ⇒ lỗi ở trích dẫn.
 */
export function conceptCreateRequest(
  chunk: Pick<ChunkView, "chunkId" | "rawText">,
  selection: PassageSelection,
  term: string,
): ConceptCreateResult {
  const { start, end } = selection;
  const inBounds =
    Number.isInteger(start) && Number.isInteger(end) && start >= 0 && start < end && end <= chunk.rawText.length;
  if (!inBounds) return { ok: false, field: "quote", message: "Chọn lại một đoạn trong văn bản." };
  const definitionQuote = chunk.rawText.slice(start, end);
  const quoteError = failed("quote", quoteLengthOk(definitionQuote));
  if (quoteError !== null) return quoteError;
  const trimmed = term.trim();
  const termError = failed("term", termLengthOk(trimmed));
  if (termError !== null) return termError;
  return { ok: true, request: { chunkId: chunk.chunkId, term: trimmed, definitionQuote } };
}

/**
 * Vùng chọn ứng với một đoạn đã bôi bằng chuột (`ChunkHighlighter` trả đúng lát đã bỏ khoảng trắng hai đầu): vị trí
 * xuất hiện đầu tiên của lát đó trong `raw_text`. `null` khi không tìm thấy (không xảy ra với lát lấy từ chính văn bản).
 */
export function selectionOfQuote(rawText: string, quote: string): PassageSelection | null {
  if (quote === "") return null;
  const start = rawText.indexOf(quote);
  return start === -1 ? null : { start, end: start + quote.length };
}

/**
 * Câu "vì sao + cách sửa" khi tìm trong tài liệu không thành. Khung tìm chỉ có nút "Tìm" nên mọi câu kết thúc bằng
 * "bấm Tìm lại." (không nói "Thử lại"); không lộ từ kỹ thuật, mã lỗi nằm ở "Chi tiết kỹ thuật" nơi khác.
 */
export function searchErrorText(error: UiError): string {
  switch (error.kind) {
    case "network":
      return "Holocron chưa chạy xong hoặc đã tắt. Mở lại Holocron rồi bấm Tìm lại.";
    case "busy":
      return "Thư mục dữ liệu của Holocron đang bận. Đợi vài giây rồi bấm Tìm lại.";
    case "forbidden":
      return "Holocron từ chối yêu cầu từ cửa sổ này. Mở lại Holocron trên máy rồi bấm Tìm lại.";
    case "capability":
    case "llm_upstream":
      return "Tìm trong tài liệu chưa dùng được trên máy này. Mở Cài đặt để xem cần cài hay bật thêm gì, rồi bấm Tìm lại.";
    case "validation":
    case "bad_request":
      return "Holocron chưa tìm được với nội dung này. Sửa ô tìm rồi bấm Tìm lại.";
    default:
      return "Holocron gặp trục trặc khi tìm trong tài liệu. Đợi một chút rồi bấm Tìm lại.";
  }
}
