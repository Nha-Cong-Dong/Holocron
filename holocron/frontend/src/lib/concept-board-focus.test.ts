import { describe, expect, it } from "vitest";
import {
  ACCEPT_ALL_EDITING_VI,
  bulkCandidateOf,
  bulkReducer,
  CONCEPT_CONFLICT_VI,
  editingSkippedToast,
  IDLE_BULK,
  nextReloadNote,
  reloadRetryHinted,
  renameCloseFocus,
  rowErrorText,
  screenErrorText,
} from "./concept-board";
import { classifyNetworkError } from "./errors";
import type { ActionGate } from "./pairs";

const OPEN: ActionGate = { shown: true, enabled: true };
const LOCKED: ActionGate = { shown: true, enabled: false, reason: "Chưa kiểm chứng được đoạn nguồn." };

describe("renameCloseFocus", () => {
  it("focus ngoài khung (bấm Nhận tất cả, thao tác chỗ khác) ⇒ không kéo focus", () => {
    expect(renameCloseFocus(false, false)).toBeNull();
    expect(renameCloseFocus(false, true)).toBeNull();
  });

  it("focus trong khung: thẻ đang rời đi ⇒ chính thẻ, thẻ ở lại ⇒ nút Sửa", () => {
    expect(renameCloseFocus(true, true)).toBe("card");
    expect(renameCloseFocus(true, false)).toBe("edit");
  });
});

describe("Nhận tất cả bỏ qua thẻ đang sửa tên", () => {
  it("thẻ đang mở khung sửa: bỏ qua kèm lý do, kể cả khi nút Nhận bật", () => {
    expect(bulkCandidateOf({ pair_id: "p1", version: 3 }, OPEN, true)).toEqual({
      pairId: "p1",
      version: 3,
      allowed: false,
      reason: ACCEPT_ALL_EDITING_VI,
    });
  });

  it("thẻ khác theo nút Nhận của thẻ", () => {
    expect(bulkCandidateOf({ pair_id: "p1", version: 1 }, OPEN, false)).toEqual({
      pairId: "p1",
      version: 1,
      allowed: true,
      reason: null,
    });
    expect(bulkCandidateOf({ pair_id: "p2", version: 1 }, LOCKED, false)).toMatchObject({
      allowed: false,
      reason: "Chưa kiểm chứng được đoạn nguồn.",
    });
  });

  it("hàng gửi không gửi thẻ đang sửa; có thẻ bị bỏ qua thì báo bằng thông báo", () => {
    const candidates = [
      bulkCandidateOf({ pair_id: "p1", version: 1 }, OPEN, true),
      bulkCandidateOf({ pair_id: "p2", version: 1 }, OPEN, false),
    ];
    const started = bulkReducer(IDLE_BULK, { type: "start", candidates });
    expect(started.items.map((item) => item.status)).toEqual(["skipped", "queued"]);
    expect(editingSkippedToast(candidates)).toBe(ACCEPT_ALL_EDITING_VI);
    expect(editingSkippedToast([bulkCandidateOf({ pair_id: "p2", version: 1 }, LOCKED, false)])).toBeNull();
    expect(ACCEPT_ALL_EDITING_VI).not.toMatch(/Thử lại/);
  });
});

describe("rowErrorText", () => {
  const network = screenErrorText(classifyNetworkError("http://127.0.0.1:8000"));

  it("khung sửa đóng: dòng có nút Thử lại nên giữ nguyên câu", () => {
    expect(rowErrorText(network, false)).toBe(network);
    expect(rowErrorText(CONCEPT_CONFLICT_VI, false)).toBe(CONCEPT_CONFLICT_VI);
  });

  it("khung sửa mở: không có nút Thử lại, câu nhắc nút Lưu của khung", () => {
    expect(network).toContain("bấm Thử lại");
    expect(rowErrorText(network, true)).not.toContain("Thử lại");
    expect(rowErrorText(network, true)).toContain("bấm Lưu");
    expect(rowErrorText(CONCEPT_CONFLICT_VI, true)).toBe(
      "Khái niệm này vừa đổi ở nơi khác nên chưa ghi được. Xem lại rồi bấm Lưu.",
    );
    expect(rowErrorText("Holocron trả lời không như mong đợi. Bấm Thử lại; nếu vẫn lỗi, mở lại Holocron.", true)).toBe(
      "Holocron trả lời không như mong đợi. Bấm Lưu; nếu vẫn lỗi, mở lại Holocron.",
    );
  });
});

describe("reloadRetryHinted", () => {
  it("chỉ nhắc Thử lại khi dòng báo đang hiện và không có lượt tải lại đang chạy", () => {
    const note = nextReloadNote(null, "Chưa cập nhật được danh sách khái niệm.");
    expect(reloadRetryHinted(note, false)).toBe(true);
    expect(reloadRetryHinted(note, true)).toBe(false);
    expect(reloadRetryHinted(null, false)).toBe(false);
    expect(reloadRetryHinted(null, true)).toBe(false);
  });
});
