import { describe, expect, it } from "vitest";

import {
  type ChunkView,
  checkSearchText,
  chunkQueryString,
  groupByChapter,
  hasNextPage,
  labelStatusLabel,
  pageRangeLabel,
  parseChapterFilter,
  parseChunkList,
  parseKnowledgeChunk,
  parseSearchHits,
  parseVaultLocation,
  previousOffset,
  searchQueryString,
  vaultHref,
} from "./vault";

const CHUNK = {
  chunk_id: "chk_1",
  doc_id: "doc_0123456789abcdef",
  subject_code: "MAU101",
  chapter_code: "ch1",
  page_number: 3,
  page_end: null,
  raw_text: "Văn bản đã xác nhận của trang ba.",
  verbatim_quote: "Văn bản đã xác nhận",
  category: "THEORY",
  tags: ["#theory"],
  topic_keys: [],
  linked_chunk_ids: [],
  token_count: 8,
  embedding_model: null,
  label_status: "proposed",
  mapping_confidence: null,
  version: 1,
};

function view(chunkId: string, chapterCode: string): ChunkView {
  const parsed = parseKnowledgeChunk({ ...CHUNK, chunk_id: chunkId, chapter_code: chapterCode });
  if (parsed === null) throw new Error("chunk mẫu hỏng");
  return parsed;
}

describe("kiểm dạng chunk và kết quả tìm", () => {
  it("đọc KnowledgeChunk; nhãn và trạng thái lạ không làm sập", () => {
    expect(parseKnowledgeChunk(CHUNK)).toMatchObject({ chunkId: "chk_1", labelStatus: "proposed", pageEnd: null });
    const odd = parseKnowledgeChunk({ ...CHUNK, label_status: "draft", category: 3, topic_keys: ["a.b", 7] });
    expect(odd).toMatchObject({ labelStatus: "draft", category: "unknown", topicKeys: ["a.b"] });
    expect(labelStatusLabel("draft")).toBe("Trạng thái nhãn chưa rõ");
    expect(parseKnowledgeChunk({ ...CHUNK, raw_text: "" })).toBeNull();
    expect(parseKnowledgeChunk({ ...CHUNK, page_number: 0 })).toBeNull();
  });

  it("danh sách chunk: bỏ phần tử hỏng và báo, giữ total của backend", () => {
    const list = parseChunkList({ total: 9, chunks: [CHUNK, { chunk_id: "x" }] });
    expect(list).toMatchObject({ total: 9, hasSkipped: true });
    expect(list?.chunks).toHaveLength(1);
    expect(parseChunkList({ chunks: [] })).toBeNull();
  });

  it("kết quả tìm: bỏ score, giữ index", () => {
    const result = parseSearchHits([
      { chunk: CHUNK, score: 3.2, index: "no_diacritics" },
      { chunk: null, score: 1, index: "primary" },
    ]);
    expect(result?.hits).toHaveLength(1);
    expect(result?.hits[0]?.index).toBe("no_diacritics");
    expect(JSON.stringify(result)).not.toContain("score");
    expect(result?.hasSkipped).toBe(true);
    expect(parseSearchHits({})).toBeNull();
  });
});

describe("query của Vault (§6.6)", () => {
  it("chunks: subject_code bắt buộc, topic_key lặp, bỏ tham số trống", () => {
    const query = chunkQueryString({
      subjectCode: "MAU101",
      chapterCode: "ch2.1",
      labelStatus: "confirmed",
      topicKeys: ["a.b", "c.d"],
      offset: 50,
      limit: 50,
    });
    const params = new URLSearchParams(query);
    expect(params.get("subject_code")).toBe("MAU101");
    expect(params.get("chapter_code")).toBe("ch2.1");
    expect(params.getAll("topic_key")).toEqual(["a.b", "c.d"]);
    expect(params.get("label_status")).toBe("confirmed");
    expect(params.get("offset")).toBe("50");
    const bare = new URLSearchParams(
      chunkQueryString({
        subjectCode: "MAU101",
        chapterCode: null,
        labelStatus: null,
        topicKeys: [],
        offset: 0,
        limit: 50,
      }),
    );
    expect(bare.has("chapter_code")).toBe(false);
    expect(bare.has("label_status")).toBe(false);
  });

  it("search: mã hoá đúng chữ tiếng Việt", () => {
    const params = new URLSearchParams(
      searchQueryString({ q: "ôn tập & kiểm tra", subjectCode: "MAU101", topicKeys: [], limit: 20 }),
    );
    expect(params.get("q")).toBe("ôn tập & kiểm tra");
    expect(params.get("limit")).toBe("20");
  });

  it("từ cần tìm 1–200 ký tự sau khi bỏ khoảng trắng hai đầu", () => {
    expect(checkSearchText("  ôn tập ")).toEqual({ ok: true, q: "ôn tập" });
    expect(checkSearchText("   ").ok).toBe(false);
    expect(checkSearchText("a".repeat(200)).ok).toBe(true);
    expect(checkSearchText("a".repeat(201)).ok).toBe(false);
  });

  it("lọc chương: rỗng là không lọc, sai mẫu là lỗi", () => {
    expect(parseChapterFilter(" ")).toEqual({ ok: true, chapter: null });
    expect(parseChapterFilter("ch3")).toEqual({ ok: true, chapter: "ch3" });
    expect(parseChapterFilter("3").ok).toBe(false);
  });

  it("phân trang theo total của backend", () => {
    expect(hasNextPage(0, 50, 120)).toBe(true);
    expect(hasNextPage(100, 20, 120)).toBe(false);
    expect(hasNextPage(0, 0, 120)).toBe(false);
    expect(previousOffset(50, 50)).toBe(0);
    expect(previousOffset(20, 50)).toBe(0);
  });
});

describe("nhóm chunk và nhãn trang", () => {
  it("nhóm theo chương theo thứ tự xuất hiện, giữ thứ tự backend trong nhóm", () => {
    const groups = groupByChapter([view("a", "ch2"), view("b", "ch1"), view("c", "ch2")]);
    expect(groups.map((g) => [g.chapterCode, g.chunks.map((c) => c.chunkId)])).toEqual([
      ["ch2", ["a", "c"]],
      ["ch1", ["b"]],
    ]);
  });

  it("nhãn trang dùng đúng số backend trả", () => {
    expect(pageRangeLabel({ pageNumber: 3, pageEnd: null })).toBe("tr. 3");
    expect(pageRangeLabel({ pageNumber: 3, pageEnd: 3 })).toBe("tr. 3");
    expect(pageRangeLabel({ pageNumber: 3, pageEnd: 5 })).toBe("tr. 3–5");
  });
});

describe("địa chỉ màn /vault", () => {
  it("đọc query hợp lệ, bỏ giá trị sai mẫu", () => {
    const params = new URLSearchParams("subject=MAU101&doc=doc_0123456789abcdef&page=4&tab=chunks");
    expect(parseVaultLocation(params)).toEqual({
      subject: "MAU101",
      doc: "doc_0123456789abcdef",
      page: 4,
      tab: "chunks",
    });
    const bad = new URLSearchParams("subject=mau&doc=../x&page=0&tab=khac");
    expect(parseVaultLocation(bad)).toEqual({ subject: null, doc: null, page: null, tab: "documents" });
    expect(parseVaultLocation(new URLSearchParams("page=1e3")).page).toBeNull();
  });

  it("dựng địa chỉ tĩnh với query string, tab mặc định không ghi", () => {
    expect(vaultHref({})).toBe("/vault");
    expect(vaultHref({ subject: "MAU101" })).toBe("/vault?subject=MAU101");
    expect(vaultHref({ subject: "MAU101", tab: "chunks" })).toBe("/vault?subject=MAU101&tab=chunks");
    expect(vaultHref({ subject: "MAU101", doc: "doc_0123456789abcdef", page: 2 })).toBe(
      "/vault?subject=MAU101&doc=doc_0123456789abcdef&page=2",
    );
  });
});
