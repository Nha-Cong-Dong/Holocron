import { describe, expect, it } from "vitest";
import type { ConceptPair, ConceptPairStatus, Subject } from "@/types/holocron";
import type { DocumentSummaryView } from "./documents-data";
import { unknownError } from "./errors";
import {
  buildHomeCards,
  formatVietnameseDate,
  pickPrimarySubject,
  readingLines,
  runWithLimit,
  type SubjectStats,
} from "./home-cards";

function subject(code: string, updatedAt: string, name = `Môn ${code}`): Subject {
  return {
    subject_code: code,
    display_name: name,
    exam_format: "mixed",
    version: 1,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: updatedAt,
  };
}

function documentSummary(id: string, overrides: Partial<DocumentSummaryView> = {}): DocumentSummaryView {
  return {
    docId: id,
    subjectCode: "VXL01",
    title: `Tài liệu ${id}`,
    sourceKind: "slide",
    pageCount: 40,
    hasTextLayer: true,
    uploadedAt: "2026-10-20T00:00:00Z",
    status: "confirmed",
    pagesPending: 0,
    pagesNeedsReview: 0,
    pagesFailed: 0,
    pagesConfirmed: 40,
    ...overrides,
  };
}

function pair(id: string, status: ConceptPairStatus, chapter = "ch1"): ConceptPair {
  return {
    pair_id: id,
    chunk_id: "chunk_1",
    subject_code: "VXL01",
    chapter_code: chapter,
    term: "thuật ngữ",
    definition_quote: "thuật ngữ là một đoạn trích",
    term_spans: [[0, 9]],
    status,
    proposed_by: "user",
    mapping_confidence: null,
    version: 1,
    created_at: "",
    confirmed_at: null,
    pre_selected: false,
    failed_gates: [],
  };
}

/** Mốc thời gian dựng theo giờ máy, nên ngày hiển thị không phụ thuộc múi giờ của máy chạy test. */
function localIso(year: number, month: number, day: number, hour = 12): string {
  return new Date(year, month - 1, day, hour).toISOString();
}

const SUBJECTS = [
  subject("VXL01", localIso(2026, 10, 20)),
  subject("MMT01", localIso(2026, 10, 28)),
  subject("CTDL01", localIso(2026, 10, 25)),
];

describe("formatVietnameseDate", () => {
  it("dd/mm/yyyy", () => {
    expect(formatVietnameseDate(localIso(2026, 10, 29))).toBe("29/10/2026");
    expect(formatVietnameseDate(localIso(2026, 3, 5, 0))).toBe("05/03/2026");
    expect(formatVietnameseDate(localIso(2026, 12, 31, 23))).toBe("31/12/2026");
  });

  it("chuỗi không đọc được ⇒ null", () => {
    expect(formatVietnameseDate("")).toBeNull();
    expect(formatVietnameseDate("hôm qua")).toBeNull();
  });
});

describe("pickPrimarySubject", () => {
  it("môn gần nhất còn trong danh sách thắng", () => {
    expect(pickPrimarySubject(SUBJECTS, "VXL01")).toBe("VXL01");
  });

  it("môn gần nhất không còn ⇒ updated_at mới nhất", () => {
    expect(pickPrimarySubject(SUBJECTS, "XOA01")).toBe("MMT01");
    expect(pickPrimarySubject(SUBJECTS, null)).toBe("MMT01");
  });

  it("updated_at hỏng xếp sau cùng; bằng nhau thì môn đứng trước", () => {
    const list = [
      subject("AA01", "hỏng"),
      subject("BB01", "2026-01-01T00:00:00Z"),
      subject("CC01", "2026-01-01T00:00:00Z"),
    ];
    expect(pickPrimarySubject(list, null)).toBe("BB01");
    expect(pickPrimarySubject([subject("AA01", "hỏng")], null)).toBe("AA01");
  });

  it("danh sách rỗng ⇒ null", () => {
    expect(pickPrimarySubject([], "VXL01")).toBeNull();
  });
});

describe("readingLines", () => {
  it("chỉ tài liệu đang đọc; x = page_count − pages_pending", () => {
    const lines = readingLines([
      documentSummary("a", { status: "processing", pageCount: 40, pagesPending: 28, title: "Bài 2" }),
      documentSummary("b"),
    ]);
    expect(lines).toEqual([
      { docId: "a", title: "Bài 2", text: "Đang đọc Bài 2: 12/40 trang", progress: { pagesRead: 12, pageCount: 40 } },
    ]);
  });

  it("thiếu số trang hoặc số đếm không khớp: chỉ ghi đang đọc, không bịa số", () => {
    const lines = readingLines([
      documentSummary("a", { status: "processing", pageCount: 0, pagesPending: 0, title: "Mới" }),
      documentSummary("b", { status: "processing", pageCount: 10, pagesPending: 12, title: "Lệch" }),
    ]);
    expect(lines.map((line) => [line.text, line.progress])).toEqual([
      ["Đang đọc Mới", null],
      ["Đang đọc Lệch", null],
    ]);
  });
});

describe("buildHomeCards", () => {
  it("thiếu số liệu ⇒ thẻ đang tải, nút Mở môn; vẫn có ngày cập nhật từ danh sách môn", () => {
    const [first] = buildHomeCards(SUBJECTS, {}, null);
    expect(first?.stats).toEqual({ kind: "loading" });
    expect(first?.action).toEqual({
      label: "Mở môn",
      href: "/subject?code=VXL01&tab=documents",
      ariaLabel: "Mở môn Môn VXL01",
    });
    expect(first?.updatedLabel).toBe("Cập nhật 20/10/2026");
  });

  it("lỗi một phần: thẻ lỗi không ảnh hưởng thẻ khác", () => {
    const stats: Record<string, SubjectStats> = {
      VXL01: { kind: "error", error: unknownError() },
      MMT01: {
        kind: "ready",
        documents: [documentSummary("a"), documentSummary("b", { status: "processing", pagesPending: 30 })],
        pairs: [
          pair("p1", "confirmed"),
          pair("p2", "confirmed", "ch2"),
          pair("p3", "proposed"),
          pair("p4", "rejected"),
        ],
      },
    };
    const cards = buildHomeCards(SUBJECTS, stats, null);
    expect(cards.map((card) => card.stats.kind)).toEqual(["error", "ready", "loading"]);
    const ready = cards[1];
    expect(ready?.stats.kind === "ready" && ready.stats.documentCount).toBe(2);
    expect(ready?.stats.kind === "ready" && ready.stats.confirmedCount).toBe(2);
    expect(ready?.stats.kind === "ready" && ready.stats.reading.map((line) => line.text)).toEqual([
      "Đang đọc Tài liệu b: 10/40 trang",
    ]);
    expect(ready?.action).toEqual({
      label: "Ôn tiếp",
      href: "/subject?code=MMT01&tab=practice",
      ariaLabel: "Ôn tiếp môn Môn MMT01",
    });
  });

  it("chưa có khái niệm đã nhận ⇒ Thêm tài liệu (chỉ proposed không tính)", () => {
    const stats: Record<string, SubjectStats> = {
      VXL01: { kind: "ready", documents: [], pairs: [pair("p1", "proposed")] },
    };
    const card = buildHomeCards(SUBJECTS, stats, null)[0];
    expect(card?.stats.kind === "ready" && card.stats.confirmedCount).toBe(0);
    expect(card?.action.label).toBe("Thêm tài liệu");
    expect(card?.action.href).toBe("/subject?code=VXL01&tab=documents");
    expect(card?.action.ariaLabel).toBe("Thêm tài liệu cho môn Môn VXL01");
  });

  it("đúng một thẻ primary: môn gần nhất, không thì môn mới cập nhật nhất", () => {
    expect(
      buildHomeCards(SUBJECTS, {}, "CTDL01")
        .filter((card) => card.primary)
        .map((card) => card.code),
    ).toEqual(["CTDL01"]);
    expect(
      buildHomeCards(SUBJECTS, {}, null)
        .filter((card) => card.primary)
        .map((card) => card.code),
    ).toEqual(["MMT01"]);
  });

  it("nhiều tài liệu đang đọc: tối đa 2 dòng, phần còn lại đếm gộp", () => {
    const documents = ["a", "b", "c", "d"].map((id) => documentSummary(id, { status: "processing", pagesPending: 1 }));
    const card = buildHomeCards(SUBJECTS, { VXL01: { kind: "ready", documents, pairs: [] } }, null)[0];
    expect(card?.stats.kind === "ready" && card.stats.reading).toHaveLength(2);
    expect(card?.stats.kind === "ready" && card.stats.moreReading).toBe(2);
  });

  it("updated_at hỏng ⇒ không có dòng cập nhật", () => {
    expect(buildHomeCards([subject("AA01", "x")], {}, null)[0]?.updatedLabel).toBeNull();
  });
});

describe("runWithLimit", () => {
  it("không quá giới hạn việc cùng lúc và chạy đủ mọi phần tử", async () => {
    let running = 0;
    let peak = 0;
    const done: number[] = [];
    await runWithLimit([1, 2, 3, 4, 5, 6, 7, 8, 9], 4, async (item) => {
      running += 1;
      peak = Math.max(peak, running);
      await new Promise((resolve) => setTimeout(resolve, 1));
      done.push(item);
      running -= 1;
    });
    expect(peak).toBe(4);
    expect([...done].sort((a, b) => a - b)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
  });

  it("danh sách rỗng xong ngay; giới hạn không hợp lệ coi như 1", async () => {
    await expect(runWithLimit([], 4, async () => undefined)).resolves.toBeUndefined();
    let peak = 0;
    let running = 0;
    await runWithLimit([1, 2, 3], 0, async () => {
      running += 1;
      peak = Math.max(peak, running);
      await Promise.resolve();
      running -= 1;
    });
    expect(peak).toBe(1);
  });
});
