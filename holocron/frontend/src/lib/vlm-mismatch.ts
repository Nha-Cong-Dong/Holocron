/**
 * Chỗ lệch ô bảng do AI đọc (ADR-009 mục 3.4–3.5; SPEC §6.5 `PATCH …/lines`). Logic thuần: gom chỗ lệch theo khối và
 * dựng payload. Không so lại, không tính lại giá trị nào: chuỗi hiển thị là chuỗi backend trả.
 */

import type { VlmMismatchView } from "./documents-data";

/** Các chỗ lệch của một khối VLM, giữ thứ tự backend trả. */
export interface MismatchGroup {
  blockId: string;
  items: VlmMismatchView[];
}

/** Gom theo `block_id`; thứ tự nhóm theo lần xuất hiện đầu tiên. */
export function groupMismatchesByBlock(mismatches: readonly VlmMismatchView[]): MismatchGroup[] {
  const groups: MismatchGroup[] = [];
  for (const item of mismatches) {
    const existing = groups.find((group) => group.blockId === item.blockId);
    if (existing === undefined) groups.push({ blockId: item.blockId, items: [item] });
    else existing.items.push(item);
  }
  return groups;
}

/** Phần thay đổi gửi kèm `PATCH …/lines`: đúng một trong hai danh sách có đúng một phần tử. */
export interface MismatchPatch {
  keptMismatches: string[];
  rejectedVlmBlocks: string[];
}

/** "Giữ giá trị này": từng chỗ lệch một, không bao giờ nhiều (không có "giữ tất cả"). */
export function keepMismatchPatch(mismatchId: string): MismatchPatch {
  return { keptMismatches: [mismatchId], rejectedVlmBlocks: [] };
}

/** "Bỏ khối AI đọc": từng khối một. */
export function rejectBlockPatch(blockId: string): MismatchPatch {
  return { keptMismatches: [], rejectedVlmBlocks: [blockId] };
}

/** Vị trí ô để người dùng tìm trên trang (số hàng/cột do backend trả, hiển thị từ 1 như backend ghi). */
export function mismatchCellLabel(item: VlmMismatchView): string {
  if (item.row === null || item.col === null) return "Ô bị AI bỏ sót";
  return `Hàng ${item.row}, cột ${item.col}`;
}
