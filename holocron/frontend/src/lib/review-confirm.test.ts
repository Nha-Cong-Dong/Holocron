/**
 * Nút xác nhận trang (I5, quyết định 2026-10-29 câu 17 A): mỗi đường lỗi đã lộ ở các vòng review có một test —
 * (a) focus rơi vào nút xác nhận khi đổi trang; (b) Shift hoặc Ctrl+K rồi Enter; (c) bấm đúp; (d) Enter/Space trên nút
 * vừa hiện; (e) ô thêm dòng còn chữ; (f) ô sửa dòng đang mở.
 */

import { describe, expect, it } from "vitest";

import type { PageStatusView, PageSummaryView } from "./documents-data";
import {
  type ActiveOnShow,
  type ConfirmClickInput,
  type ConfirmNowInput,
  canConfirmNow,
  confirmButtonBlock,
  confirmClick,
  focusOnShow,
  nextReviewTarget,
  unsavedDrafts,
} from "./review-confirm";
import { reviewKeyAction } from "./review-keys";
import { CONFIRM_GUARD_MS, endSend, reviewSessionFor } from "./review-session";

const READY: ConfirmNowInput = {
  pageBlock: null,
  hasSkippedLines: false,
  appendDraft: false,
  editing: false,
  sending: false,
  msSinceShown: CONFIRM_GUARD_MS,
};

const PAGE: ConfirmClickInput = {
  pageBlock: null,
  hasSkippedLines: false,
  appendDraft: false,
  editing: false,
  busy: false,
};

function can(change: Partial<ConfirmNowInput>): boolean {
  return canConfirmNow({ ...READY, ...change });
}

describe("canConfirmNow", () => {
  it("đủ điều kiện và đã qua CONFIRM_GUARD_MS ⇒ nhận", () => {
    expect(can({})).toBe(true);
    expect(can({ msSinceShown: CONFIRM_GUARD_MS * 10 })).toBe(true);
  });

  it.each<[string, Partial<ConfirmNowInput>]>([
    ["trang vừa hiện", { msSinceShown: 0 }],
    ["chưa đủ CONFIRM_GUARD_MS", { msSinceShown: CONFIRM_GUARD_MS - 1 }],
    ["đang gửi", { sending: true }],
    ["có dòng sai dạng bị bỏ", { hasSkippedLines: true }],
    ["trang đang đọc", { pageBlock: "pending" }],
    ["trang đọc lỗi", { pageBlock: "failed" }],
    ["trang đã xác nhận", { pageBlock: "confirmed" }],
    ["trang không có chữ", { pageBlock: "no_text" }],
    ["trạng thái lạ", { pageBlock: "unknown" }],
    ["ô thêm dòng còn chữ", { appendDraft: true }],
    ["ô sửa dòng đang mở", { editing: true }],
  ])("không nhận khi %s", (_name, change) => {
    expect(can(change)).toBe(false);
  });
});

describe("(e)(f) còn dòng chưa lưu", () => {
  it("(e) ô thêm dòng còn chữ ⇒ khoá kèm lý do 'unsaved_lines', không tự lưu hộ", () => {
    expect(confirmButtonBlock({ pageBlock: null, appendDraft: true, editing: false })).toBe("unsaved_lines");
    expect(can({ appendDraft: true, msSinceShown: CONFIRM_GUARD_MS * 100 })).toBe(false);
  });

  it("(f) ô sửa dòng đang mở ⇒ khoá kèm lý do 'unsaved_lines'", () => {
    expect(confirmButtonBlock({ pageBlock: null, appendDraft: false, editing: true })).toBe("unsaved_lines");
    expect(can({ editing: true, msSinceShown: CONFIRM_GUARD_MS * 100 })).toBe(false);
  });

  it("lý do suy từ trang đứng trước; không còn gì thì không có lý do", () => {
    expect(confirmButtonBlock({ pageBlock: "no_text", appendDraft: true, editing: false })).toBe("no_text");
    expect(confirmButtonBlock({ pageBlock: null, appendDraft: false, editing: false })).toBeNull();
  });

  it("onClick kiểm lại cùng điều kiện lúc bấm: ô thêm dòng còn chữ thì bị chặn, phiên không đổi", () => {
    const session = reviewSessionFor(null, "1:1", 0);
    const result = confirmClick(session, { ...PAGE, appendDraft: true }, CONFIRM_GUARD_MS * 10);
    expect(result.accepted).toBe(false);
    expect(result.session).toBe(session);
  });
});

describe("(d) kích hoạt nút trong CONFIRM_GUARD_MS đầu (chuột, chạm, Enter/Space trên nút đang focus)", () => {
  it("mọi kích hoạt đi qua onClick: sớm hơn CONFIRM_GUARD_MS thì bỏ, đủ thì nhận", () => {
    const session = reviewSessionFor(null, "4:2", 1000);
    expect(confirmClick(session, PAGE, 1000).accepted).toBe(false);
    expect(confirmClick(session, PAGE, 1000 + CONFIRM_GUARD_MS - 1).accepted).toBe(false);
    expect(confirmClick(session, PAGE, 1000 + CONFIRM_GUARD_MS).accepted).toBe(true);
  });

  it("trang vẽ lại sau khi lưu dòng (version đổi) ⇒ đếm lại từ đầu", () => {
    const old = reviewSessionFor(null, "4:2", 0);
    const redrawn = reviewSessionFor(old, "4:3", 10_000);
    expect(confirmClick(redrawn, PAGE, 10_000 + CONFIRM_GUARD_MS - 1).accepted).toBe(false);
  });

  it("Enter/Space trên nút không là phím tắt: chỉ còn đường onClick", () => {
    expect(
      reviewKeyAction({
        key: "Enter",
        repeat: false,
        altKey: false,
        ctrlKey: false,
        metaKey: false,
        focus: "control",
        overlayOpen: false,
        busy: false,
        hasDraft: false,
        canEdit: true,
        hasPrevious: true,
        hasNext: true,
      }),
    ).toBeNull();
  });
});

describe("(c) bấm đúp", () => {
  it("cú đầu chuyển phiên sang đang gửi ngay, cú sau bị bỏ tới khi server trả lời", () => {
    const shown = reviewSessionFor(null, "2:1", 0);
    const first = confirmClick(shown, PAGE, CONFIRM_GUARD_MS + 10);
    expect(first.accepted).toBe(true);
    expect(first.session.sending).toBe(true);
    const second = confirmClick(first.session, PAGE, CONFIRM_GUARD_MS + 20);
    expect(second.accepted).toBe(false);
    expect(second.session).toBe(first.session);
    expect(confirmClick(endSend(first.session), PAGE, CONFIRM_GUARD_MS + 500).accepted).toBe(true);
  });

  it("đang có thao tác ghi khác thì cũng bỏ", () => {
    const shown = reviewSessionFor(null, "2:1", 0);
    expect(confirmClick(shown, { ...PAGE, busy: true }, CONFIRM_GUARD_MS * 10).accepted).toBe(false);
  });
});

describe("(b) Shift hoặc Ctrl+K rồi Enter", () => {
  it("phím không mở đường xác nhận nào: nút vẫn chỉ phụ thuộc mốc thời gian và dữ liệu trang", () => {
    const keys = [
      { key: "Shift", ctrlKey: false },
      { key: "k", ctrlKey: true },
      { key: "Enter", ctrlKey: false },
    ];
    for (const { key, ctrlKey } of keys) {
      expect(
        reviewKeyAction({
          key,
          repeat: false,
          altKey: false,
          ctrlKey,
          metaKey: false,
          focus: "region",
          overlayOpen: false,
          busy: false,
          hasDraft: false,
          canEdit: true,
          hasPrevious: true,
          hasNext: true,
        }),
      ).toBeNull();
    }
    const session = reviewSessionFor(null, "1:1", 0);
    expect(confirmClick(session, PAGE, CONFIRM_GUARD_MS - 1).accepted).toBe(false);
  });
});

describe("(a) focus khi trang mới hiện", () => {
  const ACTIVE: readonly ActiveOnShow[] = ["none", "confirm", "other"];

  it("đổi trang (nút 'Sang trang…', ←/→, chọn ở danh sách) ⇒ luôn vào tiêu đề, kể cả khi focus đang ở nút xác nhận", () => {
    for (const active of ACTIVE) expect(focusOnShow({ pageChanged: true, active })).toBe("heading");
  });

  it("cùng trang vẽ lại: focus mất hay đang ở nút xác nhận ⇒ vào tiêu đề; đang ở chỗ khác thì giữ", () => {
    expect(focusOnShow({ pageChanged: false, active: "none" })).toBe("heading");
    expect(focusOnShow({ pageChanged: false, active: "confirm" })).toBe("heading");
    expect(focusOnShow({ pageChanged: false, active: "other" })).toBe("keep");
  });
});

describe("nextReviewTarget", () => {
  function page(pageNumber: number, status: PageStatusView, confirmed = false): PageSummaryView {
    return { pageNumber, status, tierUsed: null, reviewReasons: [], confirmed };
  }

  it("trang cần xem chưa xác nhận kế sau trang hiện tại, hết thì quay về trang đầu còn lại", () => {
    const pages = [page(1, "needs_review"), page(2, "needs_review", true), page(3, "ok"), page(4, "needs_review")];
    expect(nextReviewTarget({ pages }, 2)).toBe(4);
    expect(nextReviewTarget({ pages }, 4)).toBe(1);
    expect(nextReviewTarget({ pages }, 3)).toBe(4);
  });

  it("không bao giờ là chính trang hiện tại; không còn trang khác thì null", () => {
    expect(nextReviewTarget({ pages: [page(5, "needs_review")] }, 5)).toBeNull();
    expect(nextReviewTarget({ pages: [page(5, "needs_review", true), page(6, "ok")] }, 5)).toBeNull();
    expect(nextReviewTarget({ pages: [page(5, "needs_review"), page(6, "failed"), page(7, "pending")] }, 6)).toBe(5);
  });

  it("tính từ dữ liệu tài liệu đọc lại: trang vừa xác nhận không còn trong hàng đợi", () => {
    const before = [page(1, "needs_review"), page(2, "needs_review")];
    const after = [page(1, "needs_review", true), page(2, "needs_review")];
    expect(nextReviewTarget({ pages: before }, 1)).toBe(2);
    expect(nextReviewTarget({ pages: after }, 2)).toBeNull();
  });
});

describe("unsavedDrafts: nháp suy từ state của component chứa nút, cùng lần cập nhật khi gõ", () => {
  it("ô thêm dòng có chữ ⇒ nháp; trống hoặc chỉ khoảng trắng ⇒ không", () => {
    expect(unsavedDrafts({ appendText: "x", editingLine: null })).toEqual({ appendDraft: true, editing: false });
    expect(unsavedDrafts({ appendText: "  dòng  ", editingLine: null }).appendDraft).toBe(true);
    expect(unsavedDrafts({ appendText: "", editingLine: null }).appendDraft).toBe(false);
    expect(unsavedDrafts({ appendText: " 	 ", editingLine: null }).appendDraft).toBe(false);
  });

  it("ô 'Sửa' đang mở (kể cả dòng 0, chưa đổi chữ) ⇒ đang sửa", () => {
    expect(unsavedDrafts({ appendText: "", editingLine: 0 })).toEqual({ appendDraft: false, editing: true });
    expect(unsavedDrafts({ appendText: "", editingLine: 3 }).editing).toBe(true);
  });

  it("gõ một ký tự vào ô thêm dòng ⇒ chính lần tính đó khoá nút, có câu nhắc, không tự lưu", () => {
    const typed = { pageBlock: null, hasSkippedLines: false, ...unsavedDrafts({ appendText: "a", editingLine: null }) };
    expect(confirmButtonBlock(typed)).toBe("unsaved_lines");
    expect(canConfirmNow({ ...typed, sending: false, msSinceShown: CONFIRM_GUARD_MS * 10 })).toBe(false);
    const session = reviewSessionFor(null, "1:1", 0);
    const click = confirmClick(session, { ...typed, busy: false }, CONFIRM_GUARD_MS * 10);
    expect(click.accepted).toBe(false);
    expect(click.session).toBe(session);
  });

  it("mở ô 'Sửa' một dòng ⇒ chính lần tính đó khoá nút", () => {
    const opened = { pageBlock: null, hasSkippedLines: false, ...unsavedDrafts({ appendText: "", editingLine: 2 }) };
    expect(confirmButtonBlock(opened)).toBe("unsaved_lines");
    expect(
      confirmClick(reviewSessionFor(null, "1:1", 0), { ...opened, busy: false }, CONFIRM_GUARD_MS * 10).accepted,
    ).toBe(false);
  });
});
