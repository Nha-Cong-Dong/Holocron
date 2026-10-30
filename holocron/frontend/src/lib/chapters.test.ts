import { describe, expect, it } from "vitest";
import {
  CHAPTER_LOAD_CONCURRENCY,
  chaptersOfDocuments,
  compareChapterCodes,
  guessChapterCode,
  importChapterOptions,
  keepOrFirstChapter,
  loadSubjectChapters,
  sortedChapterCodes,
} from "./chapters";
import { ApiError } from "./errors";

describe("guessChapterCode", () => {
  it.each([
    ["Chuong-3_Bo-nho.pdf", "ch3"],
    ["chương 3 - Bộ nhớ.pdf", "ch3"],
    ["CHƯƠNG 3.pdf".normalize("NFD"), "ch3"],
    ["ch3.pdf", "ch3"],
    ["ch.3 slides.pdf", "ch3"],
    ["bai 3.pdf", "ch3"],
    ["Bài 03 - Ngắt.pdf", "ch3"],
    ["Chapter 12 Interrupts.pdf", "ch12"],
    ["C3.pdf", "ch3"],
    ["Lecture 3.pdf", "ch3"],
    ["lecture_07_final.pdf", "ch7"],
    ["VXL_chuong2.1_vi-dieu-khien.pdf", "ch2.1"],
    ["Slide-ch10-tong-ket.pdf", "ch10"],
  ])("%s ⇒ %s", (filename, expected) => {
    expect(guessChapterCode(filename)).toBe(expected);
  });

  it.each([
    ["Giao trinh Vi xu ly.pdf"],
    ["de-thi-2024.pdf"],
    ["abc3.pdf"],
    ["chuong 0.pdf"],
    ["ch123.pdf"],
    ["Lop C 2024.pdf"],
    [""],
  ])("không đoán được: %s ⇒ null", (filename) => {
    expect(guessChapterCode(filename)).toBeNull();
  });
});

describe("sắp xếp và danh sách chương", () => {
  it("sắp theo số chương, chương con sau chương cha", () => {
    expect(["ch10", "ch2.1", "ch1", "ch2"].sort(compareChapterCodes)).toEqual(["ch1", "ch2", "ch2.1", "ch10"]);
  });

  it("bỏ null, mã sai mẫu và trùng", () => {
    expect(sortedChapterCodes(["ch3", null, "ch3", "Ch4", "chuong5", "ch1"])).toEqual(["ch1", "ch3"]);
    expect(
      chaptersOfDocuments([
        { chapterCode: "ch2" },
        { chapterCode: null },
        { chapterCode: "ch1" },
        { chapterCode: "ch2" },
      ]),
    ).toEqual(["ch1", "ch2"]);
  });

  it("lựa chọn nhập tài liệu gồm ch1…ch20, chương đã có và chương đoán", () => {
    const options = importChapterOptions(["ch2.1", "ch25"], "ch30");
    expect(options.slice(0, 4)).toEqual(["ch1", "ch2", "ch2.1", "ch3"]);
    expect(options).toContain("ch20");
    expect(options.slice(-2)).toEqual(["ch25", "ch30"]);
    expect(importChapterOptions([], null)).toHaveLength(20);
  });

  it("giữ chương cũ nếu còn, không thì chương đầu, danh sách rỗng thì rỗng", () => {
    expect(keepOrFirstChapter("ch2", ["ch1", "ch2"])).toBe("ch2");
    expect(keepOrFirstChapter("ch9", ["ch1", "ch2"])).toBe("ch1");
    expect(keepOrFirstChapter("ch9", [])).toBe("");
  });
});

describe("loadSubjectChapters", () => {
  function conflict(): ApiError {
    return new ApiError(409, {
      kind: "state_conflict",
      message: "Tài liệu chưa có chương.",
      action: "reload",
      issues: [],
      capability: null,
    });
  }

  function source(chapters: Record<string, string | null | Error>) {
    return {
      listDocuments: async () => Object.keys(chapters).map((docId) => ({ docId })),
      getDocument: async (docId: string) => {
        const value = chapters[docId];
        if (value instanceof Error) throw value;
        return { chapterCode: value ?? null };
      },
    };
  }

  it("đọc chương của từng tài liệu, khử trùng và sắp xếp", async () => {
    await expect(loadSubjectChapters(source({ a: "ch10", b: "ch2", c: "ch2", d: null }), "VXL01")).resolves.toEqual([
      "ch2",
      "ch10",
    ]);
  });

  it("môn chưa có tài liệu ⇒ rỗng", async () => {
    await expect(loadSubjectChapters(source({}), "VXL01")).resolves.toEqual([]);
  });

  it("409 (tài liệu không có chương) bị bỏ qua, không ném lỗi", async () => {
    await expect(loadSubjectChapters(source({ a: conflict(), b: "ch3" }), "VXL01")).resolves.toEqual(["ch3"]);
  });

  it("mọi tài liệu đều 409 ⇒ rỗng như môn chưa có tài liệu", async () => {
    await expect(loadSubjectChapters(source({ a: conflict(), b: conflict() }), "VXL01")).resolves.toEqual([]);
  });

  it("tối đa 4 lời gọi getDocument cùng lúc", async () => {
    let running = 0;
    let peak = 0;
    const ids = Array.from({ length: 10 }, (_, index) => ({ docId: `d${index}` }));
    const chapters = await loadSubjectChapters(
      {
        listDocuments: async () => ids,
        getDocument: async (docId: string) => {
          running += 1;
          peak = Math.max(peak, running);
          await new Promise((resolve) => setTimeout(resolve, 1));
          running -= 1;
          return { chapterCode: `ch${Number(docId.slice(1)) + 1}` };
        },
      },
      "VXL01",
    );
    expect(peak).toBe(CHAPTER_LOAD_CONCURRENCY);
    expect(chapters).toHaveLength(10);
  });

  it("bỏ tài liệu đọc lỗi; mọi tài liệu đều lỗi thì ném lỗi", async () => {
    await expect(loadSubjectChapters(source({ a: new Error("x"), b: "ch1" }), "VXL01")).resolves.toEqual(["ch1"]);
    await expect(loadSubjectChapters(source({ a: new Error("hỏng") }), "VXL01")).rejects.toThrow("hỏng");
  });
});
