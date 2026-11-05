import { describe, expect, it } from "vitest";

import type { Section, SectionNode, SubjectTree } from "@/types/holocron";

import { buildToc, pathLabels, readTocPosition, sourceWarning, treeKey, visibleRows, writeTocPosition } from "./toc";

function section(id: string, title: string | null, level: number): Section {
  return {
    section_id: id,
    document_id: "d1",
    parent_id: null,
    kind: level === 0 ? "chapter" : "section",
    level,
    chapter_code: null,
    title,
    heading_block_ids: [],
    block_ids: [],
    ordinal: 0,
    page_start: 1,
    page_end: 2,
    flags: [],
  };
}

const ROOT: SectionNode = {
  section: section("c", "Chương 1", 0),
  children: [
    { section: section("s1", "Mục 1.1", 1), children: [{ section: section("s11", null, 2), children: [] }] },
    { section: section("s2", "Mục 1.2", 1), children: [] },
  ],
};

const TREE: SubjectTree = {
  subject_code: "X",
  display_name: "X",
  sources: [
    {
      document_id: "d1",
      title: "Slide",
      source_kind: "lecture_slides",
      chapter_code: null,
      chapter_title: null,
      layout_version: 2,
      chunks_from_lines: false,
      chapter_shared_with: [],
      root: ROOT,
      layout_failed_pages: [],
      pages_without_blocks: [],
      pages_unconfirmable_with_blocks: [],
    },
    {
      document_id: "d2",
      title: "Rỗng",
      source_kind: "lecture_slides",
      chapter_code: null,
      chapter_title: null,
      layout_version: 2,
      chunks_from_lines: false,
      chapter_shared_with: [],
      root: null,
      layout_failed_pages: [],
      pages_without_blocks: [],
      pages_unconfirmable_with_blocks: [],
    },
  ],
};

describe("buildToc và visibleRows", () => {
  const roots = buildToc(TREE);

  it("dựng nguồn → chương → mục; mục không tiêu đề có nhãn thay thế", () => {
    expect(roots.map((node) => node.label)).toEqual(["Slide", "Rỗng"]);
    expect(pathLabels(roots, "sec:s11")).toEqual(["Slide", "Chương 1", "Mục 1.1", "(Chưa có tiêu đề)"]);
  });

  it("chỉ hiện con của nút đã mở, kèm cỡ tập và vị trí", () => {
    expect(visibleRows(roots, new Set()).map((row) => row.node.id)).toEqual(["doc:d1", "doc:d2"]);
    const rows = visibleRows(roots, new Set(["doc:d1", "sec:c"]));
    expect(rows.map((row) => [row.node.id, row.level, row.expanded])).toEqual([
      ["doc:d1", 1, true],
      ["sec:c", 2, true],
      ["sec:s1", 3, false],
      ["sec:s2", 3, null],
      ["doc:d2", 1, null],
    ]);
    expect(rows[3]).toMatchObject({ posInSet: 2, setSize: 2 });
  });
});

describe("treeKey", () => {
  const roots = buildToc(TREE);
  const rows = visibleRows(roots, new Set(["doc:d1", "sec:c"]));

  it("mũi tên di chuyển, Home/End nhảy đầu cuối", () => {
    expect(treeKey(roots, rows, "sec:c", "ArrowDown")).toEqual({ kind: "focus", id: "sec:s1" });
    expect(treeKey(roots, rows, "sec:c", "ArrowUp")).toEqual({ kind: "focus", id: "doc:d1" });
    expect(treeKey(roots, rows, "sec:c", "Home")).toEqual({ kind: "focus", id: "doc:d1" });
    expect(treeKey(roots, rows, "sec:c", "End")).toEqual({ kind: "focus", id: "doc:d2" });
    expect(treeKey(roots, rows, "doc:d2", "ArrowDown")).toBeNull();
  });

  it("→ mở nút đóng rồi vào con; ← đóng nút mở rồi về cha", () => {
    expect(treeKey(roots, rows, "sec:s1", "ArrowRight")).toEqual({ kind: "toggle", id: "sec:s1", open: true });
    expect(treeKey(roots, rows, "sec:c", "ArrowRight")).toEqual({ kind: "focus", id: "sec:s1" });
    expect(treeKey(roots, rows, "sec:c", "ArrowLeft")).toEqual({ kind: "toggle", id: "sec:c", open: false });
    expect(treeKey(roots, rows, "sec:s1", "ArrowLeft")).toEqual({ kind: "focus", id: "sec:c" });
    expect(treeKey(roots, rows, "sec:s2", "ArrowRight")).toBeNull();
  });

  it("Enter chọn; phím khác bỏ qua", () => {
    expect(treeKey(roots, rows, "sec:s2", "Enter")).toEqual({ kind: "select", id: "sec:s2" });
    expect(treeKey(roots, rows, "sec:s2", "x")).toBeNull();
    expect(treeKey(roots, rows, "khong-co", "Enter")).toBeNull();
  });
});

describe("giữ vị trí", () => {
  it("ghi rồi đọc lại; dữ liệu hỏng hoặc kho vắng thì trả rỗng", () => {
    const data = new Map<string, string>();
    const storage = {
      getItem: (k: string) => data.get(k) ?? null,
      setItem: (k: string, v: string) => void data.set(k, v),
    };
    writeTocPosition(storage, "X", { expanded: ["doc:d1"], selected: "sec:c" });
    expect(readTocPosition(storage, "X")).toEqual({ expanded: ["doc:d1"], selected: "sec:c" });
    data.set("holocron.toc.X", "{hỏng");
    expect(readTocPosition(storage, "X")).toEqual({ expanded: [], selected: null });
    expect(readTocPosition(null, "X")).toEqual({ expanded: [], selected: null });
  });
});

describe("sourceWarning", () => {
  const base = { layout_failed_pages: [], pages_without_blocks: [], pages_unconfirmable_with_blocks: [] };
  it("không cảnh báo khi cả ba rỗng", () => {
    expect(sourceWarning(base)).toBeNull();
  });
  it("cảnh báo theo số trang backend trả, kể cả nguồn root null", () => {
    expect(sourceWarning({ ...base, pages_without_blocks: [2, 5] })).toContain("2 trang chưa có khối");
    const empty = TREE.sources.filter((source) => source.root === null);
    const tree: SubjectTree = {
      ...TREE,
      sources: empty.map((source) => ({ ...source, layout_failed_pages: [1], pages_unconfirmable_with_blocks: [3] })),
    };
    expect(buildToc(tree)[0]?.warning).toContain("1 trang lỗi bố cục");
    expect(buildToc(tree)[0]?.warning).toContain("1 trang có khối nhưng chưa xác nhận được");
  });
});
