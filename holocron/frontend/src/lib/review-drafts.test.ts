/**
 * Nháp chưa lưu theo trang ở màn Xem lại (I5; quyết định 2026-10-29 "Lỗi ô thêm dòng" và "PR 5 — nút Xác nhận các
 * trang đọc tốt"): nháp không mất khi vùng trang vẽ lại hay đổi trang; còn nháp ở trang nào thì nút xác nhận cả tài
 * liệu khoá kèm đúng dòng nhắc.
 */

import { describe, expect, it } from "vitest";

import { CONFIRM_PAGE_BLOCK_MESSAGE_VI } from "./documents";
import type { DocumentView, PageStatusView, PageSummaryView } from "./documents-data";
import {
  anyPageHasDraft,
  bulkConfirmBlock,
  closeLineEdit,
  DRAFT_PAGE_LABEL_VI,
  DRAFT_PAGES_LISTED,
  draftFor,
  draftPagesNote,
  EMPTY_PAGE_DRAFT,
  type LineEditDraft,
  lineEditState,
  NO_DRAFTS,
  pageHasDraft,
  pagesWithDrafts,
  setAppendText,
  setLineEditText,
  startLineEdit,
} from "./review-drafts";

function page(pageNumber: number, status: PageStatusView = "needs_review", confirmed = false): PageSummaryView {
  return { pageNumber, status, tierUsed: null, reviewReasons: [], confirmed };
}

function doc(pages: PageSummaryView[], status: DocumentView["status"] = "review") {
  return { status, pages };
}

describe("giữ nháp theo trang", () => {
  it("trang chưa có nháp ⇒ nháp rỗng", () => {
    expect(draftFor(NO_DRAFTS, 3)).toEqual(EMPTY_PAGE_DRAFT);
    expect(pageHasDraft(EMPTY_PAGE_DRAFT)).toBe(false);
  });

  it("ô thêm dòng: nháp nằm theo số trang, không theo phiên bản, nên vẽ lại (version tăng) vẫn còn", () => {
    const drafts = setAppendText(NO_DRAFTS, 2, "dòng bỏ sót");
    // Vùng trang mount lại sau khi lưu dòng khác: đọc lại theo đúng số trang.
    expect(draftFor(drafts, 2).appendText).toBe("dòng bỏ sót");
    expect(pageHasDraft(draftFor(drafts, 2))).toBe(true);
  });

  it("đổi trang rồi quay lại: nháp của từng trang còn nguyên, trang khác không bị ảnh hưởng", () => {
    let drafts = setAppendText(NO_DRAFTS, 1, "a");
    drafts = startLineEdit(drafts, 4, 7, "chữ cũ");
    drafts = setLineEditText(drafts, 4, "chữ mới");
    expect(draftFor(drafts, 1)).toEqual({ appendText: "a", editing: null });
    expect(draftFor(drafts, 4)).toEqual({
      appendText: "",
      editing: { lineIndex: 7, text: "chữ mới", baseText: "chữ cũ" },
    });
    expect(draftFor(drafts, 2)).toEqual(EMPTY_PAGE_DRAFT);
  });

  it("không sửa bản cũ", () => {
    const before = setAppendText(NO_DRAFTS, 1, "a");
    const after = setAppendText(before, 1, "ab");
    expect(draftFor(before, 1).appendText).toBe("a");
    expect(draftFor(after, 1).appendText).toBe("ab");
    expect(NO_DRAFTS.size).toBe(0);
  });

  it("ô sửa mở mà chưa đổi chữ vẫn là nháp; chỉ khoảng trắng ở ô thêm dòng thì không", () => {
    expect(pageHasDraft(draftFor(startLineEdit(NO_DRAFTS, 1, 0, "y"), 1))).toBe(true);
    expect(pageHasDraft(draftFor(setAppendText(NO_DRAFTS, 1, "   "), 1))).toBe(false);
  });

  it("xoá hết chữ và đóng ô sửa ⇒ trang rời bảng nháp", () => {
    let drafts = setAppendText(NO_DRAFTS, 1, "a");
    drafts = startLineEdit(drafts, 1, 0, "x");
    drafts = setAppendText(drafts, 1, "");
    expect(drafts.has(1)).toBe(true);
    drafts = closeLineEdit(drafts, 1, 0);
    expect(drafts.has(1)).toBe(false);
  });

  it("closeLineEdit chỉ đóng ô của đúng dòng; ô đang mở cho dòng khác giữ nguyên", () => {
    const drafts = startLineEdit(NO_DRAFTS, 1, 5, "x");
    expect(closeLineEdit(drafts, 1, 2)).toBe(drafts);
    expect(draftFor(closeLineEdit(drafts, 1, 5), 1).editing).toBeNull();
  });

  it("setLineEditText khi không mở ô nào ⇒ giữ nguyên", () => {
    const drafts = setAppendText(NO_DRAFTS, 1, "a");
    expect(setLineEditText(drafts, 1, "z")).toBe(drafts);
  });

  it("mở ô sửa giữ chữ của ô thêm dòng", () => {
    const drafts = startLineEdit(setAppendText(NO_DRAFTS, 1, "a"), 1, 3, "t");
    expect(draftFor(drafts, 1)).toEqual({ appendText: "a", editing: { lineIndex: 3, text: "t", baseText: "t" } });
  });
});

describe("nháp ở bất kỳ trang nào", () => {
  const pages = [page(1, "ok"), page(2), page(3, "ok", true), page(4, "failed")];

  it("liệt kê trang còn nháp theo thứ tự trang của tài liệu", () => {
    let drafts = setAppendText(NO_DRAFTS, 2, "b");
    drafts = startLineEdit(drafts, 1, 0, "a");
    expect(pagesWithDrafts(drafts, pages)).toEqual([1, 2]);
    expect(anyPageHasDraft(drafts, pages)).toBe(true);
    expect(anyPageHasDraft(NO_DRAFTS, pages)).toBe(false);
  });

  it("trang không còn sửa được (đã xác nhận, đọc lỗi) hay không thuộc tài liệu thì nháp không tính", () => {
    let drafts = setAppendText(NO_DRAFTS, 3, "c");
    drafts = setAppendText(drafts, 4, "d");
    drafts = setAppendText(drafts, 9, "z");
    expect(pagesWithDrafts(drafts, pages)).toEqual([]);
  });

  it("chỉ khoảng trắng ở ô thêm dòng không tính", () => {
    expect(anyPageHasDraft(setAppendText(NO_DRAFTS, 1, " \t"), pages)).toBe(false);
  });
});

describe("bulkConfirmBlock — nút 'Xác nhận các trang đọc tốt'", () => {
  const pages = [page(1, "ok"), page(2)];

  it("không còn nháp ⇒ theo lý do của tài liệu", () => {
    expect(bulkConfirmBlock(doc(pages), NO_DRAFTS)).toBeNull();
    expect(bulkConfirmBlock(doc([page(1, "ok", true), page(2)]), NO_DRAFTS)).toBe("nothing_to_confirm");
  });

  it("còn nháp ở trang khác trang đang xem (cả trang needs_review) ⇒ khoá 'unsaved_lines'", () => {
    expect(bulkConfirmBlock(doc(pages), setAppendText(NO_DRAFTS, 2, "x"))).toBe("unsaved_lines");
    expect(bulkConfirmBlock(doc(pages), startLineEdit(NO_DRAFTS, 1, 0, "y"))).toBe("unsaved_lines");
  });

  it("lý do của tài liệu đứng trước", () => {
    const drafts = setAppendText(NO_DRAFTS, 1, "x");
    expect(bulkConfirmBlock(doc(pages, "processing"), drafts)).toBe("processing");
  });

  it("lưu hoặc xoá hết nháp ⇒ mở lại", () => {
    let drafts = setAppendText(NO_DRAFTS, 2, "x");
    drafts = setAppendText(drafts, 2, "");
    expect(bulkConfirmBlock(doc(pages), drafts)).toBeNull();
  });

  it("dòng nhắc dùng đúng chữ của quyết định", () => {
    expect(CONFIRM_PAGE_BLOCK_MESSAGE_VI.unsaved_lines).toBe("Còn dòng chưa lưu. Lưu hoặc xoá trước khi xác nhận");
  });
});

describe("lineEditState — dữ liệu trang tải lại khi ô 'Sửa' đang mở (409, poll, lưu dòng khác)", () => {
  function opened(text: string): LineEditDraft {
    const editing = draftFor(startLineEdit(NO_DRAFTS, 1, 2, text), 1).editing;
    if (editing === null) throw new Error("ô sửa phải mở");
    return editing;
  }
  const lines = (text: string) => [
    { lineIndex: 1, text: "dòng một" },
    { lineIndex: 2, text },
  ];

  it("mở ô sửa ghi lại chữ của dòng lúc mở; gõ thêm không đổi chữ đó", () => {
    const drafts = setLineEditText(startLineEdit(NO_DRAFTS, 1, 2, "gốc"), 1, "đang gõ");
    expect(draftFor(drafts, 1).editing).toEqual({ lineIndex: 2, text: "đang gõ", baseText: "gốc" });
  });

  it("dòng không đổi ⇒ giữ ô sửa và chữ đang gõ", () => {
    const editing = { ...opened("gốc"), text: "đang gõ" };
    expect(lineEditState(editing, lines("gốc"))).toEqual({ kind: "open" });
  });

  it("dòng không đổi nhưng khác dạng Unicode (NFD) ⇒ vẫn coi là không đổi", () => {
    const editing = { ...opened("Tiếng Việt".normalize("NFC")), text: "x" };
    expect(lineEditState(editing, lines("Tiếng Việt".normalize("NFD")))).toEqual({ kind: "open" });
  });

  it("dòng đổi ở nơi khác ⇒ xung đột, kèm chữ mới của dòng", () => {
    const editing = { ...opened("gốc"), text: "đang gõ" };
    expect(lineEditState(editing, lines("người khác sửa"))).toEqual({
      kind: "changed",
      currentText: "người khác sửa",
    });
  });

  it("mở mà chưa gõ gì, dòng đổi ở nơi khác ⇒ vẫn xung đột (không cho Lưu ghi đè)", () => {
    expect(lineEditState(opened("gốc"), lines("bản mới"))).toEqual({ kind: "changed", currentText: "bản mới" });
  });

  it("dòng biến mất ⇒ xung đột 'removed'", () => {
    const editing = { ...opened("gốc"), text: "đang gõ" };
    expect(lineEditState(editing, [{ lineIndex: 1, text: "dòng một" }])).toEqual({ kind: "removed" });
    expect(lineEditState(editing, [])).toEqual({ kind: "removed" });
  });

  it("dòng mang đúng chữ người dùng gõ (bản trả về ngay sau khi chính ô này lưu) ⇒ không báo xung đột", () => {
    const editing = { ...opened("gốc"), text: "đã sửa" };
    expect(lineEditState(editing, lines("đã sửa"))).toEqual({ kind: "open" });
  });

  it("xung đột vẫn là nháp chưa lưu: khoá nút xác nhận cho tới khi bỏ chữ đã gõ hoặc sửa lại từ bản mới", () => {
    const pages = [page(1, "ok")];
    let drafts = setLineEditText(startLineEdit(NO_DRAFTS, 1, 2, "gốc"), 1, "đang gõ");
    expect(bulkConfirmBlock(doc(pages), drafts)).toBe("unsaved_lines");
    // "Sửa lại từ bản mới": mở lại với chữ mới của dòng, chữ đó thành mốc mới.
    const restarted = startLineEdit(drafts, 1, 2, "bản mới");
    const editing = draftFor(restarted, 1).editing;
    expect(editing).toEqual({ lineIndex: 2, text: "bản mới", baseText: "bản mới" });
    if (editing !== null) expect(lineEditState(editing, lines("bản mới"))).toEqual({ kind: "open" });
    expect(bulkConfirmBlock(doc(pages), restarted)).toBe("unsaved_lines");
    // "Bỏ chữ đã gõ": đóng ô, hết nháp.
    drafts = closeLineEdit(drafts, 1, 2);
    expect(bulkConfirmBlock(doc(pages), drafts)).toBeNull();
  });
});

describe("draftPagesNote — câu liệt kê trang còn nháp dưới nút 'Xác nhận các trang đọc tốt'", () => {
  it("không có trang nào ⇒ null", () => {
    expect(draftPagesNote([])).toBeNull();
  });

  it("một trang và nhiều trang, giữ thứ tự được truyền", () => {
    expect(draftPagesNote([2])).toBe("Trang 2 còn dòng chưa lưu.");
    expect(draftPagesNote([2, 5])).toBe("Trang 2, 5 còn dòng chưa lưu.");
  });

  it(`quá ${DRAFT_PAGES_LISTED} trang ⇒ liệt kê ${DRAFT_PAGES_LISTED} trang đầu rồi 'và N trang khác'`, () => {
    const pages = Array.from({ length: DRAFT_PAGES_LISTED + 3 }, (_, index) => index + 1);
    expect(draftPagesNote(pages)).toBe("Trang 1, 2, 3, 4, 5, 6, 7, 8 và 3 trang khác còn dòng chưa lưu.");
    expect(draftPagesNote(pages.slice(0, DRAFT_PAGES_LISTED))).toBe("Trang 1, 2, 3, 4, 5, 6, 7, 8 còn dòng chưa lưu.");
  });

  it("nhãn trong danh sách trang là chữ, không chỉ màu", () => {
    expect(DRAFT_PAGE_LABEL_VI).toBe("còn dòng chưa lưu");
  });
});
