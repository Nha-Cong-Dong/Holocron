/**
 * Kiểm dạng dữ liệu của màn luyện tập ở ranh giới (`lib/api.ts` gọi các hàm này trước khi dữ liệu tới component).
 *
 * - Thân JSON đọc như `unknown`; không ép kiểu. Câu hỏi sai dạng bị bỏ và được đếm để báo cho người dùng, không làm
 *   sập trang. Dữ liệu lạ ở cấp ngoài cùng (không phải object, thiếu mảng `twins`) thì cả phản hồi không được nhận.
 * - Câu MCQ4 chỉ được nhận khi đúng luật I2: đủ bốn phương án A–D, `correct_key` thuộc A–D, và mỗi phương án sai có
 *   đúng một misconception (`distractor_to_misconception_map` phủ đúng ba khoá còn lại).
 * - Mọi câu PHẢI có trích dẫn nguyên văn kèm số trang (I3); thiếu thì không hiển thị như kiến thức.
 * - Kết quả chấm tự luận chỉ giữ số ý khớp, tổng số ý và văn bản các ý; `ngram_coverage` bị bỏ ngay tại đây để giao diện
 *   không thể hiển thị nó như điểm (§5.14). Hai con số `matched_count`, `total_points` do backend trả; ở đây chỉ kiểm
 *   chúng khớp với hai danh sách, không đếm lại để thay thế.
 * - Không tính con số nào của đề, đáp án hay lời giải (I1).
 */

import { normalizeForMatch } from "@/lib/source";
import type { OptionKey } from "@/types/holocron";

// ------------------------------------------------------------------------------------- kiểu sau kiểm dạng

export interface PracticeCitation {
  doc_id: string;
  page_number: number;
  verbatim_quote: string;
  chunk_id: string | null;
}

export interface PracticeMisconception {
  code: string;
  label: string;
  rationale: string;
  /** Trích dẫn định nghĩa của khái niệm ứng với phương án nhiễu (câu lý thuyết); `null` với câu tính toán. */
  citation: PracticeCitation | null;
}

export interface PracticeTimelineSegment {
  label: string;
  start: number;
  end: number;
}

export type PracticeArtifact =
  | { kind: "timeline"; caption: string | null; segments: PracticeTimelineSegment[] }
  | { kind: "table"; caption: string | null; headers: string[]; rows: string[][] };

interface QuestionBase {
  twinId: string;
  slotIndex: number;
  questionKind: "computational" | "theory";
  stem: string;
  /** Lời giải do backend trả; với câu lý thuyết là định nghĩa nguyên văn. */
  solution: string;
  citation: PracticeCitation;
  artifacts: PracticeArtifact[];
}

export interface McqQuestion extends QuestionBase {
  kind: "mcq";
  options: Record<OptionKey, string>;
  correctKey: OptionKey;
  /** Đúng ba khoá: các phương án sai (I2). */
  misconceptions: Partial<Record<OptionKey, PracticeMisconception>>;
}

export interface EssayPointView {
  index: number;
  text: string;
}

export interface EssayQuestion extends QuestionBase {
  kind: "essay";
  term: string;
  points: EssayPointView[];
}

export type PracticeQuestion = McqQuestion | EssayQuestion;

export interface PracticeTicket {
  slotIndex: number;
  /** Lý do tiếng Việt do code backend ghi. */
  reason: string;
  /** Slot lý thuyết (`topic_key = null`): câu sinh từ cặp khái niệm đã xác nhận trong Vault. */
  theory: boolean;
}

export interface PracticeBatch {
  batchId: string | null;
  blueprintId: string;
  /** Sắp theo `slot_index`, giữ thứ tự backend trả trong cùng slot. */
  questions: PracticeQuestion[];
  tickets: PracticeTicket[];
  /** Câu bị bỏ: `unsupported` là hình thức màn này chưa luyện được (ví dụ câu điền số), `malformed` là sai dạng. */
  skipped: { unsupported: number; malformed: number };
}

export interface PracticeBlueprintSlot {
  slotIndex: number;
  chapterCode: string;
  format: string;
}

export interface PracticeBlueprint {
  blueprintId: string;
  subjectCode: string;
  slots: PracticeBlueprintSlot[];
}

export interface EssayGradeView {
  twinId: string;
  matchedCount: number;
  totalPoints: number;
  matched: EssayPointView[];
  missing: EssayPointView[];
  citation: PracticeCitation;
}

export interface VaultChunkView {
  chunkId: string;
  docId: string;
  pageNumber: number;
  chapterCode: string;
  rawText: string;
}

export interface ChunkPageView {
  total: number;
  chunks: VaultChunkView[];
}

// ------------------------------------------------------------------------------------------ tiện ích

const OPTION_KEYS: readonly OptionKey[] = ["A", "B", "C", "D"];

const MISCONCEPTION_CODE = /^[A-Z][A-Z0-9_]{2,63}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isText(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "";
}

function isIndex(value: unknown, min: number): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= min;
}

function optionKeyOf(value: unknown): OptionKey | null {
  return OPTION_KEYS.find((key) => key === value) ?? null;
}

function textOrNull(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

// ----------------------------------------------------------------------------------------- phần tử con

export function parseCitation(value: unknown): PracticeCitation | null {
  if (!isRecord(value)) return null;
  const { doc_id, page_number, verbatim_quote, chunk_id } = value;
  if (!isText(doc_id) || !isIndex(page_number, 1) || !isText(verbatim_quote)) return null;
  if (chunk_id !== null && chunk_id !== undefined && typeof chunk_id !== "string") return null;
  return { doc_id, page_number, verbatim_quote, chunk_id: typeof chunk_id === "string" ? chunk_id : null };
}

function parseMisconception(value: unknown): PracticeMisconception | null {
  if (!isRecord(value)) return null;
  const { misconception_code, label_vi, rationale_vi } = value;
  if (typeof misconception_code !== "string" || !MISCONCEPTION_CODE.test(misconception_code)) return null;
  if (!isText(label_vi) || !isText(rationale_vi)) return null;
  let citation: PracticeCitation | null = null;
  if (value.citation !== null && value.citation !== undefined) {
    citation = parseCitation(value.citation);
    if (citation === null) return null;
  }
  return { code: misconception_code, label: label_vi, rationale: rationale_vi, citation };
}

function parseOptions(value: unknown): Record<OptionKey, string> | null {
  if (!isRecord(value)) return null;
  const { A, B, C, D } = value;
  if (!isText(A) || !isText(B) || !isText(C) || !isText(D)) return null;
  return { A, B, C, D };
}

/** Bản đồ misconception đúng luật I2: phủ đúng ba phương án sai, không chứa `correct_key`. */
function parseMisconceptionMap(
  value: unknown,
  correctKey: OptionKey,
): Partial<Record<OptionKey, PracticeMisconception>> | null {
  if (!isRecord(value)) return null;
  const keys = Object.keys(value);
  const wrongKeys = OPTION_KEYS.filter((key) => key !== correctKey);
  if (keys.length !== wrongKeys.length || !wrongKeys.every((key) => keys.includes(key))) return null;
  const result: Partial<Record<OptionKey, PracticeMisconception>> = {};
  for (const key of wrongKeys) {
    const misconception = parseMisconception(value[key]);
    if (misconception === null) return null;
    result[key] = misconception;
  }
  return result;
}

function parseTimelineSegment(value: unknown): PracticeTimelineSegment | null {
  if (!isRecord(value)) return null;
  const label = typeof value.label === "string" ? value.label : value.process;
  const { start, end } = value;
  if (!isText(label) || !isIndex(start, 0) || typeof end !== "number" || !Number.isInteger(end) || end <= start) {
    return null;
  }
  return { label, start, end };
}

function parseArtifact(value: unknown): PracticeArtifact | null {
  if (!isRecord(value)) return null;
  const caption = textOrNull(value.caption_vi);
  if (value.kind === "timeline" && Array.isArray(value.segments)) {
    const segments = value.segments.map(parseTimelineSegment);
    if (segments.length === 0 || segments.some((segment) => segment === null)) return null;
    return { kind: "timeline", caption, segments: segments.filter((s): s is PracticeTimelineSegment => s !== null) };
  }
  if (value.kind === "table" && Array.isArray(value.headers) && Array.isArray(value.rows)) {
    const headers = value.headers.filter((header): header is string => typeof header === "string");
    if (headers.length === 0 || headers.length !== value.headers.length) return null;
    const rows: string[][] = [];
    for (const row of value.rows) {
      if (!Array.isArray(row) || row.length !== headers.length) return null;
      const cells = row.filter((cell): cell is string => typeof cell === "string");
      if (cells.length !== row.length) return null;
      rows.push(cells);
    }
    return { kind: "table", caption, headers, rows };
  }
  return null;
}

/**
 * Artifact lời giải theo `kind` (§5.11). Twin của backend cũ chưa có `artifacts` thì đọc `gantt` thành một artifact
 * `timeline` (cùng dạng đoạn `[start, end)`); không rẽ nhánh theo topic. Artifact lạ hoặc sai dạng bị bỏ.
 */
function parseArtifacts(twin: Record<string, unknown>): PracticeArtifact[] {
  if (Array.isArray(twin.artifacts)) {
    return twin.artifacts.map(parseArtifact).filter((artifact): artifact is PracticeArtifact => artifact !== null);
  }
  if (Array.isArray(twin.gantt) && twin.gantt.length > 0) {
    const legacy = parseArtifact({ kind: "timeline", caption_vi: null, segments: twin.gantt });
    return legacy === null ? [] : [legacy];
  }
  return [];
}

function parseEssayPoints(value: unknown): EssayPointView[] | null {
  if (!Array.isArray(value) || value.length === 0) return null;
  const points: EssayPointView[] = [];
  for (const raw of value) {
    if (!isRecord(raw) || !isIndex(raw.point_index, 0) || !isText(raw.text)) return null;
    points.push({ index: raw.point_index, text: raw.text });
  }
  return points;
}

type TwinParse = { kind: "ok"; question: PracticeQuestion } | { kind: "unsupported" } | { kind: "malformed" };

/** Một câu hỏi của `TwinBatch` (§5.11, §5.14). */
export function parseTwin(value: unknown): TwinParse {
  if (!isRecord(value)) return { kind: "malformed" };
  const { twin_id, slot_index, stem, solution_explanation_vi, format } = value;
  const citation = parseCitation(value.citation);
  if (!isText(twin_id) || !isIndex(slot_index, 1) || !isText(stem) || citation === null) return { kind: "malformed" };
  if (typeof solution_explanation_vi !== "string") return { kind: "malformed" };
  // Backend cũ chưa có `question_kind`: mọi twin khi đó là câu tính toán.
  const questionKind = value.question_kind === "theory" ? "theory" : "computational";
  if (
    value.question_kind !== undefined &&
    value.question_kind !== "theory" &&
    value.question_kind !== "computational"
  ) {
    return { kind: "malformed" };
  }
  const base: QuestionBase = {
    twinId: twin_id,
    slotIndex: slot_index,
    questionKind,
    stem,
    solution: solution_explanation_vi,
    citation,
    artifacts: parseArtifacts(value),
  };

  if (format === "MCQ4") {
    const options = parseOptions(value.options);
    const correctKey = optionKeyOf(value.correct_key);
    if (options === null || correctKey === null) return { kind: "malformed" };
    const misconceptions = parseMisconceptionMap(value.distractor_to_misconception_map, correctKey);
    if (misconceptions === null) return { kind: "malformed" };
    return { kind: "ok", question: { ...base, kind: "mcq", options, correctKey, misconceptions } };
  }
  if (format === "ESSAY") {
    const essay = value.essay;
    if (!isRecord(essay) || !isText(essay.term)) return { kind: "malformed" };
    const points = parseEssayPoints(essay.points);
    if (points === null) return { kind: "malformed" };
    return { kind: "ok", question: { ...base, kind: "essay", term: essay.term, points, artifacts: [] } };
  }
  if (format === "NUMERICAL") return { kind: "unsupported" };
  return { kind: "malformed" };
}

function parseTicket(value: unknown): PracticeTicket | null {
  if (!isRecord(value) || !isIndex(value.slot_index, 1) || !isText(value.reason)) return null;
  return { slotIndex: value.slot_index, reason: value.reason, theory: value.topic_key === null };
}

/** `TwinBatch` (§5.6, §6.7); `null` khi phản hồi không có dạng batch. */
export function parsePracticeBatch(value: unknown): PracticeBatch | null {
  if (!isRecord(value) || !isText(value.blueprint_id) || !Array.isArray(value.twins)) return null;
  const questions: PracticeQuestion[] = [];
  const skipped = { unsupported: 0, malformed: 0 };
  for (const raw of value.twins) {
    const parsed = parseTwin(raw);
    if (parsed.kind === "ok") questions.push(parsed.question);
    else skipped[parsed.kind] += 1;
  }
  const rawTickets = Array.isArray(value.review_tickets) ? value.review_tickets : [];
  const tickets = rawTickets.map(parseTicket).filter((ticket): ticket is PracticeTicket => ticket !== null);
  return {
    batchId: typeof value.batch_id === "string" ? value.batch_id : null,
    blueprintId: value.blueprint_id,
    // `sort` của JS là sắp ổn định: câu cùng slot giữ thứ tự backend trả.
    questions: [...questions].sort((a, b) => a.slotIndex - b.slotIndex),
    tickets: [...tickets].sort((a, b) => a.slotIndex - b.slotIndex),
    skipped,
  };
}

/** `ExamBlueprint` (§5.9); chỉ giữ phần màn luyện tập cần. `null` khi sai dạng. */
export function parseBlueprint(value: unknown): PracticeBlueprint | null {
  if (!isRecord(value) || !isText(value.blueprint_id) || !isText(value.subject_code) || !Array.isArray(value.slots)) {
    return null;
  }
  const slots: PracticeBlueprintSlot[] = [];
  for (const raw of value.slots) {
    if (!isRecord(raw) || !isIndex(raw.slot_index, 1) || !isText(raw.chapter_code) || !isText(raw.format)) return null;
    slots.push({ slotIndex: raw.slot_index, chapterCode: raw.chapter_code, format: raw.format });
  }
  return { blueprintId: value.blueprint_id, subjectCode: value.subject_code, slots };
}

function parsePointResults(value: unknown): EssayPointView[] | null {
  if (!Array.isArray(value)) return null;
  const points: EssayPointView[] = [];
  for (const raw of value) {
    if (!isRecord(raw) || !isIndex(raw.point_index, 0) || !isText(raw.text)) return null;
    points.push({ index: raw.point_index, text: raw.text });
  }
  return points;
}

/** Chỉ số các ý phủ đúng `0..total-1`: không trùng, không thiếu, không nằm ngoài phạm vi. */
function coversAllPoints(points: EssayPointView[], total: number): boolean {
  if (points.length !== total) return false;
  const indices = new Set(points.map((point) => point.index));
  if (indices.size !== total) return false;
  return points.every((point) => point.index < total);
}

/**
 * Mọi ý là chuỗi con của trích dẫn sau NFC và gộp khoảng trắng (quy tắc so khớp I3, §0; validator ESSAY ở §5.14).
 * Backend đã kiểm; đây là lớp phòng thủ thêm để giao diện không hiện một "ý của đáp án mẫu" không có trong tài liệu.
 */
function pointsInQuote(points: EssayPointView[], quote: string): boolean {
  const haystack = normalizeForMatch(quote);
  return points.every((point) => {
    const needle = normalizeForMatch(point.text);
    return needle !== "" && haystack.includes(needle);
  });
}

/**
 * `EssayGradeResult` (§5.14). Nhận khi hai con số khớp với hai danh sách như validator của contract
 * (`matched_count = len(matched_points)`, `matched_count + len(missing_points) = total_points`), chỉ số các ý phủ đúng
 * `0..total_points-1`, và văn bản mỗi ý là chuỗi con của `citation.verbatim_quote` (NFC + gộp khoảng trắng). Lệch thì
 * không nhận, vì hiển thị một con số mâu thuẫn còn tệ hơn báo lỗi. `ngram_coverage` không được giữ lại.
 */
export function parseEssayGradeResult(value: unknown): EssayGradeView | null {
  if (!isRecord(value) || !isText(value.twin_id)) return null;
  const { total_points, matched_count } = value;
  const matched = parsePointResults(value.matched_points);
  const missing = parsePointResults(value.missing_points);
  const citation = parseCitation(value.citation);
  if (!isIndex(total_points, 1) || !isIndex(matched_count, 0) || matched === null || missing === null) return null;
  if (citation === null) return null;
  if (matched.length !== matched_count || matched_count + missing.length !== total_points) return null;
  const all = [...matched, ...missing];
  if (!coversAllPoints(all, total_points) || !pointsInQuote(all, citation.verbatim_quote)) return null;
  return { twinId: value.twin_id, matchedCount: matched_count, totalPoints: total_points, matched, missing, citation };
}

/** `GET /v1/vault/chunks` (§6.6): `{total, chunks}`. Chunk sai dạng bị bỏ. */
export function parseChunkPage(value: unknown): ChunkPageView | null {
  if (!isRecord(value) || !isIndex(value.total, 0) || !Array.isArray(value.chunks)) return null;
  const chunks: VaultChunkView[] = [];
  for (const raw of value.chunks) {
    if (!isRecord(raw)) continue;
    const { chunk_id, doc_id, page_number, chapter_code, raw_text } = raw;
    if (!isText(chunk_id) || !isText(doc_id) || !isIndex(page_number, 1) || typeof raw_text !== "string") continue;
    chunks.push({
      chunkId: chunk_id,
      docId: doc_id,
      pageNumber: page_number,
      chapterCode: typeof chapter_code === "string" ? chapter_code : "",
      rawText: raw_text,
    });
  }
  return { total: value.total, chunks };
}
