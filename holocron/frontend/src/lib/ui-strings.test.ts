/**
 * Quét chuỗi giao diện trong mã nguồn: chữ người dùng nhìn thấy không được dùng thuật ngữ nội bộ của backend
 * ("sandbox", "slot", "template"). Test đọc file nguồn (không render), bỏ comment, rồi chỉ xét chuỗi ký tự và chữ
 * trong JSX; tên biến, field hay khoá enum (ví dụ `slot_index`, `template_id`) không bị tính.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("..", import.meta.url));

/** Thư mục chứa chuỗi giao diện. `mocks/` là dữ liệu mẫu đóng vai backend, `types/` không có chuỗi hiển thị. */
const UI_DIRS = ["app", "components", "lib"];

const FORBIDDEN = /\b(sandbox|slot|slots|template|templates)\b/i;

function sourceFiles(dir: string): string[] {
  const entries = readdirSync(dir, { withFileTypes: true });
  return entries.flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    if (!/\.(ts|tsx)$/.test(entry.name) || /\.test\.ts$/.test(entry.name) || entry.name.endsWith(".d.ts")) return [];
    return [path];
  });
}

/** Bỏ comment khối, comment dòng (không đụng `://` trong URL) và dòng import/export-from. */
function stripNonText(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:\\])\/\/.*$/gm, "$1")
    .replace(/^\s*(import|export)\b[^;]*\bfrom\s+["'][^"']+["'];?/gm, " ");
}

/** Chuỗi ký tự (nháy kép, nháy đơn, backtick) và chữ trong JSX. */
function visibleTexts(source: string): string[] {
  const code = stripNonText(source);
  const texts: string[] = [];
  const literal = /"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`/g;
  for (const match of code.matchAll(literal)) texts.push(match[0]);
  const jsxText = /[>}]([^<>{}]+)[<{]/g;
  for (const match of code.matchAll(jsxText)) {
    const text = match[1]?.trim() ?? "";
    if (text !== "") texts.push(text);
  }
  return texts;
}

describe("chuỗi giao diện không dùng thuật ngữ nội bộ", () => {
  const files = UI_DIRS.flatMap((dir) => sourceFiles(join(SRC, dir)));

  it("tìm thấy file nguồn để quét", () => {
    expect(files.length).toBeGreaterThan(20);
  });

  it("không có sandbox, slot hay template trong chữ hiển thị", () => {
    const hits: string[] = [];
    for (const file of files) {
      for (const text of visibleTexts(readFileSync(file, "utf-8"))) {
        if (FORBIDDEN.test(text)) hits.push(`${relative(SRC, file)}: ${text}`);
      }
    }
    expect(hits).toEqual([]);
  });

  it("bộ quét bắt được chuỗi vi phạm và bỏ qua tên định danh, comment", () => {
    const sample = [
      "// slot trong comment",
      "/* template trong comment */",
      "const a = twin.slot_index + data.template_id;",
      'const label = "Đang chạy sandbox";',
      "return <p>Slot {index}</p>;",
    ].join("\n");
    const found = visibleTexts(sample).filter((text) => FORBIDDEN.test(text));
    expect(found).toEqual(['"Đang chạy sandbox"', "Slot"]);
  });
});
