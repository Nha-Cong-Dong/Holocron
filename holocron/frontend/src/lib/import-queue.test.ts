/** Hàng đợi nhập nhiều PDF (màn 4) và danh sách "Đang đọc" (màn 5). */

import { describe, expect, it } from "vitest";

import type { FileLike } from "./documents";
import type { DocumentStatusView, DocumentSummaryView } from "./documents-data";
import { classifyHttpError, classifyNetworkError, importFailureText } from "./errors";
import {
  CHAPTER_MISSING_VI,
  chapterLabel,
  DEFAULT_SOURCE_KIND,
  defaultTitle,
  hasUnsentRows,
  type ImportQueueAction,
  type ImportQueueState,
  importQueueReducer,
  initialImportQueue,
  isStaged,
  newlySettled,
  nextToSend,
  queueProgress,
  readingRows,
  readingTotal,
  rowsOfSubject,
  START_BLOCK_MESSAGE_VI,
  settledAnnouncement,
  startDecision,
  TITLE_TOO_LONG_VI,
  withLeavingRows,
} from "./import-queue";
import { DOCUMENT_TITLE_MAX_CHARS, DOCUMENT_UPLOAD_MAX_BYTES } from "./limits";

function pdf(name: string, size = 1024): FileLike {
  return { name, type: "application/pdf", size };
}

function run(actions: ImportQueueAction[], start: ImportQueueState = initialImportQueue()): ImportQueueState {
  return actions.reduce(importQueueReducer, start);
}

function row(state: ImportQueueState, id: string) {
  const found = state.rows.find((item) => item.id === id);
  if (found === undefined) throw new Error(`thiếu dòng ${id}`);
  return found;
}

const conflict = classifyHttpError(409, { detail: "đã có ở môn khác" }, { context: "document_upload" });
const passwordError = classifyHttpError(422, { detail: "PDF có mật khẩu" }, { context: "document_upload" });

describe("thêm dòng", () => {
  it("điền tên từ tên file, đoán chương, loại mặc định là slide bài giảng", () => {
    const state = run([{ type: "add", subjectCode: "VXL01", files: [pdf("Chuong-3_Ngat.pdf")] }]);
    expect(state.rows).toHaveLength(1);
    const added = row(state, "import-1");
    expect(added.title).toBe("Chuong-3_Ngat");
    expect(added.chapterCode).toBe("ch3");
    expect(added.chapterGuessed).toBe(true);
    expect(added.sourceKind).toBe(DEFAULT_SOURCE_KIND);
    expect(added.sourceKind).toBe("lecture_slides");
    expect(added.status).toBe("ready");
    expect(added.queued).toBe(false);
  });

  it("không đoán được chương ⇒ dòng chưa hợp lệ với câu chọn chương", () => {
    const state = run([{ type: "add", subjectCode: "VXL01", files: [pdf("Tong quan.pdf")] }]);
    const added = row(state, "import-1");
    expect(added.chapterCode).toBeNull();
    expect(added.status).toBe("invalid");
    expect(added.problem).toBe(CHAPTER_MISSING_VI);
  });

  it("file không phải PDF hay quá giới hạn ⇒ dòng chưa hợp lệ, câu lỗi lấy từ kiểm file có sẵn", () => {
    const state = run([
      {
        type: "add",
        subjectCode: "VXL01",
        files: [{ name: "anh.png", type: "image/png", size: 10 }, pdf("ch1.pdf", DOCUMENT_UPLOAD_MAX_BYTES + 1)],
      },
    ]);
    expect(row(state, "import-1").status).toBe("invalid");
    expect(row(state, "import-1").problem).toContain("Chỉ nhận file PDF");
    expect(row(state, "import-2").status).toBe("invalid");
    expect(row(state, "import-2").problem).toContain("MiB");
  });

  it("id tăng dần, không trùng giữa các lượt thêm", () => {
    const state = run([
      { type: "add", subjectCode: "VXL01", files: [pdf("ch1.pdf"), pdf("ch2.pdf")] },
      { type: "add", subjectCode: "MMT02", files: [pdf("ch1.pdf")] },
    ]);
    expect(state.rows.map((item) => item.id)).toEqual(["import-1", "import-2", "import-3"]);
    expect(rowsOfSubject(state.rows, "MMT02").map((item) => item.id)).toEqual(["import-3"]);
  });

  it("danh sách rỗng không đổi state", () => {
    const start = initialImportQueue();
    expect(importQueueReducer(start, { type: "add", subjectCode: "VXL01", files: [] })).toBe(start);
  });
});

describe("sửa và bỏ dòng", () => {
  it("chọn chương làm dòng hợp lệ; bỏ chọn làm dòng chưa hợp lệ lại", () => {
    let state = run([{ type: "add", subjectCode: "VXL01", files: [pdf("Tong quan.pdf")] }]);
    state = importQueueReducer(state, { type: "edit", id: "import-1", edit: { chapterCode: "ch2" } });
    expect(row(state, "import-1").status).toBe("ready");
    expect(row(state, "import-1").chapterGuessed).toBe(false);
    state = importQueueReducer(state, { type: "edit", id: "import-1", edit: { chapterCode: null } });
    expect(row(state, "import-1").status).toBe("invalid");
  });

  it("tên quá dài ⇒ chưa hợp lệ; tên và loại sửa được", () => {
    let state = run([{ type: "add", subjectCode: "VXL01", files: [pdf("ch1.pdf")] }]);
    state = importQueueReducer(state, {
      type: "edit",
      id: "import-1",
      edit: { title: "x".repeat(DOCUMENT_TITLE_MAX_CHARS + 1) },
    });
    expect(row(state, "import-1").problem).toBe(TITLE_TOO_LONG_VI);
    state = importQueueReducer(state, {
      type: "edit",
      id: "import-1",
      edit: { title: "Bài giảng ngắt", sourceKind: "textbook" },
    });
    expect(row(state, "import-1").status).toBe("ready");
    expect(row(state, "import-1").title).toBe("Bài giảng ngắt");
    expect(row(state, "import-1").sourceKind).toBe("textbook");
  });

  it("dòng đã xếp hàng hay đang gửi không sửa được; dòng đang gửi không bỏ được", () => {
    let state = run([
      { type: "add", subjectCode: "VXL01", files: [pdf("ch1.pdf")] },
      { type: "start", subjectCode: "VXL01" },
    ]);
    const queued = state;
    expect(importQueueReducer(queued, { type: "edit", id: "import-1", edit: { title: "khác" } })).toBe(queued);
    state = importQueueReducer(state, { type: "send_started", id: "import-1" });
    expect(importQueueReducer(state, { type: "remove", id: "import-1" })).toBe(state);
  });

  it("bỏ dòng đang sửa", () => {
    const state = run([
      { type: "add", subjectCode: "VXL01", files: [pdf("ch1.pdf"), pdf("ch2.pdf")] },
      { type: "remove", id: "import-1" },
    ]);
    expect(state.rows.map((item) => item.id)).toEqual(["import-2"]);
  });
});

describe("bắt đầu đọc", () => {
  it("đếm số file gửi được; chặn khi rỗng hoặc còn dòng chưa hợp lệ", () => {
    expect(startDecision([])).toEqual({ count: 0, unknownChapters: 0, block: "empty" });
    const state = run([{ type: "add", subjectCode: "VXL01", files: [pdf("ch1.pdf"), pdf("Tong quan.pdf")] }]);
    expect(startDecision(state.rows)).toEqual({ count: 1, unknownChapters: 1, block: "invalid" });
    const fixed = importQueueReducer(state, { type: "edit", id: "import-2", edit: { chapterCode: "ch2" } });
    expect(startDecision(fixed.rows)).toEqual({ count: 2, unknownChapters: 0, block: null });
    expect(START_BLOCK_MESSAGE_VI.invalid).toContain("chọn chương");
  });

  it("chỉ xếp hàng dòng hợp lệ của đúng môn", () => {
    const state = run([
      { type: "add", subjectCode: "VXL01", files: [pdf("ch1.pdf"), pdf("Tong quan.pdf")] },
      { type: "add", subjectCode: "MMT02", files: [pdf("ch1.pdf")] },
      { type: "start", subjectCode: "VXL01" },
    ]);
    expect(row(state, "import-1").queued).toBe(true);
    expect(row(state, "import-2").queued).toBe(false);
    expect(row(state, "import-3").queued).toBe(false);
    expect(isStaged(row(state, "import-1"))).toBe(false);
    expect(isStaged(row(state, "import-2"))).toBe(true);
  });
});

describe("gửi lần lượt", () => {
  const queued = run([
    { type: "add", subjectCode: "VXL01", files: [pdf("ch1.pdf"), pdf("ch2.pdf"), pdf("ch3.pdf")] },
    { type: "start", subjectCode: "VXL01" },
  ]);

  it("mỗi lúc chỉ một file đang gửi, theo thứ tự thêm", () => {
    expect(nextToSend(queued)?.id).toBe("import-1");
    const sending = importQueueReducer(queued, { type: "send_started", id: "import-1" });
    expect(nextToSend(sending)).toBeNull();
    expect(row(sending, "import-1").status).toBe("sending");
  });

  it("202 ⇒ created, 200 ⇒ existing; đếm số file backend đã nhận theo môn", () => {
    const state = run(
      [
        { type: "send_started", id: "import-1" },
        { type: "send_succeeded", id: "import-1", created: true, documentId: "doc_0123456789abcdef" },
        { type: "send_started", id: "import-2" },
        { type: "send_succeeded", id: "import-2", created: false, documentId: "doc_aaaaaaaaaaaaaaaa" },
      ],
      queued,
    );
    expect(row(state, "import-1").status).toBe("created");
    expect(row(state, "import-1").documentId).toBe("doc_0123456789abcdef");
    expect(row(state, "import-2").status).toBe("existing");
    expect(state.completions.VXL01).toBe(2);
    expect(nextToSend(state)?.id).toBe("import-3");
  });

  it("lỗi giữa chừng không dừng hàng: dòng lỗi giữ lỗi, dòng sau vẫn tới lượt", () => {
    const state = run(
      [
        { type: "send_started", id: "import-1" },
        { type: "send_failed", id: "import-1", error: passwordError },
        { type: "send_started", id: "import-2" },
        { type: "send_failed", id: "import-2", error: conflict },
      ],
      queued,
    );
    expect(row(state, "import-1").status).toBe("failed");
    expect(row(state, "import-1").error).toEqual(passwordError);
    expect(row(state, "import-2").status).toBe("conflict");
    expect(nextToSend(state)?.id).toBe("import-3");
    expect(state.completions.VXL01).toBeUndefined();
  });

  it("mất kết nối ⇒ failed; Thử lại xếp hàng lại đúng dòng đó", () => {
    let state = run(
      [
        { type: "send_started", id: "import-1" },
        { type: "send_failed", id: "import-1", error: classifyNetworkError("") },
      ],
      queued,
    );
    expect(row(state, "import-1").status).toBe("failed");
    state = importQueueReducer(state, { type: "retry", id: "import-1" });
    expect(row(state, "import-1").status).toBe("ready");
    expect(row(state, "import-1").queued).toBe(true);
    expect(row(state, "import-1").error).toBeNull();
  });

  it("dòng trùng nội dung ở môn khác không thử lại được", () => {
    const state = run(
      [
        { type: "send_started", id: "import-1" },
        { type: "send_failed", id: "import-1", error: conflict },
      ],
      queued,
    );
    expect(importQueueReducer(state, { type: "retry", id: "import-1" })).toBe(state);
  });

  it("kết quả của dòng không ở trạng thái đang gửi bị bỏ qua", () => {
    expect(
      importQueueReducer(queued, { type: "send_succeeded", id: "import-1", created: true, documentId: "doc_x" }),
    ).toBe(queued);
    expect(importQueueReducer(queued, { type: "send_failed", id: "import-1", error: conflict })).toBe(queued);
    const staged = run([{ type: "add", subjectCode: "VXL01", files: [pdf("ch1.pdf")] }]);
    expect(importQueueReducer(staged, { type: "send_started", id: "import-1" })).toBe(staged);
  });

  it("tiến độ gửi và cảnh báo khi đóng cửa sổ còn file chưa gửi", () => {
    expect(queueProgress(queued.rows)).toEqual({ settled: 0, total: 3 });
    expect(hasUnsentRows(queued.rows)).toBe(true);
    const state = run(
      [
        { type: "send_started", id: "import-1" },
        { type: "send_succeeded", id: "import-1", created: true, documentId: "doc_1" },
        { type: "send_started", id: "import-2" },
        { type: "send_failed", id: "import-2", error: passwordError },
        { type: "send_started", id: "import-3" },
        { type: "send_succeeded", id: "import-3", created: true, documentId: "doc_3" },
      ],
      queued,
    );
    expect(queueProgress(state.rows)).toEqual({ settled: 3, total: 3 });
    // Dòng gửi lỗi còn thử lại được: đóng cửa sổ sẽ mất file đã chọn nên vẫn cảnh báo.
    expect(hasUnsentRows(state.rows)).toBe(true);
    const cleared = importQueueReducer(state, { type: "clear_settled", subjectCode: "VXL01" });
    expect(cleared.rows.map((item) => item.id)).toEqual(["import-2"]);
    expect(hasUnsentRows(importQueueReducer(cleared, { type: "remove", id: "import-2" }).rows)).toBe(false);
  });

  it("dòng trùng nội dung còn trong danh sách cũng tính là chưa gửi; chỉ dòng đã nhận thì không", () => {
    const state = run(
      [
        { type: "send_started", id: "import-1" },
        { type: "send_failed", id: "import-1", error: conflict },
      ],
      queued,
    );
    expect(hasUnsentRows(state.rows.filter((item) => item.id === "import-1"))).toBe(true);
    const accepted = run(
      [
        { type: "send_started", id: "import-1" },
        { type: "send_succeeded", id: "import-1", created: false, documentId: "doc_1" },
      ],
      queued,
    );
    expect(hasUnsentRows(accepted.rows.filter((item) => item.id === "import-1"))).toBe(false);
  });

  it("bỏ dòng rồi bấm đọc ⇒ dòng đó không được gửi", () => {
    const staged = run([{ type: "add", subjectCode: "VXL01", files: [pdf("ch1.pdf"), pdf("ch2.pdf")] }]);
    const state = run(
      [
        { type: "remove", id: "import-1" },
        { type: "start", subjectCode: "VXL01" },
      ],
      staged,
    );
    expect(state.rows.map((item) => item.id)).toEqual(["import-2"]);
    expect(nextToSend(state)?.id).toBe("import-2");
    const sending = importQueueReducer(state, { type: "send_started", id: "import-2" });
    const done = run([{ type: "send_succeeded", id: "import-2", created: true, documentId: "doc_2" }], sending);
    expect(nextToSend(done)).toBeNull();
  });

  it("dòng đang sửa (chưa bấm đọc) cũng tính là chưa gửi", () => {
    const staged = run([{ type: "add", subjectCode: "VXL01", files: [pdf("Tong quan.pdf")] }]);
    expect(hasUnsentRows(staged.rows)).toBe(true);
    expect(hasUnsentRows([])).toBe(false);
  });
});

describe("trình bày", () => {
  it("dòng vừa bỏ được chèn lại đúng vị trí cũ để chạy hiệu ứng ra", () => {
    const rows = [{ id: "a" }, { id: "c" }];
    expect(withLeavingRows(rows, [{ row: { id: "b" }, index: 1 }]).map((row) => row.id)).toEqual(["a", "b", "c"]);
    expect(withLeavingRows(rows, [{ row: { id: "z" }, index: 9 }]).map((row) => row.id)).toEqual(["a", "c", "z"]);
    expect(withLeavingRows([], [{ row: { id: "b" }, index: 0 }]).map((row) => row.id)).toEqual(["b"]);
  });

  it("chỉ báo dòng vừa có kết quả, không báo lại kết quả cũ", () => {
    const previous = new Map([
      ["a", "sending" as const],
      ["b", "created" as const],
    ]);
    const rows = [
      { id: "a", status: "failed" as const },
      { id: "b", status: "created" as const },
      { id: "c", status: "created" as const },
    ];
    expect(newlySettled(previous, rows).map((row) => row.id)).toEqual(["a"]);
  });

  it("câu báo kết quả của từng file", () => {
    const file = pdf("Bài 1.pdf");
    expect(settledAnnouncement({ file, status: "created", error: null })).toBe("Đã nhận “Bài 1.pdf”, đang đọc.");
    expect(settledAnnouncement({ file, status: "existing", error: null })).toContain("đã có");
    expect(settledAnnouncement({ file, status: "failed", error: passwordError })).toBe("Không nhập được “Bài 1.pdf”.");
  });
});

describe("chữ của dòng", () => {
  it("tên mặc định bỏ đuôi .pdf, nhãn chương", () => {
    expect(defaultTitle("  Bài 3 - Ngắt.PDF ")).toBe("Bài 3 - Ngắt");
    expect(chapterLabel("ch2")).toBe("Chương 2");
    expect(chapterLabel("ch2.1")).toBe("Chương 2.1");
    expect(chapterLabel(null)).toBe("Chưa rõ chương");
  });

  it("câu lỗi nói việc gì, vì sao và cách sửa; không lộ mã HTTP", () => {
    const password = importFailureText("Bài giảng ngắt.pdf", passwordError);
    expect(password.title).toBe("Không nhập được “Bài giảng ngắt.pdf”.");
    expect(password.fix).toContain("mật khẩu");
    expect(importFailureText("a.pdf", conflict).fix).toContain("môn khác");
    expect(
      importFailureText("a.pdf", classifyHttpError(413, { detail: "x" }, { context: "document_upload" })).fix,
    ).toContain("MiB");
    expect(
      importFailureText("a.pdf", classifyHttpError(415, { detail: "x" }, { context: "document_upload" })).fix,
    ).toContain("không phải PDF");
    expect(importFailureText("a.pdf", classifyNetworkError("")).fix).toContain("Thử lại");
    for (const status of [400, 409, 413, 415, 422, 500, 503]) {
      const text = importFailureText(
        "a.pdf",
        classifyHttpError(status, { detail: "x" }, { context: "document_upload" }),
      );
      expect(`${text.title} ${text.fix}`).not.toMatch(/\b(HTTP|backend|[45]\d\d)\b/i);
    }
  });
});

function summary(docId: string, status: DocumentStatusView, pageCount = 10, pagesPending = 0): DocumentSummaryView {
  return {
    docId,
    subjectCode: "VXL01",
    title: `Tài liệu ${docId}`,
    sourceKind: "lecture_slides",
    pageCount,
    hasTextLayer: true,
    uploadedAt: "2026-10-29T00:00:00Z",
    status,
    pagesPending,
    pagesNeedsReview: 0,
    pagesFailed: 0,
    pagesConfirmed: 0,
  };
}

describe("đang đọc", () => {
  it("tài liệu đang đọc có x/y trang; tài liệu vừa đọc xong chỉ hiện khi đã thấy đang đọc", () => {
    const documents = [
      summary("doc_a", "processing", 40, 28),
      summary("doc_b", "review"),
      summary("doc_c", "failed"),
      summary("doc_d", "confirmed"),
    ];
    const rows = readingRows(documents, new Set(["doc_b", "doc_c"]));
    expect(rows.map((item) => [item.docId, item.kind])).toEqual([
      ["doc_a", "reading"],
      ["doc_b", "done"],
      ["doc_c", "failed"],
    ]);
    expect(rows[0]?.line?.progress).toEqual({ pagesRead: 12, pageCount: 40 });
    expect(readingTotal(rows)).toEqual({ finished: 2, total: 3 });
  });

  it("không có tài liệu đang đọc và chưa thấy tài liệu nào ⇒ rỗng", () => {
    expect(readingRows([summary("doc_b", "review")], new Set())).toEqual([]);
    expect(readingTotal([])).toEqual({ finished: 0, total: 0 });
  });
});
