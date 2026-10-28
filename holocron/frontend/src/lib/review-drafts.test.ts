/**
 * Nháp chưa lưu theo trang ở màn Xem lại (I5; quyết định 2026-10-29 "Lỗi ô thêm dòng" và "PR 5 — nút Xác nhận các
 * trang đọc tốt"): nháp không mất khi vùng trang vẽ lại hay đổi trang; còn nháp ở trang nào thì nút xác nhận cả tài
 * liệu khoá kèm đúng dòng nhắc.
 */

import { describe, expect, it } from "vitest";

import { CONFIRM_PAGE_BLOCK_MESSAGE_VI, type EditBlock, lineEditBlock } from "./documents";
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
  lineEditDenial,
  lineEditLock,
  lineEditState,
  lockedDraftNotice,
  NO_ANNOUNCEMENT,
  NO_DRAFTS,
  nextAnnouncement,
  pageDraftStatusMessage,
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

/** Trang đang hiện cho `pageDraftStatusMessage`: dòng 4 mang chữ `text` (`null` thì trang không có dòng nào). */
function shownPage(pageNumber: number, text: string | null, confirmed = false, status: PageStatusView = "ok") {
  return { pageNumber, status, confirmed, lines: text === null ? [] : [{ lineIndex: 4, text }] };
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

  it("trang không thuộc tài liệu thì nháp không tính (không có trang nào để hiện nháp đó)", () => {
    expect(pagesWithDrafts(setAppendText(NO_DRAFTS, 9, "z"), pages)).toEqual([]);
  });

  it("trang không còn sửa được (đã xác nhận, đọc lỗi) mà còn nháp: vẫn hiện câu báo nên vẫn tính", () => {
    let drafts = setAppendText(NO_DRAFTS, 3, "c");
    drafts = startLineEdit(drafts, 4, 0, "d");
    expect(pagesWithDrafts(drafts, pages)).toEqual([3, 4]);
    expect(lockedDraftNotice(draftFor(drafts, 3), lineEditBlock(page(3, "ok", true)))).toEqual({
      editing: null,
      appendText: "c",
    });
    expect(lockedDraftNotice(draftFor(drafts, 4), lineEditBlock(page(4, "failed")))).toEqual({
      editing: { lineIndex: 0, text: "d", baseText: "d" },
      appendText: null,
    });
  });

  it("trang có trong danh sách nháp ⇔ giao diện còn hiện nháp (ô trên trang sửa được, câu báo trên trang bị khoá)", () => {
    const blocks: (EditBlock | null)[] = [null, "confirmed", "failed", "pending", "unknown"];
    const samples = [
      EMPTY_PAGE_DRAFT,
      draftFor(setAppendText(NO_DRAFTS, 1, " \t"), 1),
      draftFor(setAppendText(NO_DRAFTS, 1, "a"), 1),
      draftFor(startLineEdit(NO_DRAFTS, 1, 0, "x"), 1),
    ];
    for (const block of blocks) {
      for (const sample of samples) {
        const drafts = new Map([[1, sample]]);
        const listed = pagesWithDrafts(drafts, [page(1)]).includes(1);
        // Trang sửa được hiện nháp ngay trong ô (thêm dòng, sửa, xung đột); trang bị khoá hiện `lockedDraftNotice`.
        const shown = block === null ? pageHasDraft(sample) : lockedDraftNotice(sample, block) !== null;
        expect(listed).toBe(shown);
      }
    }
  });

  it("trang còn sửa được thì không có câu báo khoá (nháp hiện trong ô)", () => {
    expect(lockedDraftNotice(draftFor(setAppendText(NO_DRAFTS, 1, "a"), 1), null)).toBeNull();
    expect(lockedDraftNotice(EMPTY_PAGE_DRAFT, "confirmed")).toBeNull();
    expect(lockedDraftNotice(draftFor(setAppendText(NO_DRAFTS, 1, "  "), 1), "confirmed")).toBeNull();
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
    expect(lineEditState(editing, lines("gốc"), null)).toEqual({ kind: "open" });
  });

  it("dòng không đổi nhưng khác dạng Unicode (NFD) ⇒ vẫn coi là không đổi", () => {
    const editing = { ...opened("Tiếng Việt".normalize("NFC")), text: "x" };
    expect(lineEditState(editing, lines("Tiếng Việt".normalize("NFD")), null)).toEqual({ kind: "open" });
  });

  it("chữ lúc mở ở dạng NFD, dòng ở dạng NFC cùng chữ ⇒ không xung đột", () => {
    const editing = { ...opened("Tiếng Việt".normalize("NFD")), text: "x" };
    expect(lineEditState(editing, lines("Tiếng Việt".normalize("NFC")), null)).toEqual({ kind: "open" });
  });

  it("dòng đổi ở nơi khác ⇒ xung đột", () => {
    const editing = { ...opened("gốc"), text: "đang gõ" };
    expect(lineEditState(editing, lines("người khác sửa"), null)).toEqual({ kind: "changed" });
  });

  it("mở mà chưa gõ gì, dòng đổi ở nơi khác ⇒ vẫn xung đột (không cho Lưu ghi đè)", () => {
    expect(lineEditState(opened("gốc"), lines("bản mới"), null)).toEqual({ kind: "changed" });
  });

  it("dòng biến mất ⇒ xung đột 'removed'", () => {
    const editing = { ...opened("gốc"), text: "đang gõ" };
    expect(lineEditState(editing, [{ lineIndex: 1, text: "dòng một" }], null)).toEqual({ kind: "removed" });
    expect(lineEditState(editing, [], null)).toEqual({ kind: "removed" });
  });

  it("trang không còn sửa được ⇒ 'locked', đứng trước mọi so sánh dòng", () => {
    const editing = { ...opened("gốc"), text: "đang gõ" };
    for (const block of ["confirmed", "failed", "pending", "unknown"] as const) {
      expect(lineEditState(editing, lines("gốc"), block)).toEqual({ kind: "locked" });
      expect(lineEditState(editing, [], block)).toEqual({ kind: "locked" });
    }
  });

  it("dòng mang đúng chữ người dùng gõ (bản trả về ngay sau khi chính ô này lưu) ⇒ nháp khớp server, đóng ô sửa", () => {
    let drafts = setLineEditText(startLineEdit(NO_DRAFTS, 1, 2, "gốc"), 1, "đã sửa");
    const editing = draftFor(drafts, 1).editing;
    if (editing === null) throw new Error("ô sửa phải mở");
    expect(lineEditState(editing, lines("đã sửa"), null)).toEqual({ kind: "matched" });
    // `PagePanel` đóng ô sửa của đúng dòng đó: hết nháp, nút xác nhận mở lại.
    drafts = closeLineEdit(drafts, 1, editing.lineIndex);
    expect(pageHasDraft(draftFor(drafts, 1))).toBe(false);
    expect(bulkConfirmBlock(doc([page(1, "ok")]), drafts)).toBeNull();
  });

  it("chữ đang gõ ở dạng NFD trùng dòng NFC vừa lưu ⇒ cũng là khớp server", () => {
    const editing = { ...opened("gốc"), text: "Tiếng Việt".normalize("NFD") };
    expect(lineEditState(editing, lines("Tiếng Việt".normalize("NFC")), null)).toEqual({ kind: "matched" });
  });

  it("sau khi khớp và đóng, sửa tiếp lấy chữ mới làm mốc: gõ thêm không thành xung đột, không mất chữ", () => {
    let drafts = closeLineEdit(setLineEditText(startLineEdit(NO_DRAFTS, 1, 2, "gốc"), 1, "đã sửa"), 1, 2);
    drafts = setLineEditText(startLineEdit(drafts, 1, 2, "đã sửa"), 1, "đã sửa thêm");
    const editing = draftFor(drafts, 1).editing;
    expect(editing).toEqual({ lineIndex: 2, text: "đã sửa thêm", baseText: "đã sửa" });
    if (editing !== null) expect(lineEditState(editing, lines("đã sửa"), null)).toEqual({ kind: "open" });
  });

  it("không trạng thái nào làm mất chữ đang gõ: còn mốc cũ mà chữ khác cả hai ⇒ xung đột, chữ còn nguyên", () => {
    const drafts = setLineEditText(startLineEdit(NO_DRAFTS, 1, 2, "gốc"), 1, "đã sửa thêm");
    const editing = draftFor(drafts, 1).editing;
    if (editing === null) throw new Error("ô sửa phải mở");
    expect(lineEditState(editing, lines("đã sửa"), null)).toEqual({ kind: "changed" });
    expect(editing.text).toBe("đã sửa thêm");
    expect(bulkConfirmBlock(doc([page(1, "ok")]), drafts)).toBe("unsaved_lines");
  });

  it("xung đột 'changed' vẫn là nháp chưa lưu: khoá nút xác nhận cho tới khi bỏ chữ đã gõ hoặc sửa lại từ bản mới", () => {
    const pages = [page(1, "ok")];
    let drafts = setLineEditText(startLineEdit(NO_DRAFTS, 1, 2, "gốc"), 1, "đang gõ");
    const conflicted = draftFor(drafts, 1).editing;
    if (conflicted === null) throw new Error("ô sửa phải mở");
    // Dựng đúng trạng thái xung đột trước khi kiểm nút.
    expect(lineEditState(conflicted, lines("bản mới"), null)).toEqual({ kind: "changed" });
    expect(bulkConfirmBlock(doc(pages), drafts)).toBe("unsaved_lines");
    // "Sửa lại từ bản mới": mở lại với chữ mới của dòng, chữ đó thành mốc mới.
    const restarted = startLineEdit(drafts, 1, 2, "bản mới");
    const editing = draftFor(restarted, 1).editing;
    expect(editing).toEqual({ lineIndex: 2, text: "bản mới", baseText: "bản mới" });
    if (editing !== null) expect(lineEditState(editing, lines("bản mới"), null)).toEqual({ kind: "open" });
    expect(bulkConfirmBlock(doc(pages), restarted)).toBe("unsaved_lines");
    // "Bỏ chữ đã gõ": đóng ô, hết nháp.
    drafts = closeLineEdit(drafts, 1, 2);
    expect(bulkConfirmBlock(doc(pages), drafts)).toBeNull();
  });
});

describe("pageDraftStatusMessage — vùng thông báo cố định của trang", () => {
  const editingDraft = draftFor(setLineEditText(startLineEdit(NO_DRAFTS, 1, 4, "gốc"), 1, "đang gõ"), 1);
  const appendDraft = draftFor(setAppendText(NO_DRAFTS, 1, "a"), 1);

  it("không có gì cần báo ⇒ chuỗi rỗng (vùng vẫn có trong trang)", () => {
    expect(pageDraftStatusMessage(shownPage(1, "gốc"), EMPTY_PAGE_DRAFT)).toBe("");
    // Ô sửa mở, dòng còn nguyên chữ lúc mở.
    expect(pageDraftStatusMessage(shownPage(1, "gốc"), editingDraft)).toBe("");
    // Dòng đã mang đúng chữ đang gõ (nháp khớp server).
    expect(pageDraftStatusMessage(shownPage(1, "đang gõ"), editingDraft)).toBe("");
    expect(pageDraftStatusMessage(shownPage(1, "gốc"), appendDraft)).toBe("");
  });

  it("xung đột và trang bị khoá ⇒ câu báo, không đọc lại chữ đã gõ", () => {
    expect(pageDraftStatusMessage(shownPage(1, "bản mới"), editingDraft)).toBe(
      "Dòng 4 vừa được sửa ở nơi khác. Chữ bạn đang gõ chưa lưu.",
    );
    expect(pageDraftStatusMessage(shownPage(1, null), editingDraft)).toBe(
      "Dòng 4 không còn trên trang này. Chữ bạn đang gõ chưa lưu.",
    );
    expect(pageDraftStatusMessage(shownPage(1, "gốc", true), editingDraft)).toBe(
      "Trang 1 không sửa được nữa. Chữ bạn đã gõ chưa lưu.",
    );
    expect(pageDraftStatusMessage(shownPage(1, "gốc", true), appendDraft)).toBe(
      "Trang 1 không sửa được nữa. Chữ bạn đã gõ chưa lưu.",
    );
  });

  it("câu báo cấp trang mang số trang: hai trang cùng bị khoá ra hai câu khác nhau", () => {
    expect(pageDraftStatusMessage(shownPage(7, null, false, "failed"), appendDraft)).toBe(
      "Trang 7 không sửa được nữa. Chữ bạn đã gõ chưa lưu.",
    );
    expect(pageDraftStatusMessage(shownPage(2, null, true), appendDraft)).not.toBe(
      pageDraftStatusMessage(shownPage(3, null, true), appendDraft),
    );
  });
});

describe("khoá nút 'Sửa' của dòng khác khi trang còn ô sửa (không mất nháp im lặng)", () => {
  const otherLine = { lineIndex: 2, text: "hai" };
  const lines = [otherLine, { lineIndex: 4, text: "gốc" }];
  const editingDraft = draftFor(setLineEditText(startLineEdit(NO_DRAFTS, 1, 4, "gốc"), 1, "đang gõ"), 1);
  const openReason = "Đang sửa dòng 4. Lưu hoặc huỷ trước khi sửa dòng khác.";
  const conflictReason = "Dòng 4 còn chữ chưa lưu. Sửa lại hoặc bỏ chữ đã gõ trước.";
  const discardReason = "Dòng 4 còn chữ chưa lưu. Bỏ chữ đã gõ trước khi sửa dòng khác.";

  it("không có ô sửa (kể cả chỉ có chữ ở ô thêm dòng) ⇒ không khoá dòng nào", () => {
    expect(lineEditLock(EMPTY_PAGE_DRAFT, lines, null)).toBeNull();
    expect(lineEditLock(draftFor(setAppendText(NO_DRAFTS, 1, "a"), 1), lines, null)).toBeNull();
    expect(lineEditDenial(null, 2)).toBeNull();
  });

  it("ô sửa đang mở ⇒ dòng khác khoá kèm lý do, chính dòng đó không khoá", () => {
    const lock = lineEditLock(editingDraft, lines, null);
    expect(lock).toEqual({ lineIndex: 4, reason: openReason });
    expect(lineEditDenial(lock, 2)).toBe(openReason);
    expect(lineEditDenial(lock, 4)).toBeNull();
  });

  it("mở ô mà chưa đổi chữ vẫn khoá dòng khác", () => {
    const lock = lineEditLock(draftFor(startLineEdit(NO_DRAFTS, 1, 4, "gốc"), 1), lines, null);
    expect(lineEditDenial(lock, 2)).toBe(openReason);
  });

  it("dòng đổi ở nơi khác (changed) ⇒ dòng khác khoá; dòng đó vẫn được 'Sửa lại từ bản mới'", () => {
    const lock = lineEditLock(editingDraft, [otherLine, { lineIndex: 4, text: "bản mới" }], null);
    expect(lineEditDenial(lock, 2)).toBe(conflictReason);
    expect(lineEditDenial(lock, 4)).toBeNull();
  });

  it("dòng biến mất (removed) hay trang bị khoá còn nháp sửa (locked) ⇒ dòng khác khoá, chỉ còn bỏ chữ đã gõ", () => {
    expect(lineEditDenial(lineEditLock(editingDraft, [otherLine], null), 2)).toBe(discardReason);
    expect(lineEditDenial(lineEditLock(editingDraft, lines, "confirmed"), 2)).toBe(discardReason);
  });

  it("nháp đã khớp server (matched, ô sắp đóng) ⇒ không khoá", () => {
    expect(lineEditLock(editingDraft, [otherLine, { lineIndex: 4, text: "đang gõ" }], null)).toBeNull();
  });

  it("startLineEdit không thay ô sửa của dòng khác: nháp giữ nguyên", () => {
    const drafts = setLineEditText(startLineEdit(NO_DRAFTS, 1, 4, "gốc"), 1, "đang gõ");
    expect(startLineEdit(drafts, 1, 2, "hai")).toBe(drafts);
    // Cùng dòng ("Sửa lại từ bản mới") thì thay; trang khác không bị ảnh hưởng.
    expect(draftFor(startLineEdit(drafts, 1, 4, "bản mới"), 1).editing).toEqual({
      lineIndex: 4,
      text: "bản mới",
      baseText: "bản mới",
    });
    expect(draftFor(startLineEdit(drafts, 2, 2, "x"), 2).editing).toEqual({ lineIndex: 2, text: "x", baseText: "x" });
  });

  it("đóng ô sửa xong ⇒ dòng khác mở được", () => {
    let drafts = setLineEditText(startLineEdit(NO_DRAFTS, 1, 4, "gốc"), 1, "đang gõ");
    drafts = closeLineEdit(drafts, 1, 4);
    expect(lineEditLock(draftFor(drafts, 1), lines, null)).toBeNull();
    expect(draftFor(startLineEdit(drafts, 1, 2, "hai"), 1).editing?.lineIndex).toBe(2);
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
    expect(draftPagesNote(pages.slice(0, DRAFT_PAGES_LISTED + 1))).toBe(
      "Trang 1, 2, 3, 4, 5, 6, 7, 8 và 1 trang khác còn dòng chưa lưu.",
    );
  });

  it("nhãn trong danh sách trang là chữ, không chỉ màu", () => {
    expect(DRAFT_PAGE_LABEL_VI).toBe("còn dòng chưa lưu");
  });
});

describe("setAppendText giữ nguyên chữ đang gõ", () => {
  it("không cắt khoảng trắng cuối", () => {
    expect(draftFor(setAppendText(NO_DRAFTS, 1, "abc "), 1).appendText).toBe("abc ");
  });

  it("chỉ có khoảng trắng: giữ nguyên chữ nhưng không tính là nháp", () => {
    const drafts = setAppendText(NO_DRAFTS, 1, "   ");
    expect(draftFor(drafts, 1).appendText).toBe("   ");
    expect(pageHasDraft(draftFor(drafts, 1))).toBe(false);
  });
});

describe("nháp chỉ có khoảng trắng không tính là nháp", () => {
  it("lockedDraftNotice: ô thêm dòng chỉ có khoảng trắng ⇒ appendText null, ô sửa đang mở giữ nguyên", () => {
    const editing: LineEditDraft = { lineIndex: 0, text: "d", baseText: "d" };
    expect(lockedDraftNotice({ appendText: "   ", editing }, "confirmed")).toEqual({ editing, appendText: null });
  });

  it("pageDraftStatusMessage: trang bị khoá mà không có nháp (hoặc nháp chỉ khoảng trắng) ⇒ chuỗi rỗng", () => {
    expect(pageDraftStatusMessage(shownPage(1, null, true), EMPTY_PAGE_DRAFT)).toBe("");
    expect(pageDraftStatusMessage(shownPage(1, null, false, "failed"), { appendText: " \t", editing: null })).toBe("");
  });

  it("bulkConfirmBlock: nháp chỉ khoảng trắng hoặc nháp của trang không thuộc tài liệu ⇒ không khoá", () => {
    const pages = [page(1, "ok"), page(2)];
    expect(bulkConfirmBlock(doc(pages), setAppendText(NO_DRAFTS, 1, " \t"))).toBeNull();
    expect(bulkConfirmBlock(doc(pages), setAppendText(NO_DRAFTS, 9, "z"))).toBeNull();
  });
});

describe("vùng thông báo cố định ở PageReview", () => {
  const conflict = "Dòng 4 vừa được sửa ở nơi khác. Chữ bạn đang gõ chưa lưu.";
  const onPage1 = { pageNumber: 1, text: conflict };

  it("pageDraftStatusMessage tự đối chiếu ô sửa với dòng và lý do khoá của trang", () => {
    const draft = draftFor(setLineEditText(startLineEdit(NO_DRAFTS, 1, 4, "gốc"), 1, "đang gõ"), 1);
    expect(pageDraftStatusMessage(shownPage(1, "gốc"), draft)).toBe("");
    expect(pageDraftStatusMessage(shownPage(1, "bản mới"), draft)).toBe(conflict);
    expect(pageDraftStatusMessage(shownPage(1, null), draft)).toBe(
      "Dòng 4 không còn trên trang này. Chữ bạn đang gõ chưa lưu.",
    );
    expect(pageDraftStatusMessage(shownPage(1, "gốc", true), draft)).toBe(
      "Trang 1 không sửa được nữa. Chữ bạn đã gõ chưa lưu.",
    );
    expect(pageDraftStatusMessage(shownPage(1, null, true), EMPTY_PAGE_DRAFT)).toBe("");
  });

  it("câu đổi ⇒ chữ mới; câu về rỗng ⇒ xoá chữ", () => {
    expect(nextAnnouncement({ pageNumber: 1, text: "" }, { pageNumber: 1, message: conflict })).toEqual(onPage1);
    expect(nextAnnouncement(onPage1, { pageNumber: 1, message: "" })).toEqual({ pageNumber: 1, text: "" });
  });

  it("ghi không liên quan (câu như cũ) ⇒ giữ đúng chữ cũ, không đọc lại", () => {
    expect(nextAnnouncement(onPage1, { pageNumber: 1, message: conflict })).toBe(onPage1);
  });

  it("tải lại CÙNG trang (đang tải hoặc lỗi, chưa có câu) ⇒ giữ chữ cũ, không xoá rồi đọc lại khi tải xong", () => {
    expect(nextAnnouncement(onPage1, { pageNumber: 1, message: null })).toBe(onPage1);
    const empty = { pageNumber: 1, text: "" };
    expect(nextAnnouncement(empty, { pageNumber: 1, message: null })).toBe(empty);
  });

  it("sang trang khác còn đang tải ⇒ bỏ chữ của trang cũ ngay", () => {
    expect(nextAnnouncement(onPage1, { pageNumber: 2, message: null })).toEqual({ pageNumber: 2, text: "" });
  });

  it("sang trang khác đã hiện ⇒ lấy câu của trang mới, kể cả rỗng", () => {
    expect(nextAnnouncement(onPage1, { pageNumber: 2, message: "" })).toEqual({ pageNumber: 2, text: "" });
    const locked2 = "Trang 2 không sửa được nữa. Chữ bạn đã gõ chưa lưu.";
    expect(nextAnnouncement(onPage1, { pageNumber: 2, message: locked2 })).toEqual({ pageNumber: 2, text: locked2 });
  });

  it("đổi trang rồi quay lại trang có xung đột ⇒ chữ đã được đặt lại nên vùng đổi chữ và đọc lại", () => {
    let announced = nextAnnouncement(NO_ANNOUNCEMENT, { pageNumber: 1, message: conflict });
    announced = nextAnnouncement(announced, { pageNumber: 2, message: null });
    expect(announced.text).toBe("");
    announced = nextAnnouncement(announced, { pageNumber: 1, message: null });
    expect(announced).toEqual({ pageNumber: 1, text: "" });
    expect(nextAnnouncement(announced, { pageNumber: 1, message: conflict })).toEqual(onPage1);
  });

  it("chưa có trang nào ⇒ vùng rỗng", () => {
    expect(nextAnnouncement(NO_ANNOUNCEMENT, { pageNumber: null, message: null })).toBe(NO_ANNOUNCEMENT);
  });
});
