import { describe, expect, it } from "vitest";

import { ApiError, classifyNetworkError, type ErrorKind } from "./errors";
import { createLatestCounter } from "./latest";
import {
  type ChunkLookup,
  canSubmit,
  chaptersOf,
  chunksToPick,
  collapseWhitespace,
  INITIAL_SUBJECT_ENTRY,
  loadChunkMap,
  lookupChunk,
  lookupLabel,
  normalizeQuote,
  PY_COLLAPSE_CHARS,
  PY_STRIP_CHARS,
  pairActions,
  pairCitation,
  pairErrorAction,
  pairsHref,
  parseConceptPair,
  parseConceptPairList,
  parsePairsLocation,
  pyStrip,
  quoteLengthOk,
  runLatest,
  selectionRange,
  selectionToQuote,
  statusAfterCreate,
  subjectEntryStep,
  termLengthOk,
  termSegments,
} from "./pairs";
import type { ChunkListView, ChunkView } from "./vault";

/** Các ký tự `str.isspace()` của Python 3.12 (sinh bằng `[hex(c) for c in range(0x110000) if chr(c).isspace()]`). */
const PYTHON_ISSPACE = [
  0x9, 0xa, 0xb, 0xc, 0xd, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004,
  0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
];

function codes(text: string): number[] {
  return [...text].map((char) => char.codePointAt(0) ?? -1);
}

function params(values: Record<string, string>): { get: (name: string) => string | null } {
  return { get: (name) => values[name] ?? null };
}

function chunk(id: string, chapter: string, overrides: Partial<ChunkView> = {}): ChunkView {
  return {
    chunkId: id,
    docId: "doc_0123456789abcdef",
    subjectCode: "MAU101",
    chapterCode: chapter,
    pageNumber: 3,
    pageEnd: null,
    rawText: `Văn bản của ${id}.`,
    verbatimQuote: "Văn bản",
    category: "THEORY",
    topicKeys: [],
    labelStatus: "confirmed",
    version: 1,
    ...overrides,
  };
}

const PAIR_BODY = {
  pair_id: "cp_1",
  chunk_id: "chk_1",
  subject_code: "MAU101",
  chapter_code: "ch1",
  term: "tiến trình",
  definition_quote: "Tiến trình là chương trình đang chạy.",
  term_spans: [[0, 10]],
  status: "proposed",
  proposed_by: "llm",
  mapping_confidence: 0.8,
  version: 2,
  created_at: "2026-10-28T00:00:00Z",
  confirmed_at: null,
};

describe("khoảng trắng như Python", () => {
  it("tập bỏ hai đầu đúng tập str.isspace() của Python: có U+001C–001F, U+0085, không có U+FEFF", () => {
    expect(codes(PY_STRIP_CHARS)).toEqual(PYTHON_ISSPACE);
    expect(PY_STRIP_CHARS).not.toContain("\ufeff");
    expect(pyStrip("\u001c\u0085 thuật ngữ \u3000\u001f")).toBe("thuật ngữ");
    expect(pyStrip("\ufeffthuật ngữ\ufeff")).toBe("\ufeffthuật ngữ\ufeff");
    expect(pyStrip("  a  b  ")).toBe("a  b");
    expect(pyStrip(" \t\n")).toBe("");
  });

  it("tập gộp đúng tập \\s của re Python: có U+001C–001F, U+0085, không có U+FEFF", () => {
    expect(codes(PY_COLLAPSE_CHARS)).toEqual(PYTHON_ISSPACE);
    expect(PY_COLLAPSE_CHARS).not.toContain("\ufeff");
    expect(collapseWhitespace(" a\u001c\u001d b\u0085\r\nc ")).toBe("a b c");
    expect(collapseWhitespace("a\ufeff b")).toBe("a\ufeff b");
  });

  it("normalizeQuote: NFC rồi gộp khoảng trắng", () => {
    expect(normalizeQuote("Tiến   trình")).toBe("Tiến trình");
  });
});

describe("độ dài term và definition_quote", () => {
  it("term 1–120 code point sau khi bỏ khoảng trắng hai đầu", () => {
    expect(termLengthOk("   ")).toMatchObject({ ok: false, message: "Nhập thuật ngữ." });
    expect(termLengthOk(" a ")).toEqual({ ok: true });
    expect(termLengthOk(`  ${"𝑥".repeat(120)}  `)).toEqual({ ok: true });
    const tooLong = termLengthOk("ă".repeat(121));
    expect(tooLong.ok).toBe(false);
    if (!tooLong.ok) expect(tooLong.message).toContain("120");
  });

  it("601 code point trước khi gộp nhưng ngắn hơn 600 sau khi gộp ⇒ không hợp lệ (backend kiểm trước khi gộp)", () => {
    const quote = `a${" ".repeat(599)}b`;
    expect([...quote].length).toBe(601);
    expect(normalizeQuote(quote).length).toBe(3);
    const check = quoteLengthOk(quote);
    expect(check.ok).toBe(false);
    if (!check.ok) expect(check.message).toContain("600");
  });

  it("600 code point sau khi bỏ hai đầu là hợp lệ; khoảng trắng hai đầu không tính", () => {
    expect(quoteLengthOk(`  ${"x".repeat(600)}\n`)).toEqual({ ok: true });
  });

  it("NFD: đếm trước NFC cho giới hạn trên, sau NFC cho giới hạn dưới", () => {
    const nfd = "ế".normalize("NFD"); // 3 code point, NFC còn 1
    expect([...nfd].length).toBe(3);
    const longNfd = nfd.repeat(201); // 603 code point dạng NFD, 201 sau NFC
    expect(quoteLengthOk(longNfd).ok).toBe(false);
    const shortNfd = nfd.repeat(3); // 9 code point dạng NFD, 3 sau NFC
    const short = quoteLengthOk(shortNfd);
    expect(short.ok).toBe(false);
    if (!short.ok) expect(short.message).toContain("8");
    expect(quoteLengthOk(nfd.repeat(8)).ok).toBe(true);
  });

  it("dưới 8 ký tự sau khi gộp khoảng trắng, hoặc rỗng ⇒ không hợp lệ, câu báo đúng nguyên nhân", () => {
    const spaced = quoteLengthOk("a b    c d");
    expect(spaced.ok).toBe(false);
    if (!spaced.ok) expect(spaced.message).toContain("quá ngắn");
    const empty = quoteLengthOk(" \n ");
    expect(empty.ok).toBe(false);
    if (!empty.ok) expect(empty.message).toContain("Bôi");
  });
});

describe("selectionRange và selectionToQuote", () => {
  const raw = "Tiến trình là một chương trình đang chạy.";

  it("chuẩn hoá bôi ngược", () => {
    expect(selectionRange(raw, raw, 14, 4, true)).toEqual({ start: 4, end: 14 });
    expect(selectionRange(raw, raw, 4, 14, true)).toEqual({ start: 4, end: 14 });
  });

  it("tràn khỏi text node, node khác văn bản chunk, vị trí ngoài biên ⇒ null", () => {
    expect(selectionRange(raw, raw, 0, 3, false)).toBeNull();
    expect(selectionRange(raw, "văn bản khác", 0, 3, true)).toBeNull();
    expect(selectionRange(raw, null, 0, 3, true)).toBeNull();
    expect(selectionRange(raw, raw, 0, raw.length + 1, true)).toBeNull();
    expect(selectionRange(raw, raw, -1, 2, true)).toBeNull();
  });

  it("không cắt đôi ký tự ngoài BMP", () => {
    const text = "a𝑥b";
    expect(selectionRange(text, text, 2, 3, true)).toEqual({ start: 1, end: 3 });
    expect(selectionRange(text, text, 0, 2, true)).toEqual({ start: 0, end: 3 });
  });

  it("selectionToQuote trả đúng lát, chỉ bỏ khoảng trắng hai đầu", () => {
    expect(selectionToQuote(raw, 0, 10)).toBe("Tiến trình");
    expect(selectionToQuote(raw, 3, 3)).toBe("");
    expect(selectionToQuote("a   \n  b", 1, 7)).toBe("");
    expect(selectionToQuote("x  dòng 1\r\ndòng 2  y", 1, 18)).toBe("dòng 1\r\ndòng 2");
  });

  it("NFC hay NFD: giữ nguyên lát, không chuẩn hoá", () => {
    const nfd = "Tiến trình".normalize("NFD");
    const quote = selectionToQuote(nfd, 0, nfd.length);
    expect(quote).toBe(nfd);
    expect(quote).not.toBe(nfd.normalize("NFC"));
    const nfc = "Tiến trình".normalize("NFC");
    expect(selectionToQuote(nfc, 0, nfc.length)).toBe(nfc);
  });
});

describe("termSegments", () => {
  it("đổi vị trí code point sang UTF-16 khi có ký tự ngoài BMP trước term", () => {
    const quote = "𝑥 là biến";
    // Python: "𝑥" là 1 code point nên "biến" ở [5, 9); trong UTF-16 "𝑥" chiếm 2 đơn vị.
    expect(termSegments(quote, [[5, 9]])).toEqual([[6, 10]]);
    const [range] = termSegments(quote, [[5, 9]]);
    expect(range && quote.slice(range[0], range[1])).toBe("biến");
  });

  it("chuỗi có dấu (NFC): vị trí không đổi", () => {
    const quote = "Tiến trình là đơn vị";
    expect(termSegments(quote, [[0, 10]])).toEqual([[0, 10]]);
  });

  it("bỏ span sai dạng, rỗng, ngoài biên, chồng nhau", () => {
    const quote = "abc abc abc";
    const spans: unknown[] = [[8, 11], [0, 3], [2, 5], [5, 5], [9, 20], ["a", 1], [1], null, [4, 7]];
    expect(termSegments(quote, spans)).toEqual([
      [0, 3],
      [4, 7],
      [8, 11],
    ]);
  });
});

describe("parseConceptPair và parseConceptPairList", () => {
  it("cặp đúng dạng giữ nguyên field", () => {
    expect(parseConceptPair(PAIR_BODY)).toEqual(PAIR_BODY);
  });

  it("thiếu version, status lạ, proposed_by lạ ⇒ bỏ", () => {
    const { version: _version, ...noVersion } = PAIR_BODY;
    expect(parseConceptPair(noVersion)).toBeNull();
    expect(parseConceptPair({ ...PAIR_BODY, version: 0 })).toBeNull();
    expect(parseConceptPair({ ...PAIR_BODY, status: "archived" })).toBeNull();
    expect(parseConceptPair({ ...PAIR_BODY, proposed_by: "bot" })).toBeNull();
    expect(parseConceptPair({ ...PAIR_BODY, definition_quote: "  " })).toBeNull();
    expect(parseConceptPair("cp_1")).toBeNull();
  });

  it("term_spans không phải mảng ⇒ coi như rỗng; phần tử sai dạng bị bỏ", () => {
    expect(parseConceptPair({ ...PAIR_BODY, term_spans: "0-10" })?.term_spans).toEqual([]);
    expect(parseConceptPair({ ...PAIR_BODY, term_spans: [[0, 10], [1], "x"] })?.term_spans).toEqual([[0, 10]]);
  });

  it("danh sách giữ thứ tự backend, báo có phần tử bị bỏ; thân không phải mảng ⇒ null", () => {
    const second = { ...PAIR_BODY, pair_id: "cp_0", term: "a" };
    const list = parseConceptPairList([PAIR_BODY, { pair_id: "x" }, second]);
    expect(list?.pairs.map((pair) => pair.pair_id)).toEqual(["cp_1", "cp_0"]);
    expect(list?.hasSkipped).toBe(true);
    expect(parseConceptPairList([])).toEqual({ pairs: [], hasSkipped: false });
    expect(parseConceptPairList({ pairs: [] })).toBeNull();
  });
});

describe("parsePairsLocation và pairsHref", () => {
  it("đọc giá trị đúng mẫu", () => {
    expect(
      parsePairsLocation(params({ subject: "MAU101", chapter: "ch2.1", status: "rejected", chunk: "chk_1" })),
    ).toEqual({ subject: "MAU101", chapter: "ch2.1", status: "rejected", chunk: "chk_1" });
  });

  it("giá trị sai bị bỏ, không ném lỗi", () => {
    expect(
      parsePairsLocation(params({ subject: "mau101", chapter: "chuong1", status: "done", chunk: "x".repeat(129) })),
    ).toEqual({ subject: null, chapter: null, status: null, chunk: null });
    expect(parsePairsLocation(params({ chunk: "" })).chunk).toBeNull();
    expect(parsePairsLocation(params({ chunk: " chk " })).chunk).toBeNull();
    expect(parsePairsLocation(params({ chunk: "𝑥".repeat(128) })).chunk).toBe("𝑥".repeat(128));
    expect(parsePairsLocation(params({}))).toEqual({ subject: null, chapter: null, status: null, chunk: null });
  });

  it("pairsHref chỉ ghi tham số có giá trị; đọc lại ra đúng địa chỉ", () => {
    expect(pairsHref({})).toBe("/pairs");
    const href = pairsHref({ subject: "MAU101", chapter: null, status: "confirmed", chunk: "chk 1&x" });
    expect(href).toBe("/pairs?subject=MAU101&status=confirmed&chunk=chk+1%26x");
    const back = parsePairsLocation(new URLSearchParams(href.slice("/pairs?".length)));
    expect(back).toEqual({ subject: "MAU101", chapter: null, status: "confirmed", chunk: "chk 1&x" });
  });
});

function page(chunks: ChunkView[], total: number, received = chunks.length): ChunkListView {
  return { total, chunks, received, hasSkipped: received !== chunks.length };
}

describe("loadChunkMap", () => {
  it("trang rỗng ⇒ complete", async () => {
    const offsets: number[] = [];
    const result = await loadChunkMap(
      (offset) => {
        offsets.push(offset);
        return Promise.resolve(offset === 0 ? page([chunk("a", "ch1")], 5) : page([], 5));
      },
      1,
      10,
    );
    expect(result.status).toBe("complete");
    expect([...result.map.keys()]).toEqual(["a"]);
    expect(offsets).toEqual([0, 1]);
  });

  it("hết total ⇒ complete, giữ thứ tự backend; phần tử sai dạng vẫn tính vào offset", async () => {
    const offsets: number[] = [];
    const result = await loadChunkMap(
      (offset, limit) => {
        offsets.push(offset);
        expect(limit).toBe(2);
        if (offset === 0) return Promise.resolve(page([chunk("b", "ch2"), chunk("a", "ch1")], 3));
        return Promise.resolve(page([], 3, 1));
      },
      2,
      10,
    );
    expect(result.status).toBe("complete");
    expect([...result.map.keys()]).toEqual(["b", "a"]);
    expect(offsets).toEqual([0, 2]);
  });

  it("chạm trần request mà còn trang ⇒ partial, không gọi thêm", async () => {
    let calls = 0;
    const result = await loadChunkMap(
      (offset) => {
        calls += 1;
        return Promise.resolve(page([chunk(`c${offset}`, "ch1")], 100));
      },
      1,
      3,
    );
    expect(result.status).toBe("partial");
    expect(calls).toBe(3);
    expect(result.map.size).toBe(3);
  });

  it("trang cuối đúng lúc chạm trần ⇒ complete", async () => {
    const result = await loadChunkMap((offset) => Promise.resolve(page([chunk(`c${offset}`, "ch1")], 2)), 1, 2);
    expect(result.status).toBe("complete");
  });

  it("lỗi giữa chừng ⇒ error, map rỗng (không dùng map dở)", async () => {
    const result = await loadChunkMap(
      (offset) =>
        offset === 0
          ? Promise.resolve(page([chunk("a", "ch1")], 5))
          : Promise.reject(new ApiError(null, classifyNetworkError(""))),
      1,
      10,
    );
    expect(result.status).toBe("error");
    expect(result.map.size).toBe(0);
    if (result.status === "error") expect(result.error.kind).toBe("network");
  });
});

describe("tra chunk và trích dẫn của cặp", () => {
  const map = new Map([
    ["a", chunk("a", "ch1", { pageNumber: 7, pageEnd: 8 })],
    ["b", chunk("b", "ch2")],
    ["c", chunk("c", "ch1")],
  ]);

  it("found / missing (map đủ) / not_loaded (map chưa đủ) / unknown (đang tải, lỗi)", () => {
    expect(lookupChunk("complete", map, "a").kind).toBe("found");
    expect(lookupChunk("complete", map, "z").kind).toBe("missing");
    expect(lookupChunk("partial", map, "z").kind).toBe("not_loaded");
    expect(lookupChunk("partial", map, "b").kind).toBe("found");
    expect(lookupChunk("loading", map, "a").kind).toBe("unknown");
    expect(lookupChunk("error", map, "a").kind).toBe("unknown");
    expect(lookupLabel({ kind: "missing" })).toContain("Không tìm thấy đoạn nguồn");
    expect(lookupLabel({ kind: "not_loaded" })).toContain("Chưa tải hết các đoạn của môn");
    expect(lookupLabel({ kind: "not_loaded" })).not.toContain("Không tìm thấy");
  });

  it("trích dẫn dùng definition_quote và chunk_id của cặp, doc và trang của chunk", () => {
    const pair = parseConceptPair({ ...PAIR_BODY, chunk_id: "a" });
    const source = map.get("a");
    expect(pair).not.toBeNull();
    if (pair === null || source === undefined) return;
    expect(pairCitation(pair, source)).toEqual({
      doc_id: "doc_0123456789abcdef",
      page_number: 7,
      verbatim_quote: PAIR_BODY.definition_quote,
      chunk_id: "a",
    });
  });

  it("chương gợi ý và chunk để chọn: đủ chương, hoặc vài chunk đầu khi chưa chọn chương", () => {
    expect(chaptersOf(map)).toEqual(["ch1", "ch2"]);
    expect(chunksToPick(map, "ch1", 1)).toEqual({ chunks: [map.get("a"), map.get("c")], truncated: false });
    expect(chunksToPick(map, null, 2)).toEqual({ chunks: [map.get("a"), map.get("b")], truncated: true });
    expect(chunksToPick(map, null, 3).truncated).toBe(false);
  });
});

describe("thao tác theo trạng thái cặp và chunk nguồn", () => {
  const found: ChunkLookup = { kind: "found", chunk: chunk("a", "ch1") };

  it("đủ chunk: chờ xác nhận có Sửa/Xác nhận/Từ chối; đã xác nhận có Sửa/Từ chối; đã từ chối chỉ Xác nhận lại", () => {
    expect(pairActions("proposed", found)).toEqual({
      edit: { shown: true, enabled: true },
      confirm: { shown: true, enabled: true },
      reject: { shown: true, enabled: true },
    });
    expect(pairActions("confirmed", found)).toEqual({
      edit: { shown: true, enabled: true },
      confirm: { shown: false },
      reject: { shown: true, enabled: true },
    });
    expect(pairActions("rejected", found)).toEqual({
      edit: { shown: false },
      confirm: { shown: true, enabled: true },
      reject: { shown: false },
    });
  });

  it("map chưa đủ hoặc không thấy chunk: Sửa, Xác nhận vô hiệu kèm lý do; Từ chối vẫn được", () => {
    const blocked: ChunkLookup[] = [{ kind: "not_loaded" }, { kind: "missing" }, { kind: "unknown" }];
    for (const lookup of blocked) {
      const actions = pairActions("proposed", lookup);
      expect(actions.edit).toMatchObject({ shown: true, enabled: false });
      expect(actions.confirm).toMatchObject({ shown: true, enabled: false });
      expect(actions.reject).toEqual({ shown: true, enabled: true });
      if (actions.confirm.shown && !actions.confirm.enabled) expect(actions.confirm.reason).not.toBe("");
    }
    expect(pairActions("rejected", { kind: "missing" }).confirm).toMatchObject({ enabled: false });
  });

  it("canSubmit khoá khi request đang chạy", () => {
    expect(canSubmit({ pending: true })).toBe(false);
    expect(canSubmit({ pending: false })).toBe(true);
  });

  it("sau khi tạo cặp: bộ lọc đang ẩn cặp đã xác nhận thì chuyển sang đã xác nhận", () => {
    expect(statusAfterCreate("proposed")).toBe("confirmed");
    expect(statusAfterCreate("rejected")).toBe("confirmed");
    expect(statusAfterCreate("confirmed")).toBe("confirmed");
    expect(statusAfterCreate(null)).toBeNull();
  });
});

describe("pairErrorAction", () => {
  const network = classifyNetworkError("");

  it("tạo cặp: lỗi mạng, máy chủ, kho bận ⇒ Tải lại danh sách, không Thử lại (tránh tạo trùng)", () => {
    const kinds: ErrorKind[] = ["network", "server", "busy"];
    for (const kind of kinds) {
      expect(pairErrorAction("create", { ...network, kind })).toEqual({
        kind: "reload_list",
        label: "Tải lại danh sách",
      });
    }
  });

  it("xác nhận, từ chối: lỗi tạm thời ⇒ Thử lại", () => {
    expect(pairErrorAction("confirm", network)).toEqual({ kind: "retry", label: "Thử lại" });
    expect(pairErrorAction("reject", { ...network, kind: "busy" })).toEqual({ kind: "retry", label: "Thử lại" });
  });

  it("404 ⇒ tải lại cả map và danh sách; 409 ⇒ tải lại danh sách; 422 ⇒ không có nút", () => {
    expect(pairErrorAction("reject", { ...network, kind: "not_found" })?.kind).toBe("reload_all");
    expect(pairErrorAction("create", { ...network, kind: "not_found" })?.kind).toBe("reload_all");
    expect(pairErrorAction("confirm", { ...network, kind: "version_conflict" })?.kind).toBe("reload_list");
    expect(pairErrorAction("confirm", { ...network, kind: "validation" })).toBeNull();
  });
});

describe("runLatest", () => {
  function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
    let resolve: (value: T) => void = () => undefined;
    const promise = new Promise<T>((done) => {
      resolve = done;
    });
    return { promise, resolve };
  }

  it("phản hồi cũ về sau phản hồi mới bị bỏ", async () => {
    const counter = createLatestCounter();
    const applied: string[] = [];
    const first = deferred<string>();
    const second = deferred<string>();
    const runFirst = runLatest(
      counter,
      () => first.promise,
      (outcome) => {
        if (outcome.ok) applied.push(outcome.value);
      },
    );
    const runSecond = runLatest(
      counter,
      () => second.promise,
      (outcome) => {
        if (outcome.ok) applied.push(outcome.value);
      },
    );
    second.resolve("mới");
    await runSecond;
    first.resolve("cũ");
    await runFirst;
    expect(applied).toEqual(["mới"]);
  });

  it("lỗi được phân loại; invalidate (gỡ màn, đổi môn) bỏ cả lượt đang chờ", async () => {
    const counter = createLatestCounter();
    const outcomes: string[] = [];
    await runLatest(
      counter,
      () => Promise.reject(new ApiError(null, classifyNetworkError(""))),
      (outcome) => outcomes.push(outcome.ok ? "ok" : outcome.error.kind),
    );
    const pending = deferred<number>();
    const run = runLatest(
      counter,
      () => pending.promise,
      () => outcomes.push("muộn"),
    );
    counter.invalidate();
    pending.resolve(1);
    await run;
    expect(outcomes).toEqual(["network"]);
  });
});

describe("subjectEntryStep", () => {
  it("vào trang với ?subject= khác thanh bên: chọn môn đúng một lần, chạy lại (StrictMode) không reset", () => {
    const first = subjectEntryStep(INITIAL_SUBJECT_ENTRY, "MAU101", "OLD200");
    expect(first.action).toEqual({ kind: "select", subject: "MAU101" });
    expect(first.ready).toBe(false);
    // Effect chạy lại với cùng đầu vào trước khi thanh bên kịp đổi.
    const rerun = subjectEntryStep(first.state, "MAU101", "OLD200");
    expect(rerun.action).toEqual({ kind: "none" });
    expect(rerun.ready).toBe(false);
    // Thanh bên đã áp môn: ổn định, không reset.
    const applied = subjectEntryStep(rerun.state, "MAU101", "MAU101");
    expect(applied).toMatchObject({ action: { kind: "none" }, ready: true });
    expect(subjectEntryStep(applied.state, "MAU101", "MAU101").action).toEqual({ kind: "none" });
  });

  it("?subject= trùng thanh bên hoặc không có: không chọn, sẵn sàng ngay; chạy lại vẫn idempotent", () => {
    const same = subjectEntryStep(INITIAL_SUBJECT_ENTRY, "MAU101", "MAU101");
    expect(same).toMatchObject({ action: { kind: "none" }, ready: true });
    expect(subjectEntryStep(same.state, "MAU101", "MAU101").action).toEqual({ kind: "none" });
    const none = subjectEntryStep(INITIAL_SUBJECT_ENTRY, null, null);
    expect(none).toMatchObject({ action: { kind: "none" }, ready: true });
    expect(subjectEntryStep(none.state, null, null).action).toEqual({ kind: "none" });
  });

  it("người dùng đổi môn ở thanh bên sau đó ⇒ reset đúng một lần; ?subject= cũ không được đọc lại", () => {
    const entered = subjectEntryStep(INITIAL_SUBJECT_ENTRY, "MAU101", "MAU101");
    const changed = subjectEntryStep(entered.state, "MAU101", "KHA202");
    expect(changed).toMatchObject({ action: { kind: "reset" }, ready: true });
    expect(subjectEntryStep(changed.state, "MAU101", "KHA202").action).toEqual({ kind: "none" });
    expect(subjectEntryStep(changed.state, null, "KHA202").action).toEqual({ kind: "none" });
  });

  it("đang chờ thanh bên mà thanh bên đổi sang môn thứ ba ⇒ coi là người dùng đổi môn", () => {
    const first = subjectEntryStep(INITIAL_SUBJECT_ENTRY, "MAU101", "OLD200");
    expect(subjectEntryStep(first.state, "MAU101", "KHA202").action).toEqual({ kind: "reset" });
  });
});
