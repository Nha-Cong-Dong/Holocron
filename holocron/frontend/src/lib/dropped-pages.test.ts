/** Trang bỏ mềm (GĐ0 0D): nhãn lý do, trang liên quan, nhóm trang, đếm và phân loại lỗi khôi phục (§5.7, §6.5). */

import { describe, expect, it } from "vitest";

import type { PageRole, PageRoleEvidence } from "@/types/holocron";

import {
  classifyRestoreConflict,
  DROPPED_GENERIC_VI,
  documentCountsLine,
  droppedReasonLabel,
  explainRestoreFailure,
  isDropped,
  PAGE_ROLE_LABEL_VI,
  RESTORE_DONE_VI,
  ROLE_RULE_LABEL_VI,
  relatedPageLinks,
  restoreFailure,
  splitDroppedPages,
  withoutDropped,
} from "./dropped-pages";
import { ApiError, classifyHttpError, classifyNetworkError } from "./errors";

// `Record<…, true>` buộc danh sách dưới đây đủ mọi giá trị của kiểu: backend thêm giá trị mới ⇒ test này không build.
const ROLES: Record<PageRole, true> = {
  content: true,
  cover_or_course_nav: true,
  chapter_nav: true,
  build_step: true,
  parallel_visual_series: true,
  exercise: true,
};
const RULES: Record<PageRoleEvidence["rule"], true> = {
  cover_or_course_nav: true,
  chapter_nav: true,
  build_step: true,
  parallel_visual_series: true,
  build_step_uncertain: true,
};
const ALL_ROLES: PageRole[] = [
  "content",
  "cover_or_course_nav",
  "chapter_nav",
  "build_step",
  "parallel_visual_series",
  "exercise",
];
const ALL_RULES: PageRoleEvidence["rule"][] = [
  "cover_or_course_nav",
  "chapter_nav",
  "build_step",
  "parallel_visual_series",
  "build_step_uncertain",
];

/** Câu hiển thị không chứa mã enum thô (dạng `snake_case`) hay tên field. */
function expectNoRawCode(text: string): void {
  expect(text).not.toMatch(/[a-z]+_[a-z_]+/);
  expect(text.trim()).not.toBe("");
}

describe("nhãn lý do", () => {
  it("danh sách giá trị của test đủ theo kiểu, bảng nhãn phủ đủ mọi PageRole và mọi rule, không lộ mã thô", () => {
    expect([...ALL_ROLES].sort()).toEqual(Object.keys(ROLES).sort());
    expect([...ALL_RULES].sort()).toEqual(Object.keys(RULES).sort());
    expect(Object.keys(PAGE_ROLE_LABEL_VI).sort()).toEqual([...ALL_ROLES].sort());
    expect(Object.keys(ROLE_RULE_LABEL_VI).sort()).toEqual([...ALL_RULES].sort());
    for (const text of [...Object.values(PAGE_ROLE_LABEL_VI), ...Object.values(ROLE_RULE_LABEL_VI)]) {
      expectNoRawCode(text);
    }
  });

  for (const rule of ALL_RULES) {
    it(`rule ${rule}: câu của rule, không lộ mã thô`, () => {
      const label = droppedReasonLabel("content", rule);
      expect(label).toBe(ROLE_RULE_LABEL_VI[rule]);
      expectNoRawCode(label);
    });
  }

  for (const role of ALL_ROLES) {
    it(`role ${role} không có rule: câu của vai trò hoặc câu chung, không lộ mã thô`, () => {
      const label = droppedReasonLabel(role, null);
      const expected = role === "content" || role === "exercise" ? DROPPED_GENERIC_VI : PAGE_ROLE_LABEL_VI[role];
      expect(label).toBe(expected);
      expectNoRawCode(label);
    });
  }

  it("rule thắng role khi hai bên khác nhau", () => {
    expect(droppedReasonLabel("chapter_nav", "build_step")).toBe(ROLE_RULE_LABEL_VI.build_step);
  });

  it("mã lạ (rule, role, tên có sẵn trong Object) rơi về câu chung, không lộ mã", () => {
    const cases: [string, string | null][] = [
      ["new_role_v9", null],
      ["new_role_v9", "new_rule_v9"],
      ["constructor", "toString"],
      ["", ""],
    ];
    for (const [role, rule] of cases) {
      const label = droppedReasonLabel(role, rule);
      expect(label).toBe(DROPPED_GENERIC_VI);
      expectNoRawCode(label);
    }
  });
});

describe("trang liên quan", () => {
  it("giữ thứ tự backend; trang tài liệu khác có ghi chú và không lộ mã tài liệu", () => {
    const links = relatedPageLinks(
      [
        { documentId: "doc-a", pageNumber: 3 },
        { documentId: "doc-b", pageNumber: 1 },
      ],
      "doc-a",
    );
    expect(links.map((link) => link.sameDocument)).toEqual([true, false]);
    expect(links[0]?.label).toBe("Trang 3");
    expect(links[1]?.label).toBe("Trang 1 (tài liệu khác của môn)");
    expect(links.map((link) => link.label).join(" ")).not.toContain("doc-b");
  });

  it("không có trang liên quan thì danh sách rỗng", () => {
    expect(relatedPageLinks([], "doc-a")).toEqual([]);
  });
});

describe("nhóm trang bỏ mềm", () => {
  const pages = [
    { pageNumber: 1, dropped: false },
    { pageNumber: 2, dropped: true },
    { pageNumber: 3 },
    { pageNumber: 4, dropped: true },
  ];

  it("tách hai nhóm giữ thứ tự; `dropped` vắng nghĩa là không bỏ mềm", () => {
    const { kept, dropped } = splitDroppedPages(pages);
    expect(kept.map((page) => page.pageNumber)).toEqual([1, 3]);
    expect(dropped.map((page) => page.pageNumber)).toEqual([2, 4]);
    expect(isDropped({})).toBe(false);
    expect(isDropped({ dropped: true })).toBe(true);
  });

  it("withoutDropped giữ field khác của tài liệu và bỏ trang bỏ mềm", () => {
    const result = withoutDropped({ status: "review", pages });
    expect(result.status).toBe("review");
    expect(result.pages.map((page) => page.pageNumber)).toEqual([1, 3]);
  });
});

describe("dòng số đếm", () => {
  const base = { pagesConfirmed: 2, pageCount: 9, pagesNeedsReview: 3, pagesFailed: 1 };

  it("chép đúng số backend trả, không tự tính số trang giữ lại", () => {
    const line = documentCountsLine({ ...base, pagesDropped: 4 });
    expect(line).toBe("2/9 trang đã xác nhận · 3 trang cần xem lại · 1 trang đọc lỗi · 4 trang đã ẩn tự động");
  });

  it("pages_dropped vắng hoặc hỏng thì bỏ đoạn đó", () => {
    expect(documentCountsLine(base)).not.toContain("ẩn tự động");
  });

  it("0 trang ẩn vẫn hiện số 0 (backend đã trả)", () => {
    expect(documentCountsLine({ ...base, pagesDropped: 0 })).toContain("0 trang đã ẩn tự động");
  });
});

describe("lỗi khôi phục", () => {
  const original = classifyHttpError(500, { detail: "x" }, { context: "document_review" });
  const network = classifyNetworkError("http://127.0.0.1:8000");

  it("409 lệch phiên bản: Trang đã đổi kèm nút tải lại", () => {
    const error = restoreFailure({ status: 409, conflict: "version_changed", original });
    expect(error.message).toContain("Trang đã đổi");
    expect(error.action).toBe("reload");
    expect(error.kind).toBe("version_conflict");
  });

  it("409 tài liệu đã chốt: không khôi phục được, không nút tải lại", () => {
    const error = restoreFailure({ status: 409, conflict: "document_closed", original });
    expect(error.message).toContain("Không khôi phục được");
    expect(error.action).toBe("none");
  });

  it("409 chưa phân loại được thì coi như lệch phiên bản (có nút tải lại)", () => {
    expect(restoreFailure({ status: 409, conflict: null, original }).action).toBe("reload");
  });

  it("404 và 422 dùng câu chung của khôi phục, không mượn câu của sửa dòng", () => {
    for (const status of [404, 422, 400]) {
      const error = restoreFailure({ status, conflict: null, original });
      expect(error.message).toContain("Chưa khôi phục được trang");
      expect(error.action).toBe("none");
      expect(error.message).not.toContain("tối đa");
    }
  });

  it("lỗi mạng và lỗi máy chủ giữ câu và nút thử lại của lỗi gốc", () => {
    expect(restoreFailure({ status: null, conflict: null, original: network })).toBe(network);
    expect(restoreFailure({ status: 500, conflict: null, original })).toBe(original);
    expect(network.action).toBe("retry");
  });

  it("không lộ mã HTTP hay enum thô", () => {
    for (const conflict of ["version_changed", "document_closed"] as const) {
      const text = restoreFailure({ status: 409, conflict, original }).message;
      expect(text).not.toMatch(/409|HTTP|[a-z]+_[a-z_]+/);
    }
    expect(RESTORE_DONE_VI).not.toMatch(/[a-z]+_[a-z_]+/);
  });
});

describe("classifyRestoreConflict", () => {
  it("trang vẫn nguyên (cùng version, còn bỏ mềm) mà backend từ chối ⇒ tài liệu đã đóng", async () => {
    expect(await classifyRestoreConflict(() => Promise.resolve({ version: 3, dropped: true }), 3)).toBe(
      "document_closed",
    );
  });

  it("version đã đổi hoặc trang không còn bỏ mềm ⇒ lệch phiên bản", async () => {
    expect(await classifyRestoreConflict(() => Promise.resolve({ version: 4, dropped: true }), 3)).toBe(
      "version_changed",
    );
    expect(await classifyRestoreConflict(() => Promise.resolve({ version: 3, dropped: false }), 3)).toBe(
      "version_changed",
    );
  });

  it("không đọc lại được trang ⇒ lệch phiên bản (an toàn: có nút tải lại)", async () => {
    expect(await classifyRestoreConflict(() => Promise.reject(new Error("mạng")), 3)).toBe("version_changed");
  });
});

describe("explainRestoreFailure", () => {
  const failure = (status: number) =>
    new ApiError(status, classifyHttpError(status, { detail: "x" }, { context: "document_review", versioned: true }));

  it("409 + trang đã đổi ⇒ Trang đã đổi, có nút tải lại", async () => {
    const error = await explainRestoreFailure(failure(409), {
      load: () => Promise.resolve({ version: 5, dropped: false }),
      expectedVersion: 4,
    });
    expect(error.message).toContain("Trang đã đổi");
    expect(error.action).toBe("reload");
  });

  it("409 + trang nguyên ⇒ tài liệu đã chốt, không nút", async () => {
    const error = await explainRestoreFailure(failure(409), {
      load: () => Promise.resolve({ version: 4, dropped: true }),
      expectedVersion: 4,
    });
    expect(error.message).toContain("Không khôi phục được");
    expect(error.action).toBe("none");
  });

  it("404 và 422 ⇒ câu chung và không đọc lại trang", async () => {
    for (const status of [404, 422]) {
      let loads = 0;
      const error = await explainRestoreFailure(failure(status), {
        load: () => {
          loads += 1;
          return Promise.resolve({ version: 1, dropped: true });
        },
        expectedVersion: 1,
      });
      expect(error.message).toContain("Chưa khôi phục được trang");
      expect(loads).toBe(0);
    }
  });

  it("lỗi mạng giữ nút thử lại; lỗi lạ không phải ApiError không làm sập", async () => {
    const network = new ApiError(null, classifyNetworkError("http://127.0.0.1:8000"));
    const load = () => Promise.resolve({ version: 1, dropped: true });
    expect((await explainRestoreFailure(network, { load, expectedVersion: 1 })).action).toBe("retry");
    const unknown = await explainRestoreFailure("boom", { load, expectedVersion: 1 });
    expect(unknown.message).not.toBe("");
  });
});
