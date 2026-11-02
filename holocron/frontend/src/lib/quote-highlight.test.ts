/**
 * Vị trí tô sáng trích dẫn (T2.3, I3): tìm sau NFC và gộp khoảng trắng, ánh xạ về offset của `raw_text` nguyên bản;
 * không khớp ⇒ null. Hồi quy: offset vẫn khớp `raw_text` để đường tự bôi (Selection) không bị lệch.
 */

import { describe, expect, it } from "vitest";

import { normalizeQuote, selectionRange, selectionToQuote } from "./pairs";
import { canHighlight, findQuoteRange, QUOTE_HIGHLIGHT_NAME } from "./quote-highlight";

const RAW = "Xác suất của một biến cố là số thực.\nBiến cố đối của A là biến cố xảy ra khi A không xảy ra.";

function sliced(raw: string, quote: string): string {
  const range = findQuoteRange(raw, quote);
  if (range === null) throw new Error("phải tìm thấy");
  return raw.slice(range.start, range.end);
}

describe("findQuoteRange", () => {
  it("trích dẫn nguyên văn ⇒ đúng lát của raw_text", () => {
    const quote = "Biến cố đối của A là biến cố xảy ra khi A không xảy ra.";
    expect(sliced(RAW, quote)).toBe(quote);
    expect(findQuoteRange(RAW, quote)).toEqual({ start: RAW.indexOf(quote), end: RAW.indexOf(quote) + quote.length });
  });

  it("trích dẫn dạng NFD, văn bản dạng NFC ⇒ vẫn tìm thấy", () => {
    const quote = "số thực".normalize("NFD");
    expect(quote).not.toBe("số thực");
    expect(sliced(RAW, quote)).toBe("số thực");
  });

  it("văn bản dạng NFD, trích dẫn dạng NFC ⇒ tìm thấy và vị trí tính trên văn bản NFD nguyên bản", () => {
    const raw = RAW.normalize("NFD");
    const range = findQuoteRange(raw, "một biến cố");
    expect(range).not.toBeNull();
    expect(normalizeQuote(raw.slice(range?.start, range?.end))).toBe("một biến cố");
  });

  it("khoảng trắng được gộp: dấu xuống dòng trong văn bản khớp một dấu cách trong trích dẫn", () => {
    const quote = "là số thực. Biến cố đối";
    expect(findQuoteRange(RAW, quote)).not.toBeNull();
    expect(sliced(RAW, quote)).toBe("là số thực.\nBiến cố đối");
  });

  it("dãy khoảng trắng dài trong văn bản vẫn khớp một dấu cách", () => {
    const raw = "alpha   \t beta";
    expect(sliced(raw, "alpha beta")).toBe(raw);
  });

  it("không có trong văn bản ⇒ null (không tô, không coi là đã kiểm chứng)", () => {
    expect(findQuoteRange(RAW, "Định nghĩa không có ở đây")).toBeNull();
  });

  it("trích dẫn rỗng hoặc chỉ khoảng trắng ⇒ null", () => {
    expect(findQuoteRange(RAW, "")).toBeNull();
    expect(findQuoteRange(RAW, " \n ")).toBeNull();
  });

  it("xuất hiện nhiều lần ⇒ lần đầu", () => {
    const range = findQuoteRange("ab ab ab", "ab");
    expect(range).toEqual({ start: 0, end: 2 });
  });

  it("không cắt đôi chữ có dấu rời: khoảng nới tới hết cụm ký tự-dấu", () => {
    const raw = "tiến trình".normalize("NFD");
    const range = findQuoteRange(raw, "tiến");
    expect(raw.slice(range?.start, range?.end).normalize("NFC")).toBe("tiến");
  });
});

describe("hồi quy: offset bôi chọn vẫn khớp raw_text", () => {
  it("vùng tô và vùng bôi dùng cùng hệ offset: selectionRange trả lại đúng khoảng, selectionToQuote trả lại đúng lát", () => {
    const quote = "Biến cố đối của A là biến cố xảy ra khi A không xảy ra.";
    const range = findQuoteRange(RAW, quote);
    expect(range).not.toBeNull();
    if (range === null) return;
    // PassageText giữ `raw_text` trong một text node: nodeData === rawText và offset là offset trong raw_text.
    expect(selectionRange(RAW, RAW, range.start, range.end, true)).toEqual(range);
    expect(selectionToQuote(RAW, range.start, range.end)).toBe(quote);
  });

  it("tên vùng tô cố định, trùng tên ::highlight() trong CSS module", () => {
    expect(QUOTE_HIGHLIGHT_NAME).toBe("holocron-quote");
  });
});

describe("canHighlight", () => {
  it("cần cả CSS.highlights và Highlight", () => {
    expect(canHighlight({ CSS: { highlights: new Map() }, Highlight: class {} })).toBe(true);
    expect(canHighlight({ CSS: {}, Highlight: class {} })).toBe(false);
    expect(canHighlight({ CSS: { highlights: new Map() } })).toBe(false);
    expect(canHighlight({})).toBe(false);
    expect(canHighlight({ CSS: null, Highlight: class {} })).toBe(false);
  });
});
