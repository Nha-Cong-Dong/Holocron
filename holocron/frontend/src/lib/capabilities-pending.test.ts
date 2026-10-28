import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type { CapabilityName } from "@/types/holocron";

import { type CapabilityEntry, capabilityLabel, groupCapabilities, UNRECOGNIZED_CAPABILITY_LABEL_VI } from "./runtime";

/** Ba khả năng đề xuất (SPECIFICATION.md §5.12), luôn `not_implemented` ở bản đang chạy. */
const PENDING: readonly CapabilityName[] = ["concept_auto_proposal", "line_review_queue", "page_progress_eta"];

function pending(name: CapabilityName, detail: string | null): CapabilityEntry {
  return { name, available: false, reason: "not_implemented", detail_vi: detail };
}

const SRC_DIR = join(__dirname, "..");

function listFiles(dir: string, extension: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...listFiles(full, extension));
    else if (full.endsWith(extension)) out.push(full);
  }
  return out;
}

/** Nguồn giao diện cần quét: app, components (.ts/.tsx) và lib (.ts, không đệ quy); bỏ test, runtime.ts, types, mocks. */
function scannedFiles(): string[] {
  const runtimeFile = join(SRC_DIR, "lib", "runtime.ts");
  const typesDir = join(SRC_DIR, "types");
  const mocksDir = join(SRC_DIR, "mocks");
  const isSource = (file: string): boolean => /\.tsx?$/.test(file) && !/\.test\.tsx?$/.test(file);
  const lib = readdirSync(join(SRC_DIR, "lib"))
    .map((entry) => join(SRC_DIR, "lib", entry))
    .filter((file) => statSync(file).isFile());
  return [...listFiles(join(SRC_DIR, "app"), ""), ...listFiles(join(SRC_DIR, "components"), ""), ...lib].filter(
    (file) => isSource(file) && file !== runtimeFile && !file.startsWith(typesDir) && !file.startsWith(mocksDir),
  );
}

function kebab(name: string): string {
  return name.replaceAll("_", "-");
}

/**
 * Đoạn đường dẫn endpoint gợi tới ba khả năng chưa có. Dùng danh sách chặn (không allowlist) để PR sau thêm endpoint
 * khác không phải sửa test này; "eta" chỉ khớp nguyên đoạn hoặc hậu tố `-eta` để không bắt nhầm "metadata", "beta".
 */
function forbiddenSegment(segment: string): boolean {
  return (
    ["proposal", "line-review", "review-queue", "auto-", "progress"].some((word) => segment.includes(word)) ||
    segment === "eta" ||
    segment.endsWith("-eta")
  );
}

/** Mọi đường dẫn `/v1/...` trong mã, bỏ query, `${...}` thành `:`. */
function endpointPaths(source: string): string[] {
  const out: string[] = [];
  for (const match of source.matchAll(/["'`](\/v1\/[^"'`]*)["'`]/g)) {
    const raw = match[1] ?? "";
    const path = raw.replace(/\$\{[^}]*(?:\([^)]*\))?[^}]*\}/g, ":").split("?")[0] ?? "";
    out.push(path);
  }
  return out;
}

/** Hai dạng nhắc tên được phép: đối số của `capabilityOf(` và thuộc tính `name=` của `CapabilityNotice`. */
function allowedForms(form: string): RegExp[] {
  return [
    new RegExp(String.raw`capabilityOf\(\s*[^,()]+,\s*["']${form}["']`, "g"),
    new RegExp(String.raw`<CapabilityNotice\b[^>]*?\bname=["']${form}["']`, "g"),
  ];
}

/** Các lần nhắc tên khả năng không nằm trong dạng cho phép, xét trên toàn nguồn (nhiều dòng) chứ không từng dòng. */
function disallowedMentions(source: string): string[] {
  let rest = source;
  for (const name of PENDING) {
    for (const form of [name, kebab(name)]) {
      for (const pattern of allowedForms(form)) rest = rest.replace(pattern, "");
    }
  }
  const found: string[] = [];
  for (const name of PENDING) {
    for (const form of [name, kebab(name)]) if (rest.includes(form)) found.push(form);
  }
  return found;
}

describe("ba khả năng đề xuất ở nhóm chưa có trong bản đang chạy", () => {
  const variants: Record<string, CapabilityEntry[]> = {
    "không cấu hình": PENDING.map((name) => pending(name, null)),
    "đã cấu hình đủ LLM và VLM": PENDING.map((name) =>
      pending(name, "Đã cấu hình LLM và VLM nhưng chưa có trong bản này."),
    ),
  };

  for (const [title, entries] of Object.entries(variants)) {
    it(`${title}: cả ba vào nhóm khoá not_implemented, không nhóm khác`, () => {
      const groups = groupCapabilities(entries);
      expect(groups.not_implemented.map((c) => c.name)).toEqual([...PENDING]);
      expect(groups.offline).toEqual([]);
      expect(groups.online).toEqual([]);
      expect(groups.install).toEqual([]);
      expect(groups.configure).toEqual([]);
      expect(groups.unrecognized).toEqual([]);
    });
  }

  it("mỗi tên có nhãn tiếng Việt đã biết", () => {
    for (const name of PENDING) {
      expect(capabilityLabel(pending(name, null))).not.toBe(UNRECOGNIZED_CAPABILITY_LABEL_VI);
    }
  });

  it("api.ts không chứa tên khả năng và không có endpoint gợi tới chúng", () => {
    const source = readFileSync(join(SRC_DIR, "lib", "api.ts"), "utf-8");
    for (const name of PENDING) {
      expect(source).not.toContain(name);
      expect(source).not.toContain(kebab(name));
    }
    const endpoints = new Set(endpointPaths(source));
    expect(endpoints.size).toBeGreaterThan(0);
    for (const endpoint of endpoints) {
      expect(endpoint.split("/").some(forbiddenSegment), endpoint).toBe(false);
    }
  });

  // Phần render nhóm khả năng trên StartScreen do PR E2E phủ (vitest ở đây không có DOM); test này chỉ quét nguồn.
  it("mã giao diện chỉ nhắc ba tên dưới dạng capabilityOf(...) hoặc CapabilityNotice name=...", () => {
    const files = scannedFiles();
    expect(files.length).toBeGreaterThan(0);
    for (const file of files) {
      expect(disallowedMentions(readFileSync(file, "utf-8")), file).toEqual([]);
    }
  });

  it("tự kiểm: tên nằm cạnh onClick ở dòng khác của cùng phần tử bị bắt", () => {
    const bad = [
      "<button",
      '  type="button"',
      "  onClick={() => api.run()}",
      '  data-x="line_review_queue"',
      ">",
      "  Duyệt",
      "</button>",
    ].join("\n");
    expect(disallowedMentions(bad)).toEqual(["line_review_queue"]);
    const kebabHref = '<a\n  href="/x"\n  id="page-progress-eta"\n/>';
    expect(disallowedMentions(kebabHref)).toEqual(["page-progress-eta"]);
  });

  it("tự kiểm: dạng cho phép không bị bắt", () => {
    const ok = [
      'const v = capabilityOf(runtime, "concept_auto_proposal");',
      '<CapabilityNotice runtime={r} name="page_progress_eta" />',
      "<CapabilityNotice\n  runtime={r}\n  name='line_review_queue'\n/>",
    ].join("\n");
    expect(disallowedMentions(ok)).toEqual([]);
  });

  it("tự kiểm: endpoint lạ bị bắt", () => {
    expect(endpointPaths('request("/v1/vault/concept-proposals")')).toEqual(["/v1/vault/concept-proposals"]);
    expect(endpointPaths("request(`/v1/documents/$" + "{segment(id)}/eta?x=1`)")).toEqual(["/v1/documents/:/eta"]);
    for (const path of ["/v1/vault/concept-proposals", "/v1/documents/:/eta", "/v1/documents/:/line-review"]) {
      expect(path.split("/").some(forbiddenSegment), path).toBe(true);
    }
    for (const path of ["/v1/documents/:/metadata", "/v1/vault/concept-pairs", "/v1/runtime"]) {
      expect(path.split("/").some(forbiddenSegment), path).toBe(false);
    }
  });
});
