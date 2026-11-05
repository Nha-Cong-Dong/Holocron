import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { PageView } from "@/lib/documents-data";
import { parsePageExtraction } from "@/lib/documents-data";
import { INITIAL_PAGES } from "@/mocks/fixtures/documents";

import { VlmMismatchPanel } from "./VlmMismatchPanel";

function mockPage(): PageView {
  const found = INITIAL_PAGES.find((page) => page.page_number === 2);
  const view = found === undefined ? null : parsePageExtraction(structuredClone(found));
  if (view === null) throw new Error("fixture trang 2 không hợp lệ");
  return view;
}

function html(page: PageView): string {
  return renderToStaticMarkup(
    createElement(VlmMismatchPanel, { page, busy: false, onPatch: () => Promise.resolve(true) }),
  );
}

describe("VlmMismatchPanel", () => {
  it("mỗi chỗ lệch có một nút 'Giữ giá trị này', mỗi khối một nút 'Bỏ khối AI đọc', không có 'giữ tất cả'", () => {
    const markup = html(mockPage());
    expect(markup.match(/Giữ giá trị này/g)).toHaveLength(2);
    expect(markup.match(/Bỏ khối AI đọc/g)).toHaveLength(1);
    expect(markup.toLowerCase()).not.toContain("giữ tất cả");
    expect(markup).toContain("<code>7</code>");
    expect(markup).toContain("<code>30</code>");
  });
  it("không có chỗ lệch ⇒ không vẽ gì", () => {
    expect(html({ ...mockPage(), vlmMismatches: [] })).toBe("");
  });
});
