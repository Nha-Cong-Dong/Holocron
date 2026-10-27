/**
 * Logic thuần của màn cặp khái niệm `/pairs` (spec §5.8, §6.6; ADR-006, ADR-007): kiểm dạng `ConceptPair` ở ranh
 * giới, địa chỉ của màn qua query string (static export), lấy đoạn bôi trong văn bản chunk, kiểm độ dài như backend,
 * vị trí tô `term` trong trích dẫn, tải toàn bộ chunk của môn để tra nguồn của cặp, và thao tác được phép theo trạng
 * thái cặp và chunk nguồn.
 *
 * Không có số nào được tính ở đây để hiển thị như kiến thức: độ dài chỉ dùng để báo sớm trước khi gửi, backend vẫn là
 * nơi kiểm cuối cùng (I3).
 */

import { charLength } from "@/lib/documents";
import { toUiError, type UiError } from "@/lib/errors";
import type { LatestCounter } from "@/lib/latest";
import {
  CHAPTER_CODE_PATTERN,
  CHUNK_ID_MAX_CHARS,
  PAIR_QUOTE_MAX_CHARS,
  PAIR_QUOTE_MIN_CHARS,
  PAIR_TERM_MAX_CHARS,
  PAIR_TERM_MIN_CHARS,
  SUBJECT_CODE_PATTERN,
} from "@/lib/limits";
import { type ChunkListView, type ChunkView, hasNextPage, nextPageOffset } from "@/lib/vault";
import type { Citation, ConceptPair, ConceptPairStatus } from "@/types/holocron";

// ----------------------------------------------------------------------------- khoảng trắng như Python

/**
 * Tập ký tự mà `str.strip()` của Python bỏ ở hai đầu (Pydantic `str_strip_whitespace`, `domain/schemas.py:69`): mọi
 * ký tự có `str.isspace()`, gồm U+001C–U+001F và U+0085, KHÔNG gồm U+FEFF. Khác `trim()` của JS (JS có U+FEFF, không
 * có U+001C–U+001F, U+0085).
 */
export const PY_STRIP_CHARS =
  "\t\n\u000b\f\r\u001c\u001d\u001e\u001f \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000";

/**
 * Tập ký tự mà `re.sub(r"\s+", " ", …)` của Python gộp (`domain/schemas.py:51-52`): `\s` của `re` với chuỗi Unicode
 * khớp đúng các ký tự có `str.isspace()`, cùng tập với `PY_STRIP_CHARS` nhưng giữ riêng để mỗi phép có nguồn riêng.
 * Không dùng `\s` của JS (JS có U+FEFF).
 */
export const PY_COLLAPSE_CHARS =
  "\t\n\u000b\f\r\u001c\u001d\u001e\u001f \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000";

const COLLAPSE_RUN = new RegExp(`[${PY_COLLAPSE_CHARS}]+`, "gu");

/** `str.strip()` của Python: bỏ ký tự thuộc `PY_STRIP_CHARS` ở hai đầu, không chuẩn hoá phần giữa. */
export function pyStrip(text: string): string {
  let start = 0;
  let end = text.length;
  while (start < end && PY_STRIP_CHARS.includes(text.charAt(start))) start += 1;
  while (end > start && PY_STRIP_CHARS.includes(text.charAt(end - 1))) end -= 1;
  return text.slice(start, end);
}

/** `_collapse_ws` của backend (`domain/schemas.py:51-52`): gộp mỗi dãy khoảng trắng thành một dấu cách rồi bỏ hai đầu. */
export function collapseWhitespace(text: string): string {
  return pyStrip(text.replace(COLLAPSE_RUN, " "));
}

/** `normalize_quote` của backend: NFC rồi gộp khoảng trắng (I3). Chỉ dùng để kiểm độ dài trước khi gửi. */
export function normalizeQuote(text: string): string {
  return collapseWhitespace(text.normalize("NFC"));
}

// ------------------------------------------------------------------------------------ độ dài

export type LengthCheck = { ok: true } | { ok: false; message: string };

/** `term`: 1–120 code point sau khi bỏ khoảng trắng hai đầu như Pydantic. */
export function termLengthOk(term: string): LengthCheck {
  const length = charLength(pyStrip(term));
  if (length < PAIR_TERM_MIN_CHARS) return { ok: false, message: "Nhập thuật ngữ." };
  if (length > PAIR_TERM_MAX_CHARS) {
    return { ok: false, message: `Thuật ngữ tối đa ${PAIR_TERM_MAX_CHARS} ký tự (đang có ${length}).` };
  }
  return { ok: true };
}

/**
 * `definition_quote`: tối đa 600 code point sau khi bỏ khoảng trắng hai đầu (chưa NFC, chưa gộp, như Pydantic kiểm
 * `max_length` trước validator) và ít nhất 8 code point sau NFC và gộp khoảng trắng (`ConceptPair._quote_nfc`).
 */
export function quoteLengthOk(quote: string): LengthCheck {
  const stripped = pyStrip(quote);
  if (stripped === "") return { ok: false, message: "Chưa có đoạn định nghĩa. Bôi một đoạn trong văn bản của chunk." };
  const rawLength = charLength(stripped);
  if (rawLength > PAIR_QUOTE_MAX_CHARS) {
    return {
      ok: false,
      message: `Đoạn định nghĩa tối đa ${PAIR_QUOTE_MAX_CHARS} ký tự, tính cả khoảng trắng và xuống dòng ở giữa (đang có ${rawLength}). Bôi đoạn ngắn hơn.`,
    };
  }
  if (charLength(normalizeQuote(stripped)) < PAIR_QUOTE_MIN_CHARS) {
    return {
      ok: false,
      message: `Đoạn định nghĩa quá ngắn: cần ít nhất ${PAIR_QUOTE_MIN_CHARS} ký tự sau khi gộp khoảng trắng.`,
    };
  }
  return { ok: true };
}

// ------------------------------------------------------------------------------------ bôi đoạn

export interface TextRange {
  /** Vị trí UTF-16 (như `Selection`), `start ≤ end`. */
  start: number;
  end: number;
}

function isHighSurrogate(code: number): boolean {
  return code >= 0xd800 && code <= 0xdbff;
}

function isLowSurrogate(code: number): boolean {
  return code >= 0xdc00 && code <= 0xdfff;
}

/** Vị trí `offset` rơi giữa một cặp surrogate. */
function splitsSurrogate(text: string, offset: number): boolean {
  return (
    offset > 0 &&
    offset < text.length &&
    isHighSurrogate(text.charCodeAt(offset - 1)) &&
    isLowSurrogate(text.charCodeAt(offset))
  );
}

/**
 * Vùng bôi trong văn bản chunk, chuẩn hoá bôi ngược (`focus` trước `anchor`). `null` khi vùng bôi không nằm trọn trong
 * cùng một text node (`sameNode = false`), node đó không phải văn bản chunk (`nodeData !== rawText`), hoặc vị trí ngoài
 * biên. Vị trí rơi giữa một cặp surrogate được nới ra để không cắt đôi ký tự.
 */
export function selectionRange(
  rawText: string,
  nodeData: string | null,
  anchorOffset: number,
  focusOffset: number,
  sameNode: boolean,
): TextRange | null {
  if (!sameNode || nodeData !== rawText) return null;
  const inBounds = (offset: number) => Number.isInteger(offset) && offset >= 0 && offset <= rawText.length;
  if (!inBounds(anchorOffset) || !inBounds(focusOffset)) return null;
  let start = Math.min(anchorOffset, focusOffset);
  let end = Math.max(anchorOffset, focusOffset);
  if (splitsSurrogate(rawText, start)) start -= 1;
  if (splitsSurrogate(rawText, end)) end += 1;
  return { start, end };
}

/** Đúng lát `rawText[start, end)`, chỉ bỏ khoảng trắng hai đầu như Python; không NFC, không gộp khoảng trắng. */
export function selectionToQuote(rawText: string, start: number, end: number): string {
  return pyStrip(rawText.slice(start, end));
}

// ------------------------------------------------------------------------------------ tô term

/** Đổi vị trí theo code point (như Python) sang vị trí UTF-16 trong `text`; `null` khi vượt độ dài. */
function codePointToUtf16(text: string, codePoints: number): number | null {
  let units = 0;
  let count = 0;
  for (const char of text) {
    if (count === codePoints) return units;
    units += char.length;
    count += 1;
  }
  return count === codePoints ? units : null;
}

function isSpanTuple(value: unknown): value is [number, number] {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    Number.isInteger(value[0]) &&
    Number.isInteger(value[1]) &&
    typeof value[0] === "number" &&
    typeof value[1] === "number"
  );
}

/**
 * Vị trí tô `term` trong `definition_quote`: `term_spans` của backend tính theo code point (Python) trên trích dẫn
 * đã NFC, đổi sang UTF-16 cho DOM. Span sai dạng, rỗng, ngoài biên hoặc chồng lên span đứng trước bị bỏ.
 */
export function termSegments(quote: string, termSpans: readonly unknown[]): [number, number][] {
  const valid = termSpans
    .filter(isSpanTuple)
    .filter(([start, end]) => start >= 0 && start < end)
    .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const result: [number, number][] = [];
  let lastEnd = -1;
  for (const [start, end] of valid) {
    if (start < lastEnd) continue;
    const from = codePointToUtf16(quote, start);
    const to = codePointToUtf16(quote, end);
    if (from === null || to === null) continue;
    result.push([from, to]);
    lastEnd = end;
  }
  return result;
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

// ------------------------------------------------------------------------------------ kiểm dạng

const PAIR_STATUSES: readonly ConceptPairStatus[] = ["proposed", "confirmed", "rejected"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isText(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "";
}

function isPairStatus(value: unknown): value is ConceptPairStatus {
  return PAIR_STATUSES.some((status) => status === value);
}

/**
 * Một `ConceptPair` (§5.8); `null` khi thiếu định danh, `term`, `definition_quote`, `status` lạ, `proposed_by` lạ hoặc
 * thiếu `version` (không gửi được `expected_version`). `term_spans` không phải mảng ⇒ coi như không có vị trí tô;
 * phần tử span sai dạng bị bỏ.
 */
export function parseConceptPair(value: unknown): ConceptPair | null {
  if (!isRecord(value)) return null;
  const { pair_id, chunk_id, subject_code, chapter_code, term, definition_quote, status, proposed_by } = value;
  if (!isText(pair_id) || !isText(chunk_id) || !isText(subject_code) || !isText(chapter_code)) return null;
  if (!isText(term) || !isText(definition_quote) || !isPairStatus(status)) return null;
  if (proposed_by !== "llm" && proposed_by !== "user") return null;
  const { version, created_at, confirmed_at, mapping_confidence } = value;
  if (typeof version !== "number" || !Number.isInteger(version) || version < 1) return null;
  const spans = Array.isArray(value.term_spans) ? value.term_spans.filter(isSpanTuple) : [];
  return {
    pair_id,
    chunk_id,
    subject_code,
    chapter_code,
    term,
    definition_quote,
    term_spans: spans.map(([start, end]): [number, number] => [start, end]),
    status,
    proposed_by,
    mapping_confidence:
      typeof mapping_confidence === "number" && Number.isFinite(mapping_confidence) ? mapping_confidence : null,
    version,
    created_at: typeof created_at === "string" ? created_at : "",
    confirmed_at: typeof confirmed_at === "string" ? confirmed_at : null,
  };
}

export interface ConceptPairList {
  /** Giữ đúng thứ tự backend trả, `(chapter_code, term, pair_id)`. */
  pairs: ConceptPair[];
  /** Có phần tử sai dạng đã bị bỏ. */
  hasSkipped: boolean;
}

/** `200 list[ConceptPair]` của `GET /v1/vault/concept-pairs`; `null` khi thân không phải mảng. */
export function parseConceptPairList(value: unknown): ConceptPairList | null {
  if (!Array.isArray(value)) return null;
  const pairs: ConceptPair[] = [];
  for (const raw of value) {
    const pair = parseConceptPair(raw);
    if (pair !== null) pairs.push(pair);
  }
  return { pairs, hasSkipped: pairs.length !== value.length };
}

// ---------------------------------------------------------------------------- địa chỉ màn /pairs

export interface PairsLocation {
  subject: string | null;
  chapter: string | null;
  status: ConceptPairStatus | null;
  chunk: string | null;
}

interface SearchParamsLike {
  get: (name: string) => string | null;
}

/** Đọc `?subject=&chapter=&status=&chunk=`; giá trị sai mẫu bị bỏ (coi như không có), không ném lỗi. */
export function parsePairsLocation(params: SearchParamsLike): PairsLocation {
  const subject = params.get("subject")?.trim() ?? "";
  const chapter = params.get("chapter")?.trim() ?? "";
  const status = params.get("status")?.trim() ?? "";
  const chunk = params.get("chunk") ?? "";
  const chunkLength = charLength(chunk);
  return {
    subject: SUBJECT_CODE_PATTERN.test(subject) ? subject : null,
    chapter: CHAPTER_CODE_PATTERN.test(chapter) ? chapter : null,
    status: isPairStatus(status) ? status : null,
    chunk: chunkLength >= 1 && chunkLength <= CHUNK_ID_MAX_CHARS && chunk.trim() === chunk ? chunk : null,
  };
}

/** Địa chỉ của màn cặp khái niệm; chỉ ghi tham số có giá trị. */
export function pairsHref(location: Partial<PairsLocation>): string {
  const params = new URLSearchParams();
  if (location.subject) params.set("subject", location.subject);
  if (location.chapter) params.set("chapter", location.chapter);
  if (location.status) params.set("status", location.status);
  if (location.chunk) params.set("chunk", location.chunk);
  const query = params.toString();
  return query === "" ? "/pairs" : `/pairs?${query}`;
}

// --------------------------------------------------------------------------- map chunk của môn

export type ChunkMapStatus = "complete" | "partial" | "error";

export type ChunkMapResult =
  | { status: "complete" | "partial"; map: ReadonlyMap<string, ChunkView> }
  | { status: "error"; map: ReadonlyMap<string, ChunkView>; error: UiError };

/** Một trang `GET /v1/vault/chunks?subject_code=…` (không lọc chương), đã kiểm dạng. */
export type ChunkPageFetcher = (offset: number, limit: number) => Promise<ChunkListView>;

/**
 * Tải mọi chunk của môn theo trang để tra nguồn của cặp: dừng khi trang rỗng hoặc đã hết `total` (`complete`), hoặc
 * khi chạm `maxRequests` mà backend còn trang (`partial`). Lỗi ở bất kỳ trang nào ⇒ `error` với map rỗng (không dùng
 * map dở). Thứ tự trong map giữ thứ tự backend trả.
 */
export async function loadChunkMap(
  fetchPage: ChunkPageFetcher,
  pageSize: number,
  maxRequests: number,
): Promise<ChunkMapResult> {
  const map = new Map<string, ChunkView>();
  let offset = 0;
  for (let request = 0; request < maxRequests; request += 1) {
    let page: ChunkListView;
    try {
      page = await fetchPage(offset, pageSize);
    } catch (error) {
      return { status: "error", map: new Map(), error: toUiError(error) };
    }
    for (const chunk of page.chunks) {
      if (!map.has(chunk.chunkId)) map.set(chunk.chunkId, chunk);
    }
    if (!hasNextPage(offset, page.received, page.total)) return { status: "complete", map };
    offset = nextPageOffset(offset, page.received);
  }
  return { status: "partial", map };
}

/** Mã chương có chunk trong map, theo thứ tự xuất hiện đầu tiên (gợi ý cho ô lọc chương). */
export function chaptersOf(map: ReadonlyMap<string, ChunkView>): string[] {
  const chapters: string[] = [];
  for (const chunk of map.values()) {
    if (!chapters.includes(chunk.chapterCode)) chapters.push(chunk.chapterCode);
  }
  return chapters;
}

/**
 * Chunk để chọn ở cột phải: chunk của chương `chapter` (đủ), hoặc khi chưa chọn chương thì `previewCount` chunk đầu
 * của môn. `truncated` cho biết còn chunk không hiện.
 */
export function chunksToPick(
  map: ReadonlyMap<string, ChunkView>,
  chapter: string | null,
  previewCount: number,
): { chunks: ChunkView[]; truncated: boolean } {
  const all = [...map.values()];
  if (chapter !== null) return { chunks: all.filter((chunk) => chunk.chapterCode === chapter), truncated: false };
  return { chunks: all.slice(0, previewCount), truncated: all.length > previewCount };
}

/** Kết quả tra chunk nguồn của một cặp hay của `?chunk=`. */
export type ChunkLookup =
  | { kind: "found"; chunk: ChunkView }
  | { kind: "missing" }
  | { kind: "not_loaded" }
  | { kind: "unknown" };

/**
 * Tra chunk trong map: thấy ⇒ `found`; không thấy mà map đủ ⇒ `missing` (không tìm thấy chunk nguồn); không thấy mà
 * map chưa đủ ⇒ `not_loaded` (chưa tải hết, KHÔNG nói không tìm thấy); map đang tải hoặc lỗi ⇒ `unknown`.
 */
export function lookupChunk(
  status: ChunkMapStatus | "loading",
  map: ReadonlyMap<string, ChunkView>,
  chunkId: string,
): ChunkLookup {
  if (status === "loading" || status === "error") return { kind: "unknown" };
  const chunk = map.get(chunkId);
  if (chunk !== undefined) return { kind: "found", chunk };
  return status === "complete" ? { kind: "missing" } : { kind: "not_loaded" };
}

/** Nhãn khi trích dẫn của cặp không hiện được như trích dẫn đã kiểm chứng (không có chunk nguồn trong map). */
export function lookupLabel(lookup: Exclude<ChunkLookup, { kind: "found" }>): string {
  if (lookup.kind === "missing") return "Không tìm thấy chunk nguồn — chưa kiểm chứng được trang";
  if (lookup.kind === "not_loaded") return "Chưa tải hết chunk của môn — chưa kiểm chứng được trang";
  return "Chưa tải được chunk của môn — chưa kiểm chứng được trang";
}

/**
 * Trích dẫn của cặp để hiện qua `VerbatimQuote`: `verbatim_quote` là `definition_quote` của cặp, `chunk_id` của cặp,
 * `doc_id` và `page_number` lấy từ chunk nguồn (KHÔNG dùng đoạn trích tiêu biểu của chunk).
 */
export function pairCitation(pair: ConceptPair, chunk: ChunkView): Citation {
  return {
    doc_id: chunk.docId,
    page_number: chunk.pageNumber,
    verbatim_quote: pair.definition_quote,
    chunk_id: pair.chunk_id,
  };
}

// ---------------------------------------------------------------------------- thao tác theo trạng thái

export const PAIR_STATUS_LABEL_VI: Record<ConceptPairStatus, string> = {
  proposed: "Chờ xác nhận",
  confirmed: "Đã xác nhận",
  rejected: "Đã từ chối",
};

/** Lựa chọn của ô lọc trạng thái; giá trị rỗng là mọi trạng thái. */
export const PAIR_STATUS_FILTERS: readonly { value: ConceptPairStatus | ""; label: string }[] = [
  { value: "", label: "Mọi trạng thái" },
  ...PAIR_STATUSES.map((value) => ({ value, label: PAIR_STATUS_LABEL_VI[value] })),
];

export type ActionGate =
  | { shown: false }
  | { shown: true; enabled: true }
  | { shown: true; enabled: false; reason: string };

export interface PairActions {
  edit: ActionGate;
  /** Nút "Xác nhận" (cặp chờ xác nhận) hoặc "Xác nhận lại" (cặp đã từ chối). */
  confirm: ActionGate;
  reject: ActionGate;
}

const HIDDEN: ActionGate = { shown: false };
const ENABLED: ActionGate = { shown: true, enabled: true };

function blockedReason(lookup: ChunkLookup): string | null {
  if (lookup.kind === "found") return null;
  if (lookup.kind === "missing") {
    return "Không tìm thấy chunk nguồn của cặp này nên chưa sửa hay xác nhận được; vẫn có thể từ chối.";
  }
  if (lookup.kind === "not_loaded") {
    return "Chưa tải hết chunk của môn nên chưa kiểm chứng được chunk nguồn; chưa sửa hay xác nhận được, vẫn có thể từ chối.";
  }
  return "Chưa tải được chunk của môn nên chưa sửa hay xác nhận được cặp này; vẫn có thể từ chối.";
}

/**
 * Nút được hiện và bật theo trạng thái cặp và kết quả tra chunk nguồn. Sửa và xác nhận cần chunk nguồn trong map
 * (sửa cần văn bản để bôi; xác nhận cần kiểm chứng được trang); từ chối luôn được.
 */
export function pairActions(status: ConceptPairStatus, lookup: ChunkLookup): PairActions {
  const reason = blockedReason(lookup);
  const gated: ActionGate = reason === null ? ENABLED : { shown: true, enabled: false, reason };
  if (status === "proposed") return { edit: gated, confirm: gated, reject: ENABLED };
  if (status === "confirmed") return { edit: gated, confirm: HIDDEN, reject: ENABLED };
  return { edit: HIDDEN, confirm: gated, reject: HIDDEN };
}

/** Request ghi đang chạy thì khoá nút: backend không chống gửi trùng. */
export function canSubmit(state: { pending: boolean }): boolean {
  return !state.pending;
}

/** Thao tác ghi với cặp; mỗi thao tác có cách xử lý lỗi riêng. */
export type PairOperation = "create" | "confirm" | "reject";

/**
 * Nút đi kèm lỗi của thao tác với cặp:
 * - `reload_all`: tải lại cả map chunk lẫn danh sách cặp (404: cặp hoặc chunk không còn).
 * - `reload_list`: tải lại danh sách cặp (409 lệch phiên bản; lỗi mạng, máy chủ, kho bận khi tạo cặp: KHÔNG gửi lại
 *   vì có thể tạo trùng).
 * - `retry`: gửi lại đúng thao tác (xác nhận, từ chối khi lỗi mạng, máy chủ, kho bận).
 */
export type PairErrorAction = { kind: "reload_all" | "reload_list" | "retry"; label: string } | null;

const TRANSIENT_KINDS: readonly UiError["kind"][] = ["network", "server", "busy", "unknown"];

export function pairErrorAction(operation: PairOperation, error: UiError): PairErrorAction {
  if (error.kind === "not_found") return { kind: "reload_all", label: "Tải lại" };
  if (error.kind === "version_conflict" || error.kind === "state_conflict") {
    return { kind: "reload_list", label: "Tải lại" };
  }
  if (TRANSIENT_KINDS.includes(error.kind)) {
    return operation === "create"
      ? { kind: "reload_list", label: "Tải lại danh sách" }
      : { kind: "retry", label: "Thử lại" };
  }
  return null;
}

/** Bộ lọc của danh sách sau khi tạo cặp: cặp tạo bằng bôi là `confirmed`, nên bộ lọc đang ẩn trạng thái đó chuyển sang. */
export function statusAfterCreate(current: ConceptPairStatus | null): ConceptPairStatus | null {
  return current === "proposed" || current === "rejected" ? "confirmed" : current;
}

// ---------------------------------------------------------------------------- môn khi vào trang

/**
 * Trạng thái đồng bộ một chiều giữa `?subject=` (chỉ đọc khi vào trang) và môn ở thanh bên (nguồn duy nhất sau đó).
 * - `expected`: môn mà màn đang coi là môn hiện tại.
 * - `waitingFrom`: đã yêu cầu thanh bên chọn `expected` nhưng thanh bên chưa đổi; giữ môn của thanh bên lúc yêu cầu.
 */
export interface SubjectEntryState {
  done: boolean;
  expected: string | null;
  waitingFrom: string | null;
}

export const INITIAL_SUBJECT_ENTRY: SubjectEntryState = { done: false, expected: null, waitingFrom: null };

/** Việc màn phải làm sau một bước: chọn môn ở thanh bên một lần, hoặc bỏ chương và chunk vì người dùng đổi môn. */
export type SubjectEntryAction = { kind: "none" } | { kind: "select"; subject: string } | { kind: "reset" };

export interface SubjectEntryStep {
  state: SubjectEntryState;
  action: SubjectEntryAction;
  /** Môn đã ổn định (không còn chờ thanh bên đổi): màn được tải dữ liệu theo môn của thanh bên. */
  ready: boolean;
}

/**
 * Một bước đồng bộ, idempotent: gọi lại với cùng đầu vào (ví dụ effect chạy hai lần dưới React StrictMode) không yêu
 * cầu chọn môn lần nữa và không coi là người dùng đổi môn. Chỉ khi thanh bên đổi sang môn khác `expected` (và không phải
 * do đang chờ yêu cầu của chính màn) mới trả `reset`.
 */
export function subjectEntryStep(
  state: SubjectEntryState,
  urlSubject: string | null,
  shellSubject: string | null,
): SubjectEntryStep {
  if (!state.done) {
    if (urlSubject !== null && urlSubject !== shellSubject) {
      return {
        state: { done: true, expected: urlSubject, waitingFrom: shellSubject },
        action: { kind: "select", subject: urlSubject },
        ready: false,
      };
    }
    return { state: { done: true, expected: shellSubject, waitingFrom: null }, action: { kind: "none" }, ready: true };
  }
  if (shellSubject === state.expected) {
    return { state: { ...state, waitingFrom: null }, action: { kind: "none" }, ready: true };
  }
  if (state.waitingFrom !== null && shellSubject === state.waitingFrom) {
    // Thanh bên chưa áp yêu cầu chọn môn của chính màn: chưa phải người dùng đổi môn.
    return { state, action: { kind: "none" }, ready: false };
  }
  return { state: { done: true, expected: shellSubject, waitingFrom: null }, action: { kind: "reset" }, ready: true };
}

// ---------------------------------------------------------------------------- chạy đua

export type Outcome<T> = { ok: true; value: T } | { ok: false; error: UiError };

/**
 * Chạy `task` như một lượt mới của `counter`; chỉ áp kết quả (`apply`) khi lượt này còn là lượt mới nhất, để phản hồi
 * cũ (đổi bộ lọc hay đổi môn khi đang tải) không ghi đè dữ liệu mới.
 */
export async function runLatest<T>(
  counter: LatestCounter,
  task: () => Promise<T>,
  apply: (outcome: Outcome<T>) => void,
): Promise<void> {
  const ticket = counter.begin();
  let outcome: Outcome<T>;
  try {
    outcome = { ok: true, value: await task() };
  } catch (error) {
    outcome = { ok: false, error: toUiError(error) };
  }
  if (counter.isLatest(ticket)) apply(outcome);
}
