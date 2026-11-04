/**
 * Kết quả tra cứu khối (`GET /v1/vault/blocks/search`, §6.10): gom theo Section, nhãn loại khối, tách chữ để tô sáng.
 * Vị trí khớp do backend trả (`match_spans`, UTF-16); hàm ở đây chỉ cắt chuỗi theo các khoảng đó, không tự tìm khớp.
 */

import type { Block, BlockFlag, BlockSearchHit, BlockType, MatchSpan } from "@/types/holocron";

/** Loại khối cho bộ lọc (không có `header_footer`, `page_number`: backend trả 422). */
export const FILTER_TYPES: readonly { type: BlockType; label: string }[] = [
  { type: "paragraph", label: "Chữ" },
  { type: "figure", label: "Hình" },
  { type: "table", label: "Bảng" },
  { type: "formula", label: "Công thức" },
  { type: "code", label: "Code" },
];

const TYPE_LABELS: Record<BlockType, string> = {
  heading: "Tiêu đề",
  paragraph: "Chữ",
  list_item: "Mục danh sách",
  table: "Bảng",
  figure: "Hình",
  formula: "Công thức",
  code: "Code",
  caption: "Chú thích",
  header_footer: "Đầu/chân trang",
  page_number: "Số trang",
};

export function blockTypeLabel(type: BlockType): string {
  return TYPE_LABELS[type];
}

/** Bật/tắt một loại trong bộ lọc, giữ thứ tự của `FILTER_TYPES`. */
export function toggleType(selected: readonly BlockType[], type: BlockType): BlockType[] {
  const next = selected.includes(type) ? selected.filter((item) => item !== type) : [...selected, type];
  return FILTER_TYPES.map((entry) => entry.type).filter((item) => next.includes(item));
}

/** Cờ làm khối "chưa chắc": hiện nhãn đưa người dùng về ảnh gốc. */
const UNCERTAIN_FLAGS: readonly BlockFlag[] = [
  "crop_pending",
  "crop_failed",
  "crop_capped",
  "table_unresolved",
  "table_token_mismatch",
  "formula_text_unverified",
  "code_layout_uncertain",
  "layout_uncertain",
  "reading_order_uncertain",
  "figure_label_text",
  "text_occluded",
  "low_text_contrast",
  "font_encoding_suspect",
  "vlm_invalid",
  "vlm_gate_rejected",
];

export function isUncertain(block: Block): boolean {
  return block.flags.some((flag) => UNCERTAIN_FLAGS.includes(flag));
}

export interface HitGroup {
  key: string;
  documentTitle: string;
  chapterCode: string;
  /** Đường dẫn Section dạng chữ, ví dụ "Chương 2 › Tiến trình". */
  path: string;
  hits: BlockSearchHit[];
}

const UNTITLED = "(Chưa có tiêu đề)";

export function sectionPathLabel(hit: BlockSearchHit): string {
  return hit.section_path
    .map((item) => (item.title !== null && item.title.trim() !== "" ? item.title : UNTITLED))
    .join(" › ");
}

/** Gom kết quả theo (tài liệu, Section), giữ thứ tự xếp hạng của backend theo lần xuất hiện đầu tiên. */
export function groupHits(hits: readonly BlockSearchHit[]): HitGroup[] {
  const groups = new Map<string, HitGroup>();
  for (const hit of hits) {
    const last = hit.section_path[hit.section_path.length - 1];
    const key = `${hit.block.document_id}|${last === undefined ? "" : last.section_id}`;
    const existing = groups.get(key);
    if (existing === undefined) {
      groups.set(key, {
        key,
        documentTitle: hit.document_title,
        chapterCode: hit.chapter_code,
        path: sectionPathLabel(hit),
        hits: [hit],
      });
    } else {
      existing.hits.push(hit);
    }
  }
  return [...groups.values()];
}

export interface TextPart {
  text: string;
  match: boolean;
}

/**
 * Cắt `text` thành đoạn thường và đoạn khớp theo các khoảng backend trả cho đích `text`. Khoảng ngoài chuỗi được kẹp,
 * khoảng chồng nhau bị bỏ để không bao giờ làm mất hay lặp chữ.
 */
export function highlightParts(text: string, spans: readonly MatchSpan[]): TextPart[] {
  const ranges = spans
    .filter((span) => span.target === "text")
    .map((span) => ({ start: Math.max(0, span.start), end: Math.min(text.length, span.end) }))
    .filter((range) => range.end > range.start)
    .sort((a, b) => a.start - b.start);
  const parts: TextPart[] = [];
  let cursor = 0;
  for (const range of ranges) {
    if (range.start < cursor) continue;
    if (range.start > cursor) parts.push({ text: text.slice(cursor, range.start), match: false });
    parts.push({ text: text.slice(range.start, range.end), match: true });
    cursor = range.end;
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor), match: false });
  return parts.length === 0 ? [{ text, match: false }] : parts;
}

/** Khối cần ảnh cắt cạnh chữ: có `crop_ref` và là hình, công thức hoặc có cờ chưa chắc. */
export function showsCrop(block: Block): boolean {
  return block.crop_ref !== null && (block.type === "figure" || block.type === "formula" || isUncertain(block));
}
