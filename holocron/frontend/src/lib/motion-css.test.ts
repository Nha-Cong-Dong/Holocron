/**
 * Quy tắc của `src/app/motion.css` (brief UI PR 3): (a) mọi class thành phần có mặt trong cả hai khối giảm chuyển động
 * và hai khối giống nhau, mọi animation của thành phần bị tắt (`animation: none !important`) hoặc phần tử bị ẩn
 * (`display: none`) trong cả hai khối; (b) `!important` chỉ nằm trong hai khối đó; (c) `transition` và `@keyframes` chỉ animate
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

/** Rule của khối thành phần: từ sau comment chứa dấu mốc bắt đầu tới comment chứa dấu mốc kết thúc. */
function componentSection(css: string): Sheet | null {
  const start = css.indexOf(SECTION_START);
  const end = css.indexOf(SECTION_END);
  const commentEnd = css.indexOf("*/", start);
  if (start === -1 || end === -1 || commentEnd === -1 || end < commentEnd) return null;
  const endComment = css.lastIndexOf("/*", end);
  return parseSheet(css.slice(commentEnd + 2, endComment === -1 ? end : endComment));
}

/** Class `m-*` xuất hiện trong selector của khối thành phần (giữa hai dấu mốc trong comment). */
function componentClasses(css: string): string[] {
  const section = componentSection(css);
  if (section === null) return [];
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

function selectorMentions(selector: string, className: string): boolean {
  return new RegExp(`\\.${className}(?![\\w-])`).test(selector);
}

function pseudoElementOf(selector: string): string {
  return /::[a-z-]+$/.exec(selector)?.[0] ?? "";
}

/** Một selector của rule có animation trong khối thành phần: class cuối (phần tử được animate) và pseudo-element. */
interface AnimatedTarget {
  selector: string;
  classes: string[];
  key: string;
  pseudo: string;
}

function animatedTargets(css: string): AnimatedTarget[] {
  const section = componentSection(css);
  if (section === null) return [];
  const targets: AnimatedTarget[] = [];
  for (const rule of section.rules) {
    const animates = declarations(rule.body).some(
      ({ property, value }) => (property === "animation" || property === "animation-name") && !value.startsWith("none"),
    );
    if (!animates) continue;
    for (const selector of selectors(rule.prelude)) {
      const classes = [...selector.matchAll(/\.(m-[a-z0-9-]+)/g)].flatMap((match) =>
        match[1] === undefined ? [] : [match[1]],
      );
      const key = classes[classes.length - 1];
      if (key !== undefined) targets.push({ selector, classes, key, pseudo: pseudoElementOf(selector) });
    }
  }
  return targets;
}

/**
 * Selector có animation mà khối giảm chuyển động không tắt: cần một rule `animation: none !important` nhắm đúng class
 * cuối (và cùng pseudo-element), hoặc một rule `display: none` (không pseudo-element) nhắm một class của selector đó.
 */
function unsilencedAnimations(targets: readonly AnimatedTarget[], reduce: readonly Block[]): string[] {
  const rules = reduce.flatMap((block) =>
    selectors(block.prelude).map((selector) => ({ selector, decls: declarations(block.body) })),
  );
  function silenced(target: AnimatedTarget): boolean {
    return rules.some(({ selector, decls }) => {
      const hides = decls.some(({ property, value }) => property === "display" && value.startsWith("none"));
      if (hides && pseudoElementOf(selector) === "") {
        if (target.classes.some((name) => selectorMentions(selector, name))) return true;
      }
      const stops = decls.some(({ property, value }) => property === "animation" && /^none\s*!important$/.test(value));
      return stops && selectorMentions(selector, target.key) && pseudoElementOf(selector) === target.pseudo;
    });
  }
  return targets.filter((target) => !silenced(target)).map((target) => target.selector);
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
const animated = animatedTargets(css);

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

  it("(a) mọi animation của thành phần bị tắt hoặc ẩn trong khối theo Windows", () => {
    expect(animated.length).toBeGreaterThan(10);
    expect(unsilencedAnimations(animated, sheet.reduceMedia)).toEqual([]);
  });

  it("(a) mọi animation của thành phần bị tắt hoặc ẩn trong khối công tắc của Cài đặt", () => {
    expect(unsilencedAnimations(animated, sheet.reduceSwitch)).toEqual([]);
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

  it("báo animation chưa bị tắt: chỉ bỏ transition, sai pseudo-element, hoặc thiếu !important", () => {
    const section = [
      "/* THÀNH PHẦN — BẮT ĐẦU */",
      ".m-a { animation: m-x 1s; }",
      ".m-b::before { animation: m-x 1s; }",
      ".m-c { animation: m-x 1s; }",
      ".m-d i { animation: m-x 1s; }",
      ".m-e[data-x] .m-f { animation: m-x 1s; }",
      "/* THÀNH PHẦN — KẾT THÚC */",
    ].join("\n");
    const reduce = parseSheet(
      [
        `${REDUCE_SWITCH_ROOT} .m-a { transition: none !important; }`,
        `${REDUCE_SWITCH_ROOT} .m-b { animation: none !important; }`,
        `${REDUCE_SWITCH_ROOT} .m-c { animation: none; }`,
        `${REDUCE_SWITCH_ROOT} .m-d { display: none; }`,
        `${REDUCE_SWITCH_ROOT} .m-e .m-f { animation: none !important; }`,
      ].join("\n"),
    ).reduceSwitch;
    expect(unsilencedAnimations(animatedTargets(section), reduce)).toEqual([".m-a", ".m-b::before", ".m-c"]);
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
