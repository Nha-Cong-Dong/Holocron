/**
 * Quy tắc của `src/app/motion.css` (brief UI PR 3): (a) mọi class thành phần có mặt trong cả hai khối giảm chuyển động
 * và hai khối giống nhau; (b) `!important` chỉ nằm trong hai khối đó; (c) `transition` và `@keyframes` chỉ animate
 * opacity, transform, translate, scale, rotate. Test đọc file CSS (không render) bằng một bộ tách khối nhỏ.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const MOTION_CSS = fileURLToPath(new URL("../app/motion.css", import.meta.url));

const SECTION_START = "THÀNH PHẦN — BẮT ĐẦU";
const SECTION_END = "THÀNH PHẦN — KẾT THÚC";
const REDUCE_MEDIA = "@media (prefers-reduced-motion: reduce)";
const REDUCE_MEDIA_ROOT = ':root:not([data-motion="full"])';
const REDUCE_SWITCH_ROOT = ':root[data-motion="reduce"]';
const ANIMATABLE = new Set(["opacity", "transform", "translate", "scale", "rotate"]);

interface Block {
  prelude: string;
  body: string;
}

interface Declaration {
  property: string;
  value: string;
}

interface Sheet {
  /** Rule thường (ngoài hai khối giảm chuyển động), kể cả rule lồng trong `@media` khác. */
  rules: Block[];
  keyframes: Block[];
  /** Rule trong `@media (prefers-reduced-motion: reduce)`. */
  reduceMedia: Block[];
  /** Rule mà mọi selector bắt đầu bằng `:root[data-motion="reduce"]`. */
  reduceSwitch: Block[];
}

function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, " ");
}

/** Tách các khối cấp cao nhất `prelude { body }` (body giữ nguyên khối lồng bên trong). */
function topLevelBlocks(css: string): Block[] {
  const blocks: Block[] = [];
  let cursor = 0;
  while (cursor < css.length) {
    const open = css.indexOf("{", cursor);
    if (open === -1) break;
    let depth = 1;
    let index = open + 1;
    while (index < css.length && depth > 0) {
      const char = css[index];
      if (char === "{") depth += 1;
      else if (char === "}") depth -= 1;
      index += 1;
    }
    blocks.push({ prelude: css.slice(cursor, open).trim(), body: css.slice(open + 1, index - 1) });
    cursor = index;
  }
  return blocks;
}

function declarations(body: string): Declaration[] {
  return body
    .split(";")
    .map((part) => part.trim())
    .filter((part) => part.includes(":"))
    .map((part) => {
      const colon = part.indexOf(":");
      return { property: part.slice(0, colon).trim().toLowerCase(), value: part.slice(colon + 1).trim() };
    });
}

function selectors(prelude: string): string[] {
  return prelude.split(",").map((selector) => selector.trim());
}

function parseSheet(css: string): Sheet {
  const sheet: Sheet = { rules: [], keyframes: [], reduceMedia: [], reduceSwitch: [] };
  for (const block of topLevelBlocks(stripComments(css))) {
    if (block.prelude.startsWith(REDUCE_MEDIA)) {
      sheet.reduceMedia.push(...topLevelBlocks(block.body));
    } else if (block.prelude.startsWith("@keyframes")) {
      sheet.keyframes.push(block);
    } else if (block.prelude.startsWith("@media")) {
      sheet.rules.push(...topLevelBlocks(block.body));
    } else if (selectors(block.prelude).every((selector) => selector.startsWith(REDUCE_SWITCH_ROOT))) {
      sheet.reduceSwitch.push(block);
    } else {
      sheet.rules.push(block);
    }
  }
  return sheet;
}

/** Tách giá trị theo dấu phẩy ở cấp ngoài cùng (không cắt trong `var(...)`, `calc(...)`). */
function splitTopLevelCommas(value: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let current = "";
  for (const char of value) {
    if (char === "(") depth += 1;
    if (char === ")") depth -= 1;
    if (char === "," && depth === 0) {
      parts.push(current.trim());
      current = "";
    } else {
      current += char;
    }
  }
  parts.push(current.trim());
  return parts.filter((part) => part !== "");
}

/** Thuộc tính bị animate ngoài danh sách cho phép, dạng "<prelude>: <thuộc tính>". */
function forbiddenAnimatedProperties(sheet: Sheet): string[] {
  const problems: string[] = [];
  for (const rule of [...sheet.rules, ...sheet.reduceMedia, ...sheet.reduceSwitch]) {
    for (const { property, value } of declarations(rule.body)) {
      if (property !== "transition" && property !== "transition-property") continue;
      for (const item of splitTopLevelCommas(value.replace(/!important/g, ""))) {
        const animated = item.split(/\s+/)[0] ?? "";
        if (animated === "none") continue;
        if (!ANIMATABLE.has(animated)) problems.push(`${rule.prelude}: ${animated}`);
      }
    }
  }
  for (const keyframes of sheet.keyframes) {
    for (const frame of topLevelBlocks(keyframes.body)) {
      for (const { property } of declarations(frame.body)) {
        if (!ANIMATABLE.has(property)) problems.push(`${keyframes.prelude}: ${property}`);
      }
    }
  }
  return problems;
}

function importantOutsideReduce(sheet: Sheet): string[] {
  return [...sheet.rules, ...sheet.keyframes]
    .filter((block) => block.body.includes("!important"))
    .map((block) => block.prelude);
}

/** Class `m-*` xuất hiện trong selector của khối thành phần (giữa hai dấu mốc trong comment). */
function componentClasses(css: string): string[] {
  const start = css.indexOf(SECTION_START);
  const end = css.indexOf(SECTION_END);
  if (start === -1 || end === -1 || end < start) return [];
  const section = parseSheet(css.slice(start + SECTION_START.length, end));
  const classes = new Set<string>();
  for (const rule of section.rules) {
    for (const match of rule.prelude.matchAll(/\.(m-[a-z0-9-]+)/g)) {
      if (match[1] !== undefined) classes.add(match[1]);
    }
  }
  return [...classes].sort();
}

function mentions(blocks: readonly Block[], className: string): boolean {
  const pattern = new RegExp(`\\.${className}(?![\\w-])`);
  return blocks.some((block) => pattern.test(block.prelude));
}

/** Hai khối giảm chuyển động viết theo cùng một dạng, chỉ khác tiền tố `:root…`. */
function normalizeReduce(blocks: readonly Block[], root: string): string[] {
  return blocks.map((block) => {
    const prelude = selectors(block.prelude)
      .map((selector) => selector.replace(root, ":root"))
      .join(", ");
    const body = declarations(block.body)
      .map(({ property, value }) => `${property}: ${value}`)
      .join("; ");
    return `${prelude} { ${body} }`;
  });
}

const css = readFileSync(MOTION_CSS, "utf-8");
const sheet = parseSheet(css);
const classes = componentClasses(css);

describe("motion.css: giảm chuyển động", () => {
  it("tìm thấy khối thành phần và hai khối giảm chuyển động", () => {
    expect(classes.length).toBeGreaterThan(20);
    expect(sheet.reduceMedia.length).toBeGreaterThan(0);
    expect(sheet.reduceSwitch.length).toBeGreaterThan(0);
  });

  it("(a) mọi class thành phần có trong khối theo Windows", () => {
    expect(classes.filter((name) => !mentions(sheet.reduceMedia, name))).toEqual([]);
  });

  it("(a) mọi class thành phần có trong khối công tắc của Cài đặt", () => {
    expect(classes.filter((name) => !mentions(sheet.reduceSwitch, name))).toEqual([]);
  });

  it("(a) hai khối giảm chuyển động giống nhau", () => {
    expect(normalizeReduce(sheet.reduceMedia, REDUCE_MEDIA_ROOT)).toEqual(
      normalizeReduce(sheet.reduceSwitch, REDUCE_SWITCH_ROOT),
    );
  });

  it("(b) không có !important ngoài hai khối giảm chuyển động", () => {
    expect(importantOutsideReduce(sheet)).toEqual([]);
  });

  it("(c) transition và @keyframes chỉ animate opacity, transform, translate, scale, rotate", () => {
    expect(forbiddenAnimatedProperties(sheet)).toEqual([]);
  });
});

describe("motion.css: bộ kiểm bắt được vi phạm", () => {
  const sample = [
    "/* THÀNH PHẦN — BẮT ĐẦU */",
    ".m-a { transition: transform 1s, background 1s; }",
    ".m-b:hover { color: red !important; }",
    "@keyframes m-k { from { width: 0; opacity: 0; } }",
    ".m-c { transition: 200ms; }",
    "/* THÀNH PHẦN — KẾT THÚC */",
    `${REDUCE_MEDIA} { ${REDUCE_MEDIA_ROOT} .m-a { animation: none !important; } }`,
    `${REDUCE_SWITCH_ROOT} .m-a, ${REDUCE_SWITCH_ROOT} .m-b { animation: none !important; }`,
  ].join("\n");
  const parsed = parseSheet(sample);

  it("báo class thiếu trong từng khối và hai khối lệch nhau", () => {
    const names = componentClasses(sample);
    expect(names).toEqual(["m-a", "m-b", "m-c"]);
    expect(names.filter((name) => !mentions(parsed.reduceMedia, name))).toEqual(["m-b", "m-c"]);
    expect(names.filter((name) => !mentions(parsed.reduceSwitch, name))).toEqual(["m-c"]);
    expect(normalizeReduce(parsed.reduceMedia, REDUCE_MEDIA_ROOT)).not.toEqual(
      normalizeReduce(parsed.reduceSwitch, REDUCE_SWITCH_ROOT),
    );
  });

  it("báo !important ngoài khối giảm chuyển động", () => {
    expect(importantOutsideReduce(parsed)).toEqual([".m-b:hover"]);
  });

  it("báo thuộc tính animate ngoài danh sách, kể cả transition không ghi thuộc tính", () => {
    expect(forbiddenAnimatedProperties(parsed)).toEqual([".m-a: background", ".m-c: 200ms", "@keyframes m-k: width"]);
  });

  it("selector chỉ một phần bắt đầu bằng tiền tố công tắc là rule thường", () => {
    const mixed = parseSheet(`${REDUCE_SWITCH_ROOT} .m-a, .m-b { color: red !important; }`);
    expect(mixed.reduceSwitch).toEqual([]);
    expect(importantOutsideReduce(mixed)).toHaveLength(1);
  });
});
