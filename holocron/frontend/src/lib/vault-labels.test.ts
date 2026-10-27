/**
 * Sửa và xác nhận nhãn chunk (§6.6 `PATCH /v1/vault/chunks/{chunk_id}/labels`): kiểm đầu vào theo giới hạn của
 * backend, body chỉ gồm field đã đổi kèm `expected_version`; phân trang theo số phần tử backend trả.
 */

import { describe, expect, it } from "vitest";

import { TOPIC_KEY_MAX_CHARS, TOPIC_KEYS_MAX } from "./limits";
import {
  buildLabelChange,
  CHUNK_CATEGORY_OPTIONS,
  type ChunkView,
  canEditLabels,
  hasNextPage,
  labelDraftOf,
  nextPageOffset,
  parseChunkList,
  parseKnowledgeChunk,
  parseTopicKeys,
} from "./vault";

const RAW_CHUNK = {
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
  topic_keys: ["mau.ch1.dinh_nghia"],
  linked_chunk_ids: [],
  token_count: 8,
  embedding_model: null,
  label_status: "proposed",
  mapping_confidence: null,
  version: 4,
};

function chunk(overrides: Record<string, unknown> = {}): ChunkView {
  const parsed = parseKnowledgeChunk({ ...RAW_CHUNK, ...overrides });
  if (parsed === null) throw new Error("chunk mẫu hỏng");
  return parsed;
}

describe("version của chunk", () => {
  it("đọc version số nguyên ≥ 1; thiếu hay sai thì chunk vẫn hiện nhưng không sửa được", () => {
    expect(chunk().version).toBe(4);
    expect(canEditLabels(chunk())).toBe(true);
    for (const version of [undefined, 0, 1.5, "4", null]) {
      const odd = chunk({ version });
      expect(odd.version).toBeNull();
      expect(canEditLabels(odd)).toBe(false);
    }
  });

  it("chunk không có version: form báo lỗi, không dựng body", () => {
    const result = buildLabelChange(chunk({ version: undefined }), labelDraftOf(chunk()));
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errors.version).toBeDefined();
  });
});

describe("parseTopicKeys", () => {
  it("tách theo dòng, dấu phẩy, khoảng trắng; bỏ rỗng và khoá trùng, giữ thứ tự", () => {
    expect(parseTopicKeys(" a.b.c\nd.e.f, a.b.c  g_1.h.i_2 \n\n")).toEqual({
      ok: true,
      keys: ["a.b.c", "d.e.f", "g_1.h.i_2"],
    });
    expect(parseTopicKeys("   ")).toEqual({ ok: true, keys: [] });
  });

  it("khoá sai mẫu của backend (ít hơn ba phần, chữ hoa, có dấu) bị từ chối", () => {
    for (const bad of ["a.b", "A.b.c", "môn.ch1.dạng", "a..b.c", "a.b.c."]) {
      expect(parseTopicKeys(bad).ok).toBe(false);
    }
  });

  it(`tối đa ${TOPIC_KEYS_MAX} khoá, mỗi khoá tối đa ${TOPIC_KEY_MAX_CHARS} ký tự`, () => {
    const many = Array.from({ length: TOPIC_KEYS_MAX }, (_, index) => `a.b.k${index}`);
    expect(parseTopicKeys(many.join("\n")).ok).toBe(true);
    expect(parseTopicKeys([...many, "a.b.extra"].join("\n")).ok).toBe(false);
    const longest = `a.b.${"x".repeat(TOPIC_KEY_MAX_CHARS - 4)}`;
    expect(parseTopicKeys(longest).ok).toBe(true);
    expect(parseTopicKeys(`${longest}x`).ok).toBe(false);
  });
});

describe("buildLabelChange", () => {
  it("không đổi gì: body chỉ có expected_version (xác nhận nhãn hiện tại)", () => {
    expect(buildLabelChange(chunk(), labelDraftOf(chunk()))).toEqual({ ok: true, change: { expected_version: 4 } });
  });

  it("chỉ gửi field đã đổi", () => {
    const draft = { ...labelDraftOf(chunk()), chapterCode: " ch2.1 " };
    expect(buildLabelChange(chunk(), draft)).toEqual({
      ok: true,
      change: { expected_version: 4, chapter_code: "ch2.1" },
    });
    const category = { ...labelDraftOf(chunk()), category: "PRACTICE" };
    expect(buildLabelChange(chunk(), category)).toEqual({
      ok: true,
      change: { expected_version: 4, category: "PRACTICE" },
    });
    const keys = { ...labelDraftOf(chunk()), topicKeysText: "mau.ch1.dinh_nghia\nmau.ch1.vi_du" };
    expect(buildLabelChange(chunk(), keys)).toEqual({
      ok: true,
      change: { expected_version: 4, topic_keys: ["mau.ch1.dinh_nghia", "mau.ch1.vi_du"] },
    });
  });

  it("xoá hết khoá dạng bài gửi topic_keys rỗng có chủ đích", () => {
    const cleared = { ...labelDraftOf(chunk()), topicKeysText: "" };
    expect(buildLabelChange(chunk(), cleared)).toEqual({ ok: true, change: { expected_version: 4, topic_keys: [] } });
    // Chunk vốn không có khoá: ô trống không gửi gì.
    const none = chunk({ topic_keys: [] });
    expect(buildLabelChange(none, labelDraftOf(none))).toEqual({ ok: true, change: { expected_version: 4 } });
  });

  it("loại nội dung lạ từ backend: ô trống nghĩa là giữ nguyên, không gửi", () => {
    const odd = chunk({ category: "DIAGRAM" });
    const draft = labelDraftOf(odd);
    expect(draft.category).toBe("");
    expect(buildLabelChange(odd, draft)).toEqual({ ok: true, change: { expected_version: 4 } });
    expect(buildLabelChange(odd, { ...draft, category: "THEORY" })).toEqual({
      ok: true,
      change: { expected_version: 4, category: "THEORY" },
    });
  });

  it("đầu vào sai: báo lỗi theo ô, không dựng body", () => {
    const result = buildLabelChange(chunk(), {
      chapterCode: "chương 1",
      category: "OTHER",
      topicKeysText: "a.b",
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(Object.keys(result.errors).sort()).toEqual(["category", "chapter", "topicKeys"]);
    }
  });

  it("lựa chọn loại nội dung có nhãn tiếng Việt cho mọi mã", () => {
    expect(CHUNK_CATEGORY_OPTIONS.map((option) => option.value)).toEqual(["THEORY", "PRACTICE"]);
    for (const option of CHUNK_CATEGORY_OPTIONS) expect(option.label).not.toBe(option.value);
  });
});

describe("phân trang theo số phần tử backend trả", () => {
  it("phần tử sai dạng bị bỏ khi hiển thị nhưng vẫn tính vào offset trang sau", () => {
    const list = parseChunkList({ total: 5, chunks: [RAW_CHUNK, { chunk_id: "hỏng" }] });
    expect(list).toMatchObject({ received: 2, hasSkipped: true });
    expect(list?.chunks).toHaveLength(1);
    const received = list?.received ?? 0;
    expect(nextPageOffset(0, received)).toBe(2);
    expect(hasNextPage(0, received, 5)).toBe(true);
    expect(hasNextPage(4, 1, 5)).toBe(false);
  });

  it("trang toàn phần tử hỏng vẫn đi tiếp được, không lặp lại trang cũ", () => {
    const list = parseChunkList({ total: 3, chunks: [{ x: 1 }, { x: 2 }] });
    expect(list?.chunks).toEqual([]);
    expect(list?.received).toBe(2);
    expect(hasNextPage(0, list?.received ?? 0, 3)).toBe(true);
    expect(nextPageOffset(0, list?.received ?? 0)).toBe(2);
  });
});
