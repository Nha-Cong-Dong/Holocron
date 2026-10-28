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

function kebab(name: string): string {
  return name.replaceAll("_", "-");
}

/** Endpoint đang có trong api.ts (tham số động ghi `:`). Thêm endpoint mới phải sửa danh sách này có chủ đích. */
const EXISTING_ENDPOINTS: readonly string[] = [
  "/v1/runtime",
  "/v1/subjects",
  "/v1/ingestion/upload",
  "/v1/ingestion/items/:",
  "/v1/ingestion/items/:/table",
  "/v1/ingestion/items/:/non-cloneable",
  "/v1/ingestion/items/:/approve",
  "/v1/blueprints",
  "/v1/blueprints/:/twins",
  "/v1/twins/:/essay-grade",
  "/v1/vault/chunks",
  "/v1/vault/chunks/:/labels",
  "/v1/vault/search",
  "/v1/vault/concept-pairs",
  "/v1/vault/concept-pairs/:/confirm",
  "/v1/vault/concept-pairs/:/reject",
  "/v1/documents",
  "/v1/documents/:",
  "/v1/documents/:/pages/:",
  "/v1/documents/:/pages/:/lines",
  "/v1/documents/:/pages/:/confirm",
  "/v1/documents/:/confirm",
];

/** Từ không được xuất hiện trong đoạn nào của đường dẫn endpoint ("proposal" đã có sẵn ở endpoint ghép khái niệm thì không). */
const FORBIDDEN_SEGMENT_WORDS: readonly string[] = ["proposal", "eta", "line-review", "review-queue", "auto-"];

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

  it("api.ts không chứa tên khả năng và mọi endpoint nằm trong danh sách đã có", () => {
    const source = readFileSync(join(SRC_DIR, "lib", "api.ts"), "utf-8");
    for (const name of PENDING) {
      expect(source).not.toContain(name);
      expect(source).not.toContain(kebab(name));
    }
    const endpoints = new Set(endpointPaths(source));
    expect(endpoints.size).toBeGreaterThan(0);
    for (const endpoint of endpoints) {
      expect(EXISTING_ENDPOINTS, endpoint).toContain(endpoint);
      const segments = endpoint.split("/");
      for (const word of FORBIDDEN_SEGMENT_WORDS) {
        expect(
          segments.some((s) => s === word || s.includes(word)),
          `${endpoint} ~ ${word}`,
        ).toBe(false);
      }
    }
  });

  it("components chỉ nhắc ba tên dưới dạng capabilityOf(...) hoặc CapabilityNotice name=...", () => {
    for (const file of listFiles(join(SRC_DIR, "components"), ".tsx")) {
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
  });
});
