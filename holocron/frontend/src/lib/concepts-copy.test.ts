/**
 * Chữ hiển thị của tab Khái niệm và Ôn tập (design-handoff/README.md mục 7): không lộ từ kỹ thuật backend, chunk, OCR,
 * LLM, VLM, vault, pair, blueprint, và không dùng "cặp khái niệm". Bộ quét đọc file nguồn bằng trình phân tích của
 * TypeScript (không render): chỉ xét chuỗi ký tự, phần chữ của template string và chữ trong JSX; tên định danh,
 * comment và đường dẫn import không bị tính. Kèm kiểm các thông điệp lỗi mà hai màn này nhận từ `errors.ts`.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { classifyHttpError, type ErrorContext } from "./errors";

const SRC = fileURLToPath(new URL("..", import.meta.url));

const FORBIDDEN = /\b(backend|chunk|chunks|OCR|LLM|VLM|vault|pair|pairs|blueprint|blueprints)\b|cặp khái niệm/i;

/** File chứa chữ hiển thị của màn 7–8. */
function scannedFiles(): string[] {
  const conceptsDir = join(SRC, "components", "concepts");
  const concepts = readdirSync(conceptsDir)
    .filter((name) => name.endsWith(".tsx") || name.endsWith(".ts"))
    .map((name) => join(conceptsDir, name));
  return [
    ...concepts,
    join(SRC, "components", "practice", "PracticeSetup.tsx"),
    join(SRC, "components", "practice", "PracticeView.tsx"),
    join(SRC, "lib", "concept-board.ts"),
    join(SRC, "lib", "passage.ts"),
    join(SRC, "lib", "practice-setup.ts"),
  ];
}

function isModuleSpecifier(node: ts.Node): boolean {
  const parent = node.parent;
  return (
    parent !== undefined &&
    (ts.isImportDeclaration(parent) || ts.isExportDeclaration(parent)) &&
    parent.moduleSpecifier === node
  );
}

/** Chuỗi ký tự, phần chữ của template string và chữ JSX trong một file nguồn. */
function visibleTexts(source: string, fileName: string): string[] {
  const kind = fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const file = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, kind);
  const texts: string[] = [];
  function visit(node: ts.Node): void {
    if ((ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) && !isModuleSpecifier(node)) {
      texts.push(node.text);
    } else if (ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) {
      texts.push(node.text);
    } else if (ts.isJsxText(node)) {
      const text = node.text.trim();
      if (text !== "") texts.push(text);
    }
    ts.forEachChild(node, visit);
  }
  visit(file);
  return texts;
}

describe("chữ của tab Khái niệm và Ôn tập không lộ từ kỹ thuật", () => {
  const files = scannedFiles();

  it("tìm thấy đủ file để quét", () => {
    expect(files.length).toBeGreaterThanOrEqual(11);
  });

  it("không có từ cấm trong chữ hiển thị", () => {
    const hits: string[] = [];
    for (const file of files) {
      for (const text of visibleTexts(readFileSync(file, "utf-8"), file)) {
        if (FORBIDDEN.test(text)) hits.push(`${relative(SRC, file)}: ${text}`);
      }
    }
    expect(hits).toEqual([]);
  });

  it("bộ quét bắt được chuỗi vi phạm và bỏ qua định danh, comment, đường dẫn import", () => {
    const sample = [
      'import { api } from "@/lib/vault";',
      "// chunk trong comment",
      "/* blueprint trong comment */",
      "const view = pair.chunk_id + blueprint.blueprintId;",
      'const label = "Đang đọc chunk";',
      // Template string có phần thay thế: ghép từ hai mảnh để chính file test không chứa `${…}` trong chuỗi.
      ["const line = `Có $", "{count} cặp khái niệm`;"].join(""),
      "export const A = () => <p>Mở Vault {name}</p>;",
    ].join("\n");
    const found = visibleTexts(sample, "sample.tsx").filter((text) => FORBIDDEN.test(text));
    expect(found).toEqual(["Đang đọc chunk", " cặp khái niệm", "Mở Vault"]);
  });
});

describe("thông điệp lỗi mà màn 7–8 hiển thị", () => {
  const contexts: ErrorContext[] = [
    "pairs_list",
    "pair_create",
    "pair_confirm",
    "pair_reject",
    "vault_browse",
    "document_list",
    "blueprint_create",
    "twin_generation",
  ];

  it("câu riêng theo luồng không dùng từ cấm (400, 404, 409, 413, 415, 422)", () => {
    const hits: string[] = [];
    for (const context of contexts) {
      for (const status of [400, 404, 409, 413, 415, 422]) {
        for (const versioned of [false, true]) {
          const error = classifyHttpError(status, { detail: "x" }, { context, versioned });
          if (FORBIDDEN.test(error.message)) hits.push(`${context} ${status}: ${error.message}`);
        }
      }
      const quote = classifyHttpError(422, { detail: [{ loc: ["body", "definition_quote"] }] }, { context });
      if (FORBIDDEN.test(quote.message)) hits.push(`${context} 422 quote: ${quote.message}`);
    }
    expect(hits).toEqual([]);
  });
});
