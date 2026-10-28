import { describe, expect, it } from "vitest";

import {
  chunksOfChapter,
  citationCaption,
  conceptCreateRequest,
  passageNav,
  resolveOpenPassage,
  SENTENCE_LABEL_MAX_CHARS,
  selectionOfQuote,
  sentenceLabel,
  sentenceRanges,
} from "./passage";
import type { ChunkView } from "./vault";

function chunk(chunkId: string, chapterCode: string, rawText = "Văn bản mẫu đủ dài."): ChunkView {
  return {
    chunkId,
    docId: "doc_0000000000000001",
    subjectCode: "MAU101",
    chapterCode,
    pageNumber: 14,
    pageEnd: null,
    rawText,
    verbatimQuote: rawText,
    category: "THEORY",
    topicKeys: [],
    labelStatus: "confirmed",
    version: 1,
  };
}

/** Mọi câu là đúng lát của văn bản tại vị trí của nó. */
function expectSlices(rawText: string): void {
  for (const range of sentenceRanges(rawText)) {
    expect(range.text).toBe(rawText.slice(range.start, range.end));
    expect(rawText.includes(range.text)).toBe(true);
  }
}

describe("sentenceRanges", () => {
  it("tách theo dấu kết câu, bỏ khoảng trắng hai đầu, giữ thứ tự", () => {
    const text = "  Cờ IF cho phép CPU nhận ngắt INTR. Lệnh CLI xoá cờ IF!  Ngắt NMI có bị che không?";
    expect(sentenceRanges(text).map((range) => range.text)).toEqual([
      "Cờ IF cho phép CPU nhận ngắt INTR.",
      "Lệnh CLI xoá cờ IF!",
      "Ngắt NMI có bị che không?",
    ]);
    expectSlices(text);
  });

  it("dấu chấm giữa số hay viết tắt không cắt câu khi không có khoảng trắng theo sau", () => {
    const text = "Phiên bản 2.1 của chuẩn thêm ngắt mới. Câu thứ hai đủ dài.";
    expect(sentenceRanges(text).map((range) => range.text)).toEqual([
      "Phiên bản 2.1 của chuẩn thêm ngắt mới.",
      "Câu thứ hai đủ dài.",
    ]);
  });

  it("xuống dòng là ranh giới câu, trừ dòng bị gói giữa câu (dòng sau bắt đầu bằng chữ thường)", () => {
    const text =
      "• Ngắt cứng do thiết bị gửi\n• Ngắt mềm do lệnh INT gây ra\nKhi có yêu cầu ngắt, CPU\nchỉ đáp ứng nếu cờ IF bật.";
    expect(sentenceRanges(text).map((range) => range.text)).toEqual([
      "• Ngắt cứng do thiết bị gửi",
      "• Ngắt mềm do lệnh INT gây ra",
      "Khi có yêu cầu ngắt, CPU\nchỉ đáp ứng nếu cờ IF bật.",
    ]);
    expectSlices(text);
  });

  it("CRLF và nhiều dòng trống không sinh câu rỗng", () => {
    const text = "Dòng thứ nhất đủ dài\r\n\r\n\r\nDòng thứ hai cũng đủ dài\r\n";
    expect(sentenceRanges(text).map((range) => range.text)).toEqual([
      "Dòng thứ nhất đủ dài",
      "Dòng thứ hai cũng đủ dài",
    ]);
    expectSlices(text);
  });

  it("câu quá ngắn (dưới 8 ký tự sau gộp khoảng trắng) hoặc quá dài (trên 600) bị bỏ", () => {
    const long = `${"a".repeat(601)}.`;
    const text = `Ngắn. ${long} Câu này vừa đủ dài.`;
    expect(sentenceRanges(text).map((range) => range.text)).toEqual(["Câu này vừa đủ dài."]);
  });

  it("văn bản rỗng hoặc chỉ khoảng trắng ⇒ không có câu", () => {
    expect(sentenceRanges("")).toEqual([]);
    expect(sentenceRanges(" \n\t ")).toEqual([]);
  });

  it("không có dấu câu ⇒ cả văn bản là một câu", () => {
    const text = "Bộ điều khiển ngắt lập trình được";
    expect(sentenceRanges(text)).toEqual([{ start: 0, end: text.length, text, label: text }]);
  });

  it("NFD và NFC: lát giữ nguyên dạng của văn bản gốc, không chuẩn hoá", () => {
    const nfc = "Cờ ngắt được xoá bởi lệnh CLI. Ngắt NMI không bị che.";
    const nfd = nfc.normalize("NFD");
    const fromNfd = sentenceRanges(nfd);
    expect(fromNfd).toHaveLength(2);
    expect(fromNfd[0]?.text).toBe("Cờ ngắt được xoá bởi lệnh CLI.".normalize("NFD"));
    expect(fromNfd[0]?.text.normalize("NFC")).toBe("Cờ ngắt được xoá bởi lệnh CLI.");
    expectSlices(nfd);
    expectSlices(nfc);
  });

  it("ký tự ngoài BMP không bị cắt đôi", () => {
    const text = "Biến 𝑥 lưu cờ ngắt hiện tại. Biến 𝑦 lưu mặt nạ ngắt.";
    expectSlices(text);
    expect(sentenceRanges(text).map((range) => range.text)).toEqual([
      "Biến 𝑥 lưu cờ ngắt hiện tại.",
      "Biến 𝑦 lưu mặt nạ ngắt.",
    ]);
  });
});

describe("sentenceLabel", () => {
  it("câu ngắn giữ nguyên; câu dài cắt còn tối đa 44 ký tự kèm dấu …", () => {
    expect(sentenceLabel("Câu ngắn.")).toBe("Câu ngắn.");
    const long = "Khi có yêu cầu ngắt trên chân INTR, CPU chỉ đáp ứng nếu cờ cho phép ngắt đang bật.";
    const label = sentenceLabel(long);
    expect(label.endsWith("…")).toBe(true);
    expect(Array.from(label).length).toBeLessThanOrEqual(SENTENCE_LABEL_MAX_CHARS);
    expect(long.startsWith(label.slice(0, -1))).toBe(true);
  });

  it("không tách chữ khỏi dấu tổ hợp (NFD)", () => {
    const nfd = "Cờ cho phép ngắt được đặt lại ở mỗi lần khởi động máy tính".normalize("NFD");
    const label = sentenceLabel(nfd);
    const body = label.slice(0, -1);
    expect(nfd.startsWith(body)).toBe(true);
    expect(/\p{M}/u.test(nfd.charAt(body.length))).toBe(false);
  });
});

describe("passageNav và chunksOfChapter", () => {
  const map = new Map([
    ["a", chunk("a", "ch1")],
    ["b", chunk("b", "ch2")],
    ["c", chunk("c", "ch1")],
    ["d", chunk("d", "ch1")],
  ]);

  it("đoạn của chương giữ thứ tự backend", () => {
    expect(chunksOfChapter(map, "ch1").map((item) => item.chunkId)).toEqual(["a", "c", "d"]);
    expect(chunksOfChapter(map, "ch9")).toEqual([]);
  });

  it("đoạn trước/sau và vị trí; hai đầu là null; đoạn lạ ⇒ null", () => {
    const list = chunksOfChapter(map, "ch1");
    expect(passageNav(list, "a")).toEqual({ previous: null, next: "c", position: 1, total: 3 });
    expect(passageNav(list, "c")).toEqual({ previous: "a", next: "d", position: 2, total: 3 });
    expect(passageNav(list, "d")).toEqual({ previous: "c", next: null, position: 3, total: 3 });
    expect(passageNav(list, "b")).toBeNull();
  });
});

describe("citationCaption", () => {
  it("chương và trang do backend trả", () => {
    expect(citationCaption("ch2", { pageNumber: 14, pageEnd: null })).toBe("Chương 2 · tr. 14");
    expect(citationCaption("ch2.1", { pageNumber: 3, pageEnd: 5 })).toBe("Chương 2.1 · tr. 3–5");
  });
});

describe("conceptCreateRequest", () => {
  const raw = "Mở đầu. Khi có yêu cầu ngắt trên chân INTR, CPU chỉ đáp ứng nếu cờ cho phép ngắt đang bật. Hết.";
  const source = chunk("chk_1", "ch3", raw);

  it("trích dẫn gửi đi là ĐÚNG lát nguyên văn, kể cả câu dài hơn nhãn chip", () => {
    const sentence = sentenceRanges(raw).find((range) => range.text.length > SENTENCE_LABEL_MAX_CHARS);
    expect(sentence).toBeDefined();
    if (sentence === undefined) return;
    expect(sentence.label).not.toBe(sentence.text);
    const result = conceptCreateRequest(source, sentence, "  Cờ cho phép ngắt ");
    expect(result).toEqual({
      ok: true,
      request: { chunkId: "chk_1", term: "Cờ cho phép ngắt", definitionQuote: raw.slice(sentence.start, sentence.end) },
    });
  });

  it("vùng chọn bôi chuột ứng với đúng lát trong văn bản", () => {
    const quote = "cờ cho phép ngắt đang bật";
    const selection = selectionOfQuote(raw, quote);
    expect(selection).not.toBeNull();
    if (selection === null) return;
    expect(raw.slice(selection.start, selection.end)).toBe(quote);
    expect(selectionOfQuote(raw, "không có trong văn bản")).toBeNull();
    expect(selectionOfQuote(raw, "")).toBeNull();
  });

  it("thiếu tên, trích dẫn quá ngắn hoặc vùng chọn ngoài biên ⇒ lỗi đúng ô", () => {
    expect(conceptCreateRequest(source, { start: 8, end: 60 }, "   ")).toMatchObject({ ok: false, field: "term" });
    expect(conceptCreateRequest(source, { start: 0, end: 4 }, "Tên")).toMatchObject({ ok: false, field: "quote" });
    expect(conceptCreateRequest(source, { start: 5, end: raw.length + 1 }, "Tên")).toMatchObject({
      ok: false,
      field: "quote",
    });
    expect(conceptCreateRequest(source, { start: 9, end: 9 }, "Tên")).toMatchObject({ ok: false, field: "quote" });
  });
});

describe("sentenceRanges: dòng gói giữa câu mở bằng chữ thường ngoài BMP", () => {
  it("dòng sau mở bằng chữ thường ngoài BMP (cặp surrogate) vẫn là cùng một câu", () => {
    const raw = "Biến chứa giá trị hiện\n𝑥 là cờ ngắt đang bật.";
    const ranges = sentenceRanges(raw);
    expect(ranges).toHaveLength(1);
    expect(ranges[0]?.text).toBe(raw);
  });
});

describe("sentenceLabel: biên độ dài", () => {
  it("câu đúng 44 code point ⇒ nhãn là nguyên câu, không có …", () => {
    const sentence = `${"a".repeat(SENTENCE_LABEL_MAX_CHARS - 1)}.`;
    expect([...sentence]).toHaveLength(44);
    expect(sentenceLabel(sentence)).toBe(sentence);
  });

  it("câu 80 ký tự không có khoảng trắng ở chỗ cắt ⇒ 42 code point đầu kèm … (dài 43)", () => {
    const sentence = `${"x".repeat(79)}.`;
    const label = sentenceLabel(sentence);
    expect(label).toBe(`${"x".repeat(42)}…`);
    expect([...label]).toHaveLength(43);
  });
});

describe("conceptCreateRequest: biên vùng chọn và I3", () => {
  const raw = "Mở đầu. Khi có yêu cầu ngắt trên chân INTR, CPU chỉ đáp ứng nếu cờ cho phép ngắt đang bật. Hết.";
  const source = chunk("chk_1", "ch3", raw);

  it("start = 0 và end = độ dài văn bản là vùng chọn hợp lệ", () => {
    expect(conceptCreateRequest(source, { start: 0, end: 60 }, "Ngắt")).toEqual({
      ok: true,
      request: { chunkId: "chk_1", term: "Ngắt", definitionQuote: raw.slice(0, 60) },
    });
    expect(conceptCreateRequest(source, { start: 8, end: raw.length }, "Ngắt")).toEqual({
      ok: true,
      request: { chunkId: "chk_1", term: "Ngắt", definitionQuote: raw.slice(8, raw.length) },
    });
  });

  it("vùng chọn rỗng ⇒ câu 'Chọn lại'; vị trí không nguyên ⇒ lỗi ở trích dẫn", () => {
    expect(conceptCreateRequest(source, { start: 9, end: 9 }, "Ngắt")).toEqual({
      ok: false,
      field: "quote",
      message: "Chọn lại một đoạn trong văn bản.",
    });
    expect(conceptCreateRequest(source, { start: 8.5, end: 60 }, "Ngắt")).toMatchObject({ ok: false, field: "quote" });
    expect(conceptCreateRequest(source, { start: 8, end: 59.5 }, "Ngắt")).toMatchObject({ ok: false, field: "quote" });
  });

  it("I3: văn bản NFD ⇒ trích dẫn là đúng lát NFD, không NFC hoá", () => {
    const rawNfd = raw.normalize("NFD");
    expect(rawNfd).not.toBe(raw);
    const start = rawNfd.indexOf("Khi");
    const end = rawNfd.lastIndexOf(" ");
    const result = conceptCreateRequest(chunk("chk_2", "ch3", rawNfd), { start, end }, "Ngắt");
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.request.definitionQuote).toBe(rawNfd.slice(start, end));
    expect(result.request.definitionQuote).not.toBe(rawNfd.slice(start, end).normalize("NFC"));
  });

  it("I3: xuống dòng và hai dấu cách liền trong vùng chọn được giữ nguyên", () => {
    const text = "Định nghĩa:\nNgắt  là tín hiệu báo cho CPU một sự kiện.";
    const result = conceptCreateRequest(chunk("chk_3", "ch3", text), { start: 0, end: text.length }, "Ngắt");
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.request.definitionQuote).toBe(text);
    expect(result.request.definitionQuote).toContain("\n");
    expect(result.request.definitionQuote).toContain("Ngắt  là");
  });
});

describe("resolveOpenPassage: kết quả tìm hiện thẳng đoạn backend trả", () => {
  const first = chunk("chk_1", "ch1");
  const second = chunk("chk_2", "ch2");
  const map = new Map([
    [first.chunkId, first],
    [second.chunkId, second],
  ]);
  const hit = chunk("chk_far", "ch9", "Đoạn ở xa chưa nằm trong phần đã tải.");

  it("chưa chọn đoạn ⇒ đoạn đầu tiên; đoạn có trong map ⇒ đúng đoạn đó", () => {
    expect(resolveOpenPassage("complete", map, null, null)).toEqual({ chunk: first, note: null });
    expect(resolveOpenPassage("partial", map, "chk_2", null)).toEqual({ chunk: second, note: null });
  });

  it("map chưa tải hết mà đoạn là kết quả tìm vừa chọn ⇒ hiện thẳng đoạn của kết quả tìm, không báo", () => {
    expect(resolveOpenPassage("partial", map, "chk_far", hit)).toEqual({ chunk: hit, note: null });
  });

  it("đoạn không có trong map và không phải kết quả vừa chọn ⇒ đoạn đầu tiên kèm lý do đúng", () => {
    expect(resolveOpenPassage("partial", map, "chk_far", null)).toEqual({ chunk: first, note: "not_loaded" });
    expect(resolveOpenPassage("partial", map, "chk_far", second)).toEqual({ chunk: first, note: "not_loaded" });
    expect(resolveOpenPassage("complete", map, "chk_gone", null)).toEqual({ chunk: first, note: "missing" });
  });

  it("map rỗng ⇒ không có đoạn", () => {
    expect(resolveOpenPassage("complete", new Map(), null, null)).toEqual({ chunk: null, note: null });
  });
});

describe("sentenceLabel: chỗ cắt rơi ngay sau dấu cách", () => {
  it("bỏ khoảng trắng cuối trước dấu … (không ra ' …')", () => {
    // 41 chữ rồi một dấu cách: 42 code point đầu kết thúc bằng dấu cách.
    const sentence = `${"a".repeat(41)} ${"b".repeat(40)}.`;
    const label = sentenceLabel(sentence);
    expect(label).toBe(`${"a".repeat(41)}…`);
    expect(label.endsWith(" …")).toBe(false);
  });
});

describe("selectionOfQuote: lát xuất hiện hai lần", () => {
  it("lấy vị trí xuất hiện đầu tiên", () => {
    const quote = "Ngắt là tín hiệu";
    expect(quote).toHaveLength(16);
    expect(selectionOfQuote(`${quote} và ${quote}`, quote)).toEqual({ start: 0, end: 16 });
  });
});
