import { describe, expect, it } from "vitest";

import { CHUNK_FIXTURES, DEFINITIONS } from "@/mocks/fixtures/chunks";

import { locateQuote, MAX_CHUNK_PAGES, nextChunkOffset } from "./source";

describe("locateQuote", () => {
  it("tách đúng ba phần, phần khớp là nguyên văn chunk", () => {
    const raw = CHUNK_FIXTURES[0]?.raw_text ?? "";
    const segments = locateQuote(raw, DEFINITIONS.selfTest);
    expect(segments?.match).toBe(DEFINITIONS.selfTest);
    expect(`${segments?.before}${segments?.match}${segments?.after}`).toBe(raw.normalize("NFC"));
  });

  it("khớp khi khoảng trắng khác nhau, giữ xuống dòng gốc trong phần khớp", () => {
    const raw = "Mở đầu.\nĐịnh nghĩa  là\n  một câu dài đủ để trích. Hết.";
    const segments = locateQuote(raw, "Định nghĩa là một câu dài đủ để trích.");
    expect(segments).toEqual({
      before: "Mở đầu.\n",
      match: "Định nghĩa  là\n  một câu dài đủ để trích.",
      after: " Hết.",
    });
  });

  it("khớp khi trích dẫn ở dạng NFD còn chunk ở dạng NFC (và ngược lại)", () => {
    const raw = "Tiêu đề. Học xen kẽ là cách trộn nhiều dạng bài.";
    const quote = "Học xen kẽ là cách trộn nhiều dạng bài.";
    expect(locateQuote(raw.normalize("NFC"), quote.normalize("NFD"))?.match).toBe(quote.normalize("NFC"));
    expect(locateQuote(raw.normalize("NFD"), quote.normalize("NFC"))?.match).toBe(quote.normalize("NFC"));
  });

  it("không tìm thấy hoặc trích dẫn rỗng thì trả null", () => {
    expect(locateQuote("Văn bản nguồn.", "Không có trong văn bản")).toBeNull();
    expect(locateQuote("Văn bản nguồn.", "   ")).toBeNull();
  });
});

describe("nextChunkOffset", () => {
  it("đọc tiếp tới khi hết dữ liệu hoặc đủ số lần cho phép", () => {
    expect(nextChunkOffset(0, 200, 450, 1)).toBe(200);
    expect(nextChunkOffset(400, 50, 450, 3)).toBeNull();
    expect(nextChunkOffset(0, 0, 450, 1)).toBeNull();
    expect(nextChunkOffset(0, 200, 5000, MAX_CHUNK_PAGES)).toBeNull();
  });
});
