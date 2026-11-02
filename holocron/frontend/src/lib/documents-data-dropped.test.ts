/** Kiểm dạng field bỏ mềm của `PageExtraction`, `PageSummary` và `DocumentSummary` (GĐ0 0D, §5.7). */

import { describe, expect, it } from "vitest";

import { INITIAL_PAGES, MOCK_DOCUMENT_SCENARIO } from "@/mocks/fixtures/documents";

import { parseDocumentImport, parseDocumentSummaries, parsePageExtraction } from "./documents-data";

const PAGE = INITIAL_PAGES[0];

function droppedPage(extra: Record<string, unknown> = {}): unknown {
  return {
    ...structuredClone(PAGE),
    page_role: "chapter_nav",
    dropped: true,
    role_evidence: {
      rule: "chapter_nav",
      related_pages: [
        { document_id: "doc-x", page_number: 2 },
        { document_id: "", page_number: 3 },
        { document_id: "doc-y", page_number: 0 },
        "rác",
      ],
      jaccard: 0.9,
      dhash_distance: null,
    },
    ...extra,
  };
}

describe("parsePageExtraction: trang bỏ mềm", () => {
  it("đọc dropped, page_role, role_evidence; bỏ trang liên quan hỏng; không giữ số đo", () => {
    const view = parsePageExtraction(droppedPage());
    expect(view).toMatchObject({ dropped: true, restored: false, pageRole: "chapter_nav" });
    expect(view?.roleEvidence).toEqual({ rule: "chapter_nav", relatedPages: [{ documentId: "doc-x", pageNumber: 2 }] });
  });

  it("trang thường: không bỏ mềm, vai trò content, không bằng chứng", () => {
    expect(parsePageExtraction(structuredClone(PAGE))).toMatchObject({
      dropped: false,
      restored: false,
      pageRole: "content",
      roleEvidence: null,
    });
  });

  it("field thiếu hoặc sai kiểu không làm sập: mặc định an toàn", () => {
    const view = parsePageExtraction(
      droppedPage({ dropped: "yes", restored: 1, page_role: 7, role_evidence: { related_pages: "x" } }),
    );
    expect(view).toMatchObject({ dropped: false, restored: false, pageRole: "content", roleEvidence: null });
  });

  it("rule lạ được giữ dạng chuỗi để lớp nhãn xử lý", () => {
    const view = parsePageExtraction(droppedPage({ role_evidence: { rule: "rule_v9", related_pages: [] } }));
    expect(view?.roleEvidence).toEqual({ rule: "rule_v9", relatedPages: [] });
  });
});

describe("parseDocumentImport: tóm tắt trang", () => {
  it("đọc dropped và page_role của từng trang", () => {
    const base = structuredClone(MOCK_DOCUMENT_SCENARIO.initial.document);
    const pages = base.pages.map((page, index) =>
      index === 0 ? { ...page, dropped: true, page_role: "build_step" } : page,
    );
    const view = parseDocumentImport({ ...base, pages });
    expect(view?.pages[0]).toMatchObject({ dropped: true, pageRole: "build_step" });
    expect(view?.pages[1]).toMatchObject({ dropped: false, pageRole: "content" });
  });

  it("backend cũ không trả field GĐ0: tóm tắt trang không có dropped và pageRole (vắng = trang thường)", () => {
    const base = structuredClone(MOCK_DOCUMENT_SCENARIO.initial.document);
    const pages = base.pages.map(({ dropped: _d, page_role: _r, ...rest }) => rest);
    const view = parseDocumentImport({ ...base, pages });
    expect(Object.keys(view?.pages[0] ?? {})).not.toContain("dropped");
    expect(view?.pages[0]?.dropped).toBeUndefined();
  });
});

describe("parseDocumentSummaries: pages_dropped", () => {
  const summary = structuredClone(MOCK_DOCUMENT_SCENARIO.initial.summary);

  it("giữ nguyên số backend trả, kể cả 0", () => {
    expect(parseDocumentSummaries([{ ...summary, pages_dropped: 4 }])?.[0]?.pagesDropped).toBe(4);
    expect(parseDocumentSummaries([{ ...summary, pages_dropped: 0 }])?.[0]?.pagesDropped).toBe(0);
  });

  it("pages_dropped vắng hoặc không hợp lệ thì bỏ field, tóm tắt vẫn dùng được", () => {
    for (const bad of [undefined, -1, 1.5, "3", null]) {
      const list = parseDocumentSummaries([{ ...summary, pages_dropped: bad }]);
      expect(list).toHaveLength(1);
      expect(list?.[0]?.pagesDropped).toBeUndefined();
    }
  });

  it("không có field pagesKept nào (frontend không tự tính số trang giữ lại)", () => {
    expect(Object.keys(parseDocumentSummaries([summary])?.[0] ?? {})).not.toContain("pagesKept");
  });
});
