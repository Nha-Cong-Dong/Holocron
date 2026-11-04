/**
 * "Đọc lại bằng AI" theo yêu cầu (SPEC §6.10a, ADR-009 mục 15): kiểm dạng thân backend ở ranh giới và logic thuần cho
 * màn xem lại trang. Không tính số nào: chỗ lệch, lý do từ chối, số trang đều do backend trả; giao diện chỉ hiển thị
 * (I1). Chữ AI chỉ hiện khi backend trả `status = "accepted_text"` (đã qua cổng, I3); chữ bị từ chối không bao giờ có
 * trong thân.
 */

import type {
  AiRereadBlock,
  AiRereadErrorCode,
  AiRereadMismatch,
  AiRereadProposal,
  AiRereadStatus,
  BlockFlag,
  BlockType,
  GateReason,
} from "@/types/holocron";

// ------------------------------------------------------------------------------------------- điều kiện khối

/** Cờ cho phép đọc lại bằng AI (SPEC §6.10a): bảng ảnh chưa dựng được, mã lệch bố cục, chữ tương phản thấp. */
export const REREAD_FLAGS: readonly BlockFlag[] = ["table_unresolved", "code_layout_uncertain", "low_text_contrast"];

const REREAD_FLAG_LABEL_VI: Record<string, string> = {
  table_unresolved: "bảng chưa dựng được",
  code_layout_uncertain: "bố cục mã chưa chắc",
  low_text_contrast: "chữ khó đọc (tương phản thấp)",
};

/** Khối của trang ở dạng giao diện cần (từ `PageBlockList`, §6.10). */
export interface PageBlockView {
  blockId: string;
  type: string;
  flags: string[];
  /** Khoá ảnh cắt (64 hex) hoặc `null`. */
  cropRef: string | null;
  text: string | null;
  ocrText: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const CROP_REF_PATTERN = /^[0-9a-f]{64}$/;

/** Khoá ảnh cắt hợp lệ (chặn khoá lạ trước khi ghép vào đường dẫn). */
export function isCropRef(value: unknown): value is string {
  return typeof value === "string" && CROP_REF_PATTERN.test(value);
}

function parsePageBlock(raw: unknown): PageBlockView | null {
  if (!isRecord(raw)) return null;
  if (typeof raw.block_id !== "string" || raw.block_id === "" || typeof raw.type !== "string") return null;
  if (!Array.isArray(raw.flags)) return null;
  return {
    blockId: raw.block_id,
    type: raw.type,
    flags: raw.flags.filter((flag): flag is string => typeof flag === "string"),
    cropRef: isCropRef(raw.crop_ref) ? raw.crop_ref : null,
    text: typeof raw.text === "string" ? raw.text : null,
    ocrText: typeof raw.ocr_text === "string" ? raw.ocr_text : null,
  };
}

/** Response `GET …/pages/{n}/blocks`: khối sai dạng bị bỏ; thân không có `blocks` là mảng ⇒ `null`. */
export function parsePageBlocks(raw: unknown): PageBlockView[] | null {
  if (!isRecord(raw) || !Array.isArray(raw.blocks)) return null;
  const blocks: PageBlockView[] = [];
  for (const item of raw.blocks) {
    const block = parsePageBlock(item);
    if (block !== null) blocks.push(block);
  }
  return blocks;
}

/** Khối có ít nhất một cờ đủ điều kiện đọc lại bằng AI. */
export function isRereadEligible(block: PageBlockView): boolean {
  return block.flags.some((flag) => REREAD_FLAGS.some((allowed) => allowed === flag));
}

/** Các khối đủ điều kiện, giữ thứ tự backend. */
export function eligibleBlocks(blocks: readonly PageBlockView[]): PageBlockView[] {
  return blocks.filter(isRereadEligible);
}

/** Nhãn tiếng Việt của các cờ đủ điều kiện của khối (lý do khối được đề nghị đọc lại). */
export function rereadReasons(block: PageBlockView): string[] {
  return block.flags.flatMap((flag) => {
    const label = REREAD_FLAG_LABEL_VI[flag];
    return label === undefined ? [] : [label];
  });
}

/** Nhãn loại khối cho danh sách (loại lạ giữ nguyên chữ backend). */
const BLOCK_TYPE_LABEL_VI: Record<string, string> = {
  table: "Bảng",
  code: "Mã nguồn",
  paragraph: "Đoạn văn",
  figure: "Hình",
  formula: "Công thức",
  heading: "Tiêu đề",
  list_item: "Mục danh sách",
  caption: "Chú thích",
};

export function blockTypeLabel(type: string): string {
  return BLOCK_TYPE_LABEL_VI[type] ?? type;
}

// ------------------------------------------------------------------------------------------- kiểm dạng đề xuất

const STATUSES: readonly AiRereadStatus[] = ["accepted_text", "rejected"];
const GATE_REASONS: readonly GateReason[] = [
  "ok",
  "status_invalid",
  "status_unavailable",
  "text_layer_overlap",
  "invalid_overlap",
  "ocr_missing",
  "unreadable_marker",
  "empty",
  "mixed_table",
  "too_long",
  "cer",
  "jaccard",
  "number_tokens",
  "code_tokens",
  "figure_only",
];
const TOKEN_KINDS: readonly AiRereadMismatch["token_kind"][] = ["number", "code"];

function pick<T extends string>(options: readonly T[], value: unknown): T | null {
  return options.find((option) => option === value) ?? null;
}

function indexOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function parseMismatch(raw: unknown): AiRereadMismatch | null {
  if (!isRecord(raw) || typeof raw.mismatch_id !== "string") return null;
  const kind = pick(TOKEN_KINDS, raw.token_kind);
  if (kind === null) return null;
  return {
    mismatch_id: raw.mismatch_id,
    token_kind: kind,
    row: indexOrNull(raw.row),
    col: indexOrNull(raw.col),
    vlm_value: stringOrNull(raw.vlm_value),
    ocr_value: stringOrNull(raw.ocr_value),
  };
}

function parseRows(raw: unknown): string[][] | null {
  if (!Array.isArray(raw)) return null;
  const rows: string[][] = [];
  for (const row of raw) {
    if (!Array.isArray(row) || !row.every((cell) => typeof cell === "string")) return null;
    rows.push(row.map(String));
  }
  return rows;
}

/** Cờ khối của đề xuất: giữ chuỗi, bỏ phần tử không phải chuỗi (không dùng để quyết định gì ngoài hiển thị). */
function parseProposalBlock(raw: unknown, flags: readonly BlockFlag[]): AiRereadBlock | null {
  if (!isRecord(raw) || typeof raw.type !== "string") return null;
  const text = raw.text === null ? null : typeof raw.text === "string" ? raw.text : undefined;
  if (text === undefined) return null;
  const rows = raw.rows === null ? null : parseRows(raw.rows);
  // `text` null ⇔ bảng ⇔ `rows` khác null (§6.10a); dạng lệch ⇒ bỏ cả khối chứ không đoán.
  if ((text === null) === (rows === null)) return null;
  const type = BLOCK_TYPES.find((option) => option === raw.type);
  if (type === undefined) return null;
  return { type, text, rows, flags: [...flags] };
}

const BLOCK_TYPES: readonly BlockType[] = [
  "paragraph",
  "heading",
  "list_item",
  "table",
  "figure",
  "formula",
  "code",
  "caption",
  "header_footer",
  "page_number",
];

const ALL_FLAGS: readonly BlockFlag[] = [
  "crop_pending",
  "crop_failed",
  "crop_capped",
  "table_unresolved",
  "table_token_mismatch",
  "boxed",
  "formula_text_unverified",
  "code_layout_uncertain",
  "layout_uncertain",
  "reading_order_uncertain",
  "figure_label_text",
  "text_occluded",
  "low_text_contrast",
  "font_encoding_suspect",
  "human_edited",
  "too_short_for_chunk",
  "ai_proposal_applied",
  "ai_proposal_rejected",
  "vlm_invalid",
  "vlm_gate_rejected",
];

function knownFlags(raw: unknown): BlockFlag[] {
  if (!Array.isArray(raw)) return [];
  return ALL_FLAGS.filter((flag) => raw.some((item) => item === flag));
}

/**
 * Response `POST …/ai-reread` đã kiểm dạng. `accepted_text` mà không có đúng một khối hợp lệ, hoặc `rejected` mà vẫn
 * có khối (chữ AI bị từ chối không được hiện), ⇒ `null` (lỗi phản hồi không hợp lệ).
 */
export function parseAiRereadProposal(raw: unknown): AiRereadProposal | null {
  if (!isRecord(raw)) return null;
  const status = pick(STATUSES, raw.status);
  const reason = pick(GATE_REASONS, raw.reason);
  if (status === null || reason === null) return null;
  if (typeof raw.proposal_id !== "string" || typeof raw.document_id !== "string" || typeof raw.block_id !== "string")
    return null;
  const pageNumber = raw.page_number;
  const pageVersion = raw.page_version;
  if (typeof pageNumber !== "number" || !Number.isInteger(pageNumber) || pageNumber < 1) return null;
  if (typeof pageVersion !== "number" || !Number.isInteger(pageVersion) || pageVersion < 1) return null;
  if (!Array.isArray(raw.blocks) || !Array.isArray(raw.mismatches)) return null;
  if (typeof raw.ocr_text !== "string" || typeof raw.recipient_label !== "string") return null;
  if (typeof raw.input_sha256 !== "string" || typeof raw.from_cache !== "boolean") return null;

  const blocks: AiRereadBlock[] = [];
  for (const item of raw.blocks) {
    const flags = isRecord(item) ? knownFlags(item.flags) : [];
    const block = parseProposalBlock(item, flags);
    if (block === null) return null;
    blocks.push(block);
  }
  if (status === "accepted_text" ? blocks.length !== 1 : blocks.length !== 0) return null;

  const mismatches: AiRereadMismatch[] = [];
  for (const item of raw.mismatches) {
    const mismatch = parseMismatch(item);
    if (mismatch !== null) mismatches.push(mismatch);
  }
  return {
    proposal_id: raw.proposal_id,
    document_id: raw.document_id,
    page_number: pageNumber,
    block_id: raw.block_id,
    page_version: pageVersion,
    status,
    reason,
    blocks,
    mismatches,
    ocr_text: raw.ocr_text,
    input_sha256: raw.input_sha256,
    from_cache: raw.from_cache,
    recipient_label: raw.recipient_label,
  };
}

// ------------------------------------------------------------------------------------------- tô chỗ lệch

/** Khoá ô `"hàng:cột"` của token VLM thừa do backend chỉ ra; giao diện chỉ tô, không so sánh lại. */
export function mismatchCellKey(row: number, col: number): string {
  return `${row}:${col}`;
}

/** Tập ô cần tô (mismatch có `row`/`col`). */
export function mismatchCells(mismatches: readonly AiRereadMismatch[]): Set<string> {
  const cells = new Set<string>();
  for (const mismatch of mismatches) {
    if (mismatch.row !== null && mismatch.col !== null) cells.add(mismatchCellKey(mismatch.row, mismatch.col));
  }
  return cells;
}

/** Token OCR mà AI bỏ sót (chỉ có `ocr_value`, không có vị trí ô), giữ thứ tự backend. */
export function missedTokens(mismatches: readonly AiRereadMismatch[]): string[] {
  return mismatches.flatMap((mismatch) =>
    mismatch.row === null && mismatch.col === null && mismatch.vlm_value === null && mismatch.ocr_value !== null
      ? [mismatch.ocr_value]
      : [],
  );
}

// ------------------------------------------------------------------------------------------- thông điệp

/** Lý do cổng từ chối chữ AI (`status = "rejected"`), một câu tiếng Việt kèm việc làm tiếp. */
export const GATE_REASON_MESSAGE_VI: Record<GateReason, string> = {
  ok: "AI đã đọc lại và chữ qua được bước kiểm.",
  status_invalid: "AI trả về kết quả sai dạng nên Holocron bỏ kết quả này. Giữ chữ hiện có hoặc thử đọc lại.",
  status_unavailable: "AI chưa đọc được vùng này nên Holocron giữ nguyên chữ hiện có.",
  text_layer_overlap: "Vùng này đã có chữ trong tài liệu gốc nên Holocron không nhận chữ AI thay vào.",
  invalid_overlap: "Vùng đọc trùng với khối khác nên Holocron không nhận kết quả.",
  ocr_missing: "Không có chữ OCR độc lập của vùng này để đối chiếu, nên chưa thể kiểm chữ AI.",
  unreadable_marker: "AI báo vùng này không đọc được.",
  empty: "AI không đọc ra chữ nào trong vùng này.",
  mixed_table: "AI đọc vùng này thành lẫn chữ và bảng nên Holocron không nhận.",
  too_long: "Chữ AI dài bất thường so với vùng nên Holocron không nhận.",
  cer: "Chữ AI khác chữ OCR quá nhiều nên Holocron không nhận.",
  jaccard: "Từ trong chữ AI khác chữ OCR quá nhiều nên Holocron không nhận.",
  number_tokens: "Số trong chữ AI không khớp số OCR đọc được nên Holocron không nhận.",
  code_tokens: "Mã trong chữ AI không khớp mã OCR đọc được nên Holocron không nhận.",
  figure_only: "Vùng này là hình, không có chữ để nhận.",
};

export function gateReasonMessage(reason: GateReason): string {
  return GATE_REASON_MESSAGE_VI[reason];
}

/** Việc người dùng làm tiếp sau một lỗi: sang Cài đặt, đọc lại (lấy đề xuất mới), hoặc tải lại trang. */
export type AiRereadNext = "settings" | "reread" | "reload";

export interface AiRereadErrorInfo {
  message: string;
  next: AiRereadNext;
}

export const AI_REREAD_ERRORS: Record<AiRereadErrorCode, AiRereadErrorInfo> = {
  BLOCK_NOT_ELIGIBLE: {
    message: "Khối này không còn đủ điều kiện để đọc lại bằng AI (có thể vừa được sửa). Tải lại trang để xem khối mới.",
    next: "reload",
  },
  AI_NOT_CONFIGURED: {
    message: "Chưa cấu hình nhà cung cấp AI đọc ảnh. Vào Cài đặt để thêm một nhà cung cấp rồi thử lại.",
    next: "settings",
  },
  AI_MODE_OFF: {
    message: "Chế độ đọc ảnh bằng AI đang tắt. Vào Cài đặt để bật rồi thử lại.",
    next: "settings",
  },
  AI_BUDGET_EXHAUSTED: {
    message: "Đã chạm trần chi phí AI nên chưa gửi ảnh đi. Xem lại trần ở Cài đặt rồi thử lại.",
    next: "settings",
  },
  VLM_TIMEOUT: {
    message: "AI trả lời quá lâu nên lần đọc này bị huỷ. Thử đọc lại.",
    next: "reread",
  },
  VLM_RATE_LIMITED: {
    message: "Nhà cung cấp AI đang giới hạn số lượt gọi. Chờ một lúc rồi thử đọc lại.",
    next: "reread",
  },
  VLM_UPSTREAM_ERROR: {
    message: "Nhà cung cấp AI đang gặp lỗi. Thử đọc lại sau ít phút.",
    next: "reread",
  },
  VERSION_CONFLICT: {
    message: "Trang vừa được sửa ở nơi khác nên chưa nhận chữ AI. Tải lại trang rồi đọc lại.",
    next: "reload",
  },
  PROPOSAL_STALE: {
    message: "Đề xuất này đã cũ vì trang vừa thay đổi. Đọc lại bằng AI để lấy đề xuất mới.",
    next: "reread",
  },
  PROPOSAL_NOT_PENDING: {
    message: "Đề xuất này đã được xử lý trước đó. Đọc lại bằng AI nếu cần đề xuất mới.",
    next: "reread",
  },
};

/** Mã lỗi nghiệp vụ trong `detail.code` (hoặc ngay thân) của lỗi 409/503 ở luồng đọc lại; mã lạ ⇒ `null`. */
export function parseAiRereadErrorCode(value: unknown): AiRereadErrorCode | null {
  if (typeof value !== "string") return null;
  return Object.keys(AI_REREAD_ERRORS).find((code): code is AiRereadErrorCode => code === value) ?? null;
}

/** Thông điệp và việc làm tiếp theo mã lỗi; mã lạ hoặc vắng ⇒ `null` (nơi gọi dùng thông điệp chung của lỗi). */
export function aiRereadErrorInfo(code: string | undefined): AiRereadErrorInfo | null {
  const known = parseAiRereadErrorCode(code);
  return known === null ? null : AI_REREAD_ERRORS[known];
}

/** Dòng báo trước khi gửi: ảnh vùng sẽ tới nhà cung cấp AI (ADR-009 mục 15). */
export const SEND_DISCLOSURE_VI =
  "Bấm “Đọc lại bằng AI” sẽ gửi ảnh vùng này tới nhà cung cấp AI bạn đã cấu hình ở Cài đặt. Chữ AI chỉ là đề xuất: Holocron đối chiếu với chữ OCR và bạn phải bấm Nhận thì mới ghi vào trang.";
