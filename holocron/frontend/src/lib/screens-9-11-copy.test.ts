/**
 * Chữ hiển thị của màn Làm bài, Kết quả, Cài đặt và trang chuyển hướng (design-handoff/README.md mục 7): không lộ từ
 * kỹ thuật backend, chunk, OCR, LLM, VLM, vault, pair, blueprint, và không dùng "cặp khái niệm". Thuật ngữ kỹ thuật chỉ
 * được nằm trong `components/settings/*Technical.tsx` (render trong "Chi tiết kỹ thuật"). Bộ quét đọc file nguồn bằng
 * trình phân tích của TypeScript (không render): chỉ xét chuỗi ký tự, phần chữ của template string và chữ trong JSX;
 * tên định danh, comment, đường dẫn import, đường dẫn route (ví dụ "/vault") và khoá mã (chuỗi ASCII chữ thường liền
 * một từ, ví dụ "ocr" trong `capabilityOf(runtime, "ocr")`) không bị tính. Kèm kiểm các thông điệp
 * lỗi mà các màn này nhận từ `errors.ts`.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { CAPABILITY_ERROR_BODY } from "@/mocks/fixtures/errors";
import type { CapabilityReason } from "@/types/holocron";

import { classifyHttpError, classifyNetworkError, type ErrorContext, invalidResponseError } from "./errors";

const SRC = fileURLToPath(new URL("..", import.meta.url));

const FORBIDDEN = /\b(backend|chunk|chunks|OCR|LLM|VLM|vault|pair|pairs|blueprint|blueprints)\b|cặp khái niệm/i;

/** Đường dẫn route trong mã (ví dụ "/pairs", "/subject"): không phải chữ hiển thị. */
const ROUTE_PATH = /^\/[a-z/]*$/;

/** Khoá mã so sánh hay truyền cho hàm (ví dụ "ocr", "chunks", "quiz"): không phải chữ hiển thị. */
const CODE_KEY = /^[a-z][a-z0-9_-]*$/;

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(ts|tsx)$/.test(entry.name) && !/\.test\.ts$/.test(entry.name) ? [path] : [];
  });
}

/** File chứa chữ hiển thị của màn 9–11 và trang chuyển hướng; miễn `*Technical.tsx`. */
function scannedFiles(): string[] {
  const settings = join(SRC, "components", "settings");
  return [
    ...sourceFiles(join(SRC, "components", "practice")),
    join(SRC, "components", "shell", "LegacyRedirect.tsx"),
    join(settings, "SettingsView.tsx"),
    join(settings, "MachineDiagnostics.tsx"),
    join(settings, "QuestionStyleSection.tsx"),
    join(SRC, "components", "concepts", "PassageText.tsx"),
    join(SRC, "lib", "quiz.ts"),
    join(SRC, "lib", "legacy-routes.ts"),
  ].filter((file) => !file.endsWith("Technical.tsx"));
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
  return texts.filter((text) => !ROUTE_PATH.test(text) && !CODE_KEY.test(text));
}

describe("chữ của màn Làm bài, Kết quả, Cài đặt không lộ từ kỹ thuật", () => {
  const files = scannedFiles();

  it("tìm thấy đủ file để quét", () => {
    expect(files.length).toBeGreaterThanOrEqual(18);
    expect(files.some((file) => file.endsWith("Technical.tsx"))).toBe(false);
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

  it("không còn câu nhắc thanh trạng thái hay màn Bắt đầu", () => {
    const hits: string[] = [];
    for (const file of files) {
      for (const text of visibleTexts(readFileSync(file, "utf-8"), file)) {
        if (/thanh trạng thái|màn Bắt đầu/i.test(text)) hits.push(`${relative(SRC, file)}: ${text}`);
      }
    }
    expect(hits).toEqual([]);
  });

  it("bộ quét bắt được chuỗi vi phạm, bỏ qua định danh, comment, import và đường dẫn route", () => {
    const sample = [
      'import { api } from "@/lib/vault";',
      "// chunk trong comment",
      'const route = "/vault";',
      'const ocr = capabilityOf(runtime, "ocr");',
      'const note = "Đọc OCR";',
      "const view = pair.chunk_id + blueprint.blueprintId;",
      'const label = "Đang đọc chunk";',
      "export const A = () => <p>Mở Vault {name}</p>;",
    ].join("\n");
    const found = visibleTexts(sample, "sample.tsx").filter((text) => FORBIDDEN.test(text));
    expect(found).toEqual(["Đọc OCR", "Đang đọc chunk", "Mở Vault"]);
  });
});

const BANNED_IN_ERRORS = /backend|màn Bắt đầu/i;

describe("thông điệp lỗi mà màn 9–11 hiển thị", () => {
  const contexts: ErrorContext[] = ["essay_grade", "vault_source", "runtime"];

  it("câu riêng theo luồng không nhắc backend hay màn Bắt đầu", () => {
    const hits: string[] = [];
    for (const context of contexts) {
      for (const status of [400, 403, 404, 409, 413, 415, 422, 500, 502, 503]) {
        const error = classifyHttpError(status, { detail: "x" }, { context });
        if (BANNED_IN_ERRORS.test(error.message)) hits.push(`${context} ${status}: ${error.message}`);
      }
    }
    expect(hits).toEqual([]);
  });

  it("lỗi mạng giữ địa chỉ nhưng không nói backend; phản hồi lạ cũng vậy", () => {
    expect(classifyNetworkError("").message).not.toMatch(BANNED_IN_ERRORS);
    expect(classifyNetworkError("http://127.0.0.1:8000").message).toContain("http://127.0.0.1:8000");
    expect(classifyNetworkError("http://127.0.0.1:8000").message).not.toMatch(BANNED_IN_ERRORS);
    expect(invalidResponseError().message).not.toMatch(BANNED_IN_ERRORS);
  });

  it("mọi lý do khả năng (và lý do lạ) không nhắc backend hay màn Bắt đầu", () => {
    const reasons: (CapabilityReason | "quota_exhausted")[] = [
      "llm_not_configured",
      "vlm_not_configured",
      "ocr_engine_missing",
      "ocr_language_missing",
      "secondary_ocr_disabled",
      "not_implemented",
      "quota_exhausted",
    ];
    for (const reason of reasons) {
      const body = { detail: { ...CAPABILITY_ERROR_BODY.detail, reason } };
      const error = classifyHttpError(409, body);
      expect(error.kind, reason).toBe("capability");
      expect(error.message, reason).not.toMatch(BANNED_IN_ERRORS);
    }
  });
});
