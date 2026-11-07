/**
 * Chỗ lệch ô bảng do AI đọc (ADR-009 mục 3.4–3.5; SPEC §6.5 `PATCH …/lines`). Logic thuần: gom chỗ lệch theo khối và
 * dựng payload. Không so lại, không tính lại giá trị nào: chuỗi hiển thị là chuỗi backend trả.
 */

import type { PageView, VlmMismatchView } from "./documents-data";

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

/** Hàm gọi `PATCH …/lines` (khớp chữ ký của `api.patchPageLines`). */
export type PatchPageLinesFn = (
  documentId: string,
  pageNumber: number,
  expectedVersion: number,
  edits: [],
  appendedLines: [],
  keptMismatches: string[],
  rejectedVlmBlocks: string[],
) => Promise<PageView>;

/** Gửi một `MismatchPatch`: không sửa dòng nào, đúng thứ tự `kept` rồi `rejected` như chữ ký của `api`. */
export function writeMismatchPatch(
  patchPageLines: PatchPageLinesFn,
  documentId: string,
  pageNumber: number,
  expectedVersion: number,
  patch: MismatchPatch,
): Promise<PageView> {
  return patchPageLines(documentId, pageNumber, expectedVersion, [], [], patch.keptMismatches, patch.rejectedVlmBlocks);
}

/** Thông báo thành công theo loại thao tác. */
export function mismatchDoneMessage(patch: MismatchPatch): string {
  return patch.rejectedVlmBlocks.length > 0 ? "Đã bỏ khối AI đọc." : "Đã giữ giá trị này.";
}

/** Trạng thái hỏi lại của panel: khối đang chờ xác nhận bỏ (nếu có). */
export interface ConfirmState {
  confirming: string | null;
}

export type ConfirmEvent =
  | { type: "keep"; mismatchId: string }
  | { type: "askReject"; blockId: string }
  | { type: "confirmReject"; blockId: string }
  | { type: "cancel" };

/**
 * Chuyển trạng thái hỏi lại. "Bỏ khối AI đọc" (`askReject`) chỉ mở bước hỏi, KHÔNG gửi gì; chỉ `confirmReject` cho
 * khối đang hỏi mới trả payload bỏ khối. `keep` gửi ngay đúng một chỗ lệch.
 */
export function reduceConfirm(
  state: ConfirmState,
  event: ConfirmEvent,
): { state: ConfirmState; patch: MismatchPatch | null } {
  switch (event.type) {
    case "keep":
      return { state: { confirming: null }, patch: keepMismatchPatch(event.mismatchId) };
    case "askReject":
      return { state: { confirming: event.blockId }, patch: null };
    case "confirmReject":
      if (state.confirming !== event.blockId) return { state, patch: null };
      return { state: { confirming: null }, patch: rejectBlockPatch(event.blockId) };
    case "cancel":
      return { state: { confirming: null }, patch: null };
  }
}
