import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { PageView } from "@/lib/documents-data";
import { parsePageExtraction } from "@/lib/documents-data";
import { VLM_MISMATCH_PAGE } from "@/mocks/fixtures/documents";

import { VlmMismatchPanel } from "./VlmMismatchPanel";

function mockPage(): PageView {
  const view = parsePageExtraction(structuredClone(VLM_MISMATCH_PAGE));
  if (view === null) throw new Error("fixture trang lệch VLM không hợp lệ");
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
    expect(markup.match(/>Giữ giá trị này</g)).toHaveLength(2);
    expect(markup.match(/Bỏ khối AI đọc/g)).toHaveLength(1);
    expect(markup.toLowerCase()).not.toContain("giữ tất cả");
    expect(markup).toContain("<code>7</code>");
    expect(markup).toContain("<code>30</code>");
  });
  it("nút 'Giữ giá trị này' có tên phân biệt theo ô và khối", () => {
    const markup = html(mockPage());
    expect(markup).toContain("Giữ giá trị này: Hàng 2, cột 3, khối mock-blk-vlm-1");
    expect(markup).toContain("Giữ giá trị này: Ô bị AI bỏ sót, khối mock-blk-vlm-1");
  });
  it("không có chỗ lệch và không có lý do TABLE_TOKEN_MISMATCH ⇒ không vẽ gì", () => {
    expect(html({ ...mockPage(), vlmMismatches: [], reviewReasons: [] })).toBe("");
  });
  it("trang bị chặn vì TABLE_TOKEN_MISMATCH mà không đọc được chi tiết ⇒ báo thay vì im lặng", () => {
    expect(html({ ...mockPage(), vlmMismatches: [] })).toContain("không đọc được chi tiết");
  });
});
