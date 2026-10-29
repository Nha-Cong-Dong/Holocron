/**
 * Smoke của các route sau khi gỡ UI cũ (design-handoff/README.md màn 9–11): tập route của `src/app`, trang chuyển hướng
 * của route cũ, không còn màn cũ hay liên kết tới route cũ trong component. Đọc file nguồn bằng trình phân tích của
 * TypeScript (không render): chỉ xét import và chuỗi ký tự; comment không bị tính.
 */

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const APP = join(SRC, "app");
const COMPONENTS = join(SRC, "components");

function walk(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return walk(path);
    return /\.(ts|tsx)$/.test(entry.name) && !/\.test\.ts$/.test(entry.name) && !entry.name.endsWith(".d.ts")
      ? [path]
      : [];
  });
}

/** Đường dẫn route của một file `page.tsx`/`page.dev.tsx` dưới `src/app`. */
function routeOf(file: string): string {
  const dir = relative(APP, file).split(sep).slice(0, -1).join("/");
  return dir === "" ? "/" : `/${dir}`;
}

function parse(file: string, source: string = readFileSync(file, "utf-8")): ts.SourceFile {
  const kind = file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  return ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, kind);
}

/** Đường dẫn của mọi `import … from "…"` và `export … from "…"`. */
function importsOf(file: string): string[] {
  const specifiers: string[] = [];
  for (const statement of parse(file).statements) {
    if (
      (ts.isImportDeclaration(statement) || ts.isExportDeclaration(statement)) &&
      statement.moduleSpecifier !== undefined &&
      ts.isStringLiteral(statement.moduleSpecifier)
    ) {
      specifiers.push(statement.moduleSpecifier.text);
    }
  }
  return specifiers;
}

/** Chuỗi ký tự và phần chữ của template string (không gồm đường dẫn import). */
function literalsOf(file: string, source?: string): string[] {
  const texts: string[] = [];
  function visit(node: ts.Node): void {
    const parent = node.parent;
    const isSpecifier =
      parent !== undefined &&
      (ts.isImportDeclaration(parent) || ts.isExportDeclaration(parent)) &&
      parent.moduleSpecifier === node;
    if ((ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) && !isSpecifier) texts.push(node.text);
    else if (ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) texts.push(node.text);
    ts.forEachChild(node, visit);
  }
  visit(parse(file, source));
  return texts;
}

/** Tên các hàm được gọi trực tiếp (`foo(…)`). */
function callsOf(file: string, source?: string): string[] {
  const names: string[] = [];
  function visit(node: ts.Node): void {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression)) names.push(node.expression.text);
    ts.forEachChild(node, visit);
  }
  visit(parse(file, source));
  return names;
}

const REDIRECT_ROUTES = ["/pairs", "/practice", "/vault"] as const;

describe("tập route của src/app", () => {
  it("chỉ có màn mới, trang chuyển hướng, màn thử nghiệm và trang dev", () => {
    const routes = walk(APP)
      .filter((file) => /[\\/]page(\.dev)?\.tsx$/.test(file))
      .map(routeOf)
      .sort();
    expect(routes).toEqual(
      ["/", "/subject", "/settings", ...REDIRECT_ROUTES, "/ingestion", "/twins", "/dev/ui"].sort(),
    );
  });

  it("trang của route cũ chỉ là chuyển hướng (chỉ import LegacyRedirect và Suspense)", () => {
    for (const route of REDIRECT_ROUTES) {
      const file = join(APP, route.slice(1), "page.tsx");
      expect(importsOf(file).sort(), route).toEqual(["@/components/shell/LegacyRedirect", "react"]);
      const source = readFileSync(file, "utf-8");
      expect(source, route).toContain(`<LegacyRedirect path="${route}" />`);
      expect(source, route).toContain("<Suspense");
    }
  });

  it("khung chờ chuyển hướng mang dấu hiệu để smoke của bản đóng gói nhận ra", () => {
    const source = readFileSync(join(COMPONENTS, "shell", "LegacyRedirect.tsx"), "utf-8");
    expect(source).toContain("data-legacy-redirect");
    expect(source).toContain("router.replace(");
  });
});

describe("không còn màn cũ", () => {
  const files = walk(SRC);

  it("thư mục components/pairs đã bị xoá, không file nào import từ đó", () => {
    expect(existsSync(join(COMPONENTS, "pairs"))).toBe(false);
    const hits = files.filter((file) => importsOf(file).some((specifier) => specifier.includes("components/pairs/")));
    expect(hits.map((file) => relative(SRC, file))).toEqual([]);
  });

  it("thanh trạng thái dưới cùng đã bị gỡ, không file nào import nó", () => {
    expect(existsSync(join(COMPONENTS, "shell", "StatusBar.tsx"))).toBe(false);
    const hits = files.filter((file) => importsOf(file).some((specifier) => /(^|\/)StatusBar$/.test(specifier)));
    expect(hits.map((file) => relative(SRC, file))).toEqual([]);
  });

  it("màn Vault cũ đã bị xoá", () => {
    expect(existsSync(join(COMPONENTS, "vault", "VaultView.tsx"))).toBe(false);
    expect(existsSync(join(COMPONENTS, "vault", "ChunkBrowser.tsx"))).toBe(false);
  });
});

describe("component không trỏ tới route cũ", () => {
  const componentFiles = walk(COMPONENTS);

  it("không có liên kết tới /pairs, /practice, /vault", () => {
    const hits: string[] = [];
    for (const file of componentFiles) {
      for (const text of literalsOf(file)) {
        if (/^\/(pairs|practice|vault)(\/|\?|$)/.test(text)) hits.push(`${relative(SRC, file)}: ${text}`);
      }
    }
    expect(hits).toEqual([]);
  });

  it("không còn lời gọi vaultHref, vaultSubjectHref hay pairsHref", () => {
    const hits = componentFiles.filter((file) =>
      callsOf(file).some((name) => name === "vaultHref" || name === "vaultSubjectHref" || name === "pairsHref"),
    );
    expect(hits.map((file) => relative(SRC, file))).toEqual([]);
  });

  it("màn thử nghiệm /ingestion, /twins chỉ được trỏ tới từ Cài đặt", () => {
    const own = [join(COMPONENTS, "ingestion"), join(COMPONENTS, "twins"), join(APP, "ingestion"), join(APP, "twins")];
    const hits: string[] = [];
    for (const file of [...walk(APP), ...componentFiles]) {
      if (own.some((dir) => file.startsWith(dir + sep))) continue;
      if (file === join(COMPONENTS, "settings", "SettingsView.tsx")) continue;
      for (const text of literalsOf(file)) {
        if (/^\/(ingestion|twins)(\/|\?|$)/.test(text)) hits.push(`${relative(SRC, file)}: ${text}`);
      }
    }
    expect(hits).toEqual([]);
    const settings = literalsOf(join(COMPONENTS, "settings", "SettingsView.tsx"));
    expect(settings).toContain("/ingestion");
    expect(settings).toContain("/twins");
  });

  it("bộ quét bắt được liên kết và lời gọi trong nguồn mẫu, bỏ qua comment và import", () => {
    const sample = [
      'import { vaultHref } from "@/lib/vault";',
      '// <Link href="/pairs"> trong comment',
      'export const A = () => <Link href="/vault?subject=VXL01">Mở</Link>;',
      "const b = vaultHref({});",
    ].join("\n");
    expect(literalsOf("sample.tsx", sample)).toEqual(["/vault?subject=VXL01"]);
    expect(callsOf("sample.tsx", sample)).toEqual(["vaultHref"]);
  });
});
