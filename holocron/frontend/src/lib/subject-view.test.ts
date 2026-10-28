import { describe, expect, it } from "vitest";
import type { Subject } from "@/types/holocron";
import { localError } from "./errors";
import {
  breadcrumbForPath,
  canShowPanes,
  HOME_CRUMB,
  subjectBreadcrumb,
  subjectTitle,
  subjectViewOf,
} from "./subject-view";
import type { SubjectsState } from "./subjects";

function subject(code: string, name: string): Subject {
  return {
    subject_code: code,
    display_name: name,
    exam_format: "mixed",
    version: 1,
    created_at: "2026-10-28T00:00:00Z",
    updated_at: "2026-10-28T00:00:00Z",
  };
}

const ERROR = localError("Chưa tải được danh sách môn.");
const READY: SubjectsState = { kind: "ready", subjects: [subject("VXL01", "Vi xử lý"), subject("MAU101", " ")] };

describe("subjectViewOf", () => {
  it("mã sai hoặc thiếu ⇒ invalid_code, bất kể danh sách", () => {
    expect(subjectViewOf(null, READY)).toEqual({ kind: "invalid_code" });
    expect(subjectViewOf(null, { kind: "loading" })).toEqual({ kind: "invalid_code" });
  });

  it("đang tải ⇒ loading; lỗi danh sách ⇒ list_unavailable kèm lỗi", () => {
    expect(subjectViewOf("VXL01", { kind: "loading" })).toEqual({ kind: "loading", code: "VXL01" });
    expect(subjectViewOf("VXL01", { kind: "error", error: ERROR })).toEqual({
      kind: "list_unavailable",
      code: "VXL01",
      error: ERROR,
    });
  });

  it("danh sách đã tải: có môn ⇒ ready, không có ⇒ not_found", () => {
    const view = subjectViewOf("VXL01", READY);
    expect(view.kind === "ready" ? view.subject.subject_code : null).toBe("VXL01");
    expect(subjectViewOf("KHAC01", READY)).toEqual({ kind: "not_found", code: "KHAC01" });
    expect(subjectViewOf("VXL01", { kind: "ready", subjects: [] })).toEqual({ kind: "not_found", code: "VXL01" });
  });

  it("chỉ hiện các phần khi biết mã và môn không chắc chắn vắng mặt", () => {
    expect(canShowPanes(subjectViewOf(null, READY))).toBe(false);
    expect(canShowPanes(subjectViewOf("KHAC01", READY))).toBe(false);
    expect(canShowPanes(subjectViewOf("VXL01", READY))).toBe(true);
    expect(canShowPanes(subjectViewOf("VXL01", { kind: "loading" }))).toBe(true);
    expect(canShowPanes(subjectViewOf("VXL01", { kind: "error", error: ERROR }))).toBe(true);
  });

  it("tiêu đề: tên môn; tên trống hoặc chưa tải ⇒ mã môn; không có mã ⇒ null", () => {
    expect(subjectTitle(subjectViewOf("VXL01", READY))).toBe("Vi xử lý");
    expect(subjectTitle(subjectViewOf("MAU101", READY))).toBe("MAU101");
    expect(subjectTitle(subjectViewOf("VXL01", { kind: "loading" }))).toBe("VXL01");
    expect(subjectTitle(subjectViewOf("KHAC01", READY))).toBe("KHAC01");
    expect(subjectTitle(subjectViewOf(null, READY))).toBeNull();
  });
});

describe("subjectBreadcrumb", () => {
  it("Môn học (liên kết về trang chủ) › tên môn (không phải liên kết)", () => {
    expect(HOME_CRUMB).toEqual({ label: "Môn học", href: "/" });
    expect(subjectBreadcrumb(subjectViewOf("VXL01", READY))).toEqual([
      { label: "Môn học", href: "/" },
      { label: "Vi xử lý", href: null },
    ]);
  });

  it("chưa rõ môn vẫn có mục cuối để đánh dấu trang đang mở", () => {
    expect(subjectBreadcrumb({ kind: "invalid_code" })).toEqual([
      { label: "Môn học", href: "/" },
      { label: "Chưa rõ môn", href: null },
    ]);
  });
});

describe("breadcrumbForPath", () => {
  it("trang chủ không có breadcrumb, kể cả có / cuối", () => {
    expect(breadcrumbForPath("/", "VXL01", READY)).toEqual([]);
    expect(breadcrumbForPath("", "VXL01", READY)).toEqual([]);
  });

  it("trang môn: Môn học › tên môn đang mở; chưa tải danh sách ⇒ mã môn", () => {
    expect(breadcrumbForPath("/subject/", "VXL01", READY)).toEqual([HOME_CRUMB, { label: "Vi xử lý", href: null }]);
    expect(breadcrumbForPath("/subject", "VXL01", { kind: "loading" })).toEqual([
      HOME_CRUMB,
      { label: "VXL01", href: null },
    ]);
    expect(breadcrumbForPath("/subject", null, READY)).toEqual([HOME_CRUMB, { label: "Chưa rõ môn", href: null }]);
  });

  it("trang khác: Môn học › tiêu đề trang; mục cuối không phải liên kết", () => {
    expect(breadcrumbForPath("/settings", "VXL01", READY)).toEqual([HOME_CRUMB, { label: "Cài đặt", href: null }]);
    expect(breadcrumbForPath("/ingestion/", null, READY)).toEqual([HOME_CRUMB, { label: "Nhập đề thi", href: null }]);
  });
});
