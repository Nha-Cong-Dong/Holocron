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

  it("api.ts không chứa tên hay đường dẫn nào của ba khả năng", () => {
    const source = readFileSync(join(SRC_DIR, "lib", "api.ts"), "utf-8");
    for (const name of PENDING) {
      expect(source).not.toContain(name);
      expect(source).not.toContain(kebab(name));
    }
  });

  it("không dòng nào trong components vừa nhắc tên vừa gắn nút, liên kết, form hoặc lời gọi api", () => {
    const triggers = /onClick|href|onSubmit|api\./;
    for (const file of listFiles(join(SRC_DIR, "components"), ".tsx")) {
      for (const line of readFileSync(file, "utf-8").split(/\r?\n/)) {
        const mentions = PENDING.some((name) => line.includes(name) || line.includes(kebab(name)));
        if (mentions) expect(line, file).not.toMatch(triggers);
      }
    }
  });
});
