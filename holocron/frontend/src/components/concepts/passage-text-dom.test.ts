/**
 * Hồi quy T2.3 (vitest chạy môi trường node, không có DOM): `PassageText` phải giữ `raw_text` trong MỘT text node, không
 * chèn `<mark>` hay thẻ con vào chữ, vì offset `Selection` của đường tự bôi là offset trong `raw_text`. Tô sáng đi qua CSS
 * Custom Highlight API (`CSS.highlights`), có kiểm tính năng. Phần offset thuần được kiểm ở `lib/quote-highlight.test.ts`.
 */

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const SOURCE = readFileSync(new URL("./PassageText.tsx", import.meta.url), "utf8").replace(/\r\n/g, "\n");
const CSS_SOURCE = readFileSync(new URL("./ConceptsTab.module.css", import.meta.url), "utf8");

describe("PassageText giữ raw_text trong một text node", () => {
  it("phần tử <pre> chỉ chứa {rawText}, không thẻ con", () => {
    expect(SOURCE).toMatch(/<pre ref=\{textRef\} className=\{styles\.passageText\}>\s*\{rawText\}\s*<\/pre>/);
  });

  it("không chèn <mark> hay dangerouslySetInnerHTML vào văn bản", () => {
    // Chú thích có nhắc `<mark>` trong dấu huyền; chỉ cấm thẻ thật trong JSX.
    expect(SOURCE).not.toMatch(/(^|[^`])<mark[\s>/]/);
    expect(SOURCE).not.toContain("dangerouslySetInnerHTML");
  });

  it("tô sáng qua CSS.highlights, có kiểm tính năng trước khi dùng", () => {
    expect(SOURCE).toContain("CSS.highlights.set(");
    expect(SOURCE).toContain("canHighlight(window)");
    expect(CSS_SOURCE).toContain(".passageText::highlight(holocron-quote)");
  });
});
