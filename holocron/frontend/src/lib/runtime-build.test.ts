import { describe, expect, it } from "vitest";

import { buildLabel, logDirOf, normalizeRuntimeInfo } from "@/lib/runtime";

const BASE = { demo_mode: false, llm_configured: false, vlm_configured: false };

describe("thông tin bản chạy", () => {
  it("nhãn cắt commit còn 7 ký tự", () => {
    const view = normalizeRuntimeInfo({ ...BASE, app_version: "1.2.3", commit: "0123456789abcdef", log_dir: null });
    expect(buildLabel(view)).toBe("Holocron 1.2.3 (0123456)");
    expect(logDirOf(view)).toBeNull();
  });

  it("log_dir chuỗi được giữ nguyên; rỗng hoặc thiếu thì null", () => {
    expect(logDirOf(normalizeRuntimeInfo({ ...BASE, log_dir: "C:logs" }))).toBe("C:logs");
    expect(logDirOf(normalizeRuntimeInfo({ ...BASE, log_dir: "" }))).toBeNull();
    expect(logDirOf(normalizeRuntimeInfo(BASE))).toBeNull();
  });

  it("backend cũ chưa báo version thì không có nhãn; commit unknown vẫn hiển thị", () => {
    expect(buildLabel(normalizeRuntimeInfo(BASE))).toBeNull();
    expect(buildLabel(null)).toBeNull();
    expect(buildLabel(normalizeRuntimeInfo({ ...BASE, app_version: "dev", commit: "unknown" }))).toBe(
      "Holocron dev (unknown)",
    );
  });
});
