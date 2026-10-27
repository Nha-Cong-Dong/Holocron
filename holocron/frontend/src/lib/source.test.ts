import { describe, expect, it } from "vitest";

import { CHUNK_FIXTURES, DEFINITIONS } from "@/mocks/fixtures/chunks";

import {
  chunkScanTruncated,
  isBackendWhitespace,
  locateQuote,
  MAX_CHUNK_PAGES,
  nextChunkOffset,
  normalizeForMatch,
} from "./source";

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

describe("khoảng trắng theo định nghĩa của backend (Python \\s)", () => {
  // Đúng tập ký tự `re.fullmatch(r"\s", ch)` của Python, mà `_collapse_ws` ở `domain/schemas.py` dùng.
  const PYTHON_WHITESPACE = [
    0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003,
    0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
  ];

  it("khớp đúng tập ký tự của Python trong toàn bộ mặt phẳng cơ bản", () => {
    const found: number[] = [];
    for (let code = 0; code <= 0xffff; code += 1) {
      if (isBackendWhitespace(String.fromCharCode(code))) found.push(code);
    }
    expect(found).toEqual(PYTHON_WHITESPACE);
  });

  it("U+0085 và U+001C–U+001F được gộp; U+FEFF không phải khoảng trắng", () => {
    for (const char of ["\u0085", "\u001c", "\u001d", "\u001e", "\u001f"]) {
      expect(normalizeForMatch(`a${char}${char} b${char}`)).toBe("a b");
      expect(locateQuote(`Mở.${char}Định${char}nghĩa đủ dài.`, "Định nghĩa đủ dài.")?.match).toBe(
        `Định${char}nghĩa đủ dài.`,
      );
    }
    expect(isBackendWhitespace("﻿")).toBe(false);
    expect(normalizeForMatch("﻿a b﻿")).toBe("﻿a b﻿");
    expect(locateQuote("Định﻿nghĩa đủ dài.", "Định nghĩa đủ dài.")).toBeNull();
  });

  it("normalizeForMatch: NFC, gộp khoảng trắng, bỏ hai đầu", () => {
    expect(normalizeForMatch("  Học\n\t xen kẽ  ".normalize("NFD"))).toBe("Học xen kẽ".normalize("NFC"));
  });
});

describe("chunkScanTruncated", () => {
  it("chỉ đúng khi dừng vì đạt giới hạn số lần đọc mà backend còn chunk", () => {
    expect(chunkScanTruncated(800, 200, 5000, MAX_CHUNK_PAGES)).toBe(true);
    expect(chunkScanTruncated(800, 200, 1000, MAX_CHUNK_PAGES)).toBe(false);
    expect(chunkScanTruncated(0, 200, 5000, 1)).toBe(false);
    expect(chunkScanTruncated(800, 0, 5000, MAX_CHUNK_PAGES)).toBe(false);
  });
});
