import { describe, expect, it } from "vitest";
import type { Subject } from "@/types/holocron";
import { localError } from "./errors";
import {
  breadcrumbForLocation,
  breadcrumbForPath,
  canShowPanes,
  canShowSky,
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
    expect(breadcrumbForPath("/ingestion/", null, READY)).toEqual([
      HOME_CRUMB,
      { label: "Nhập đề thi cũ", href: null },
    ]);
  });
});

describe("breadcrumb của route cũ", () => {
  it("dùng chữ của luồng mới, không lộ Vault hay Cặp khái niệm", () => {
    const labels = ["/vault", "/pairs/", "/practice", "/ingestion", "/twins/"].map(
      (path) => breadcrumbForPath(path, "VXL01", READY)[1]?.label,
    );
    expect(labels).toEqual(["Tài liệu", "Khái niệm", "Ôn tập", "Nhập đề thi cũ", "Câu hỏi song sinh"]);
    for (const path of ["/vault", "/pairs", "/practice", "/ingestion", "/twins"]) {
      const crumbs = breadcrumbForPath(path, null, READY);
      expect(crumbs[0]).toEqual(HOME_CRUMB);
      expect(crumbs[1]?.label).not.toMatch(/vault|cặp|chunk|backend/i);
      expect(crumbs[1]?.href).toBeNull();
    }
  });

  it("đường dẫn lạ dùng tiêu đề mặc định", () => {
    expect(breadcrumbForPath("/khong-co", null, READY)).toEqual([HOME_CRUMB, { label: "Holocron", href: null }]);
  });
});

describe("breadcrumbForLocation (môn lấy từ địa chỉ)", () => {
  const query = (text: string) => new URLSearchParams(text);

  it("mã sai hoặc chưa có mã ⇒ Chưa rõ môn, không hiện môn mở trước đó", () => {
    expect(breadcrumbForLocation("/subject", query("code=sai"), READY)[1]?.label).toBe("Chưa rõ môn");
    expect(breadcrumbForLocation("/subject", query(""), READY)[1]?.label).toBe("Chưa rõ môn");
  });

  it("danh sách môn chưa tải ⇒ hiện mã môn; tải xong ⇒ tên môn", () => {
    expect(breadcrumbForLocation("/subject", query("code=VXL01"), { kind: "loading" })[1]?.label).toBe("VXL01");
    expect(breadcrumbForLocation("/subject", query("code=VXL01"), READY)[1]?.label).toBe("Vi xử lý");
  });

  it("đổi môn: breadcrumb theo ngay mã trên địa chỉ mới", () => {
    expect(breadcrumbForLocation("/subject/", query("code=VXL01&tab=concepts"), READY)[1]?.label).toBe("Vi xử lý");
    expect(breadcrumbForLocation("/subject/", query("code=MAU101&tab=concepts"), READY)[1]?.label).toBe("MAU101");
  });

  it("trang khác bỏ qua ?code=", () => {
    expect(breadcrumbForLocation("/settings", query("code=VXL01"), READY)).toEqual([
      HOME_CRUMB,
      { label: "Cài đặt", href: null },
    ]);
  });
});

describe("canShowSky", () => {
  it("chỉ hiện bầu trời khi môn có trong danh sách đã tải", () => {
    expect(canShowSky(subjectViewOf("VXL01", READY))).toBe(true);
  });

  it("mã sai, môn không có, danh sách đang tải hoặc lỗi: không hiện bầu trời", () => {
    expect(canShowSky(subjectViewOf(null, READY))).toBe(false);
    expect(canShowSky(subjectViewOf("KHAC01", READY))).toBe(false);
    expect(canShowSky(subjectViewOf("VXL01", { kind: "ready", subjects: [] }))).toBe(false);
    expect(canShowSky(subjectViewOf("VXL01", { kind: "loading" }))).toBe(false);
    expect(canShowSky(subjectViewOf("VXL01", { kind: "error", error: ERROR }))).toBe(false);
  });
});

describe("breadcrumb khi đang làm bài: tên môn không thêm mục lịch sử", () => {
  it("tên môn trỏ về tab Ôn tập không kèm session và thay mục lịch sử hiện tại", () => {
    const items = breadcrumbForLocation("/subject", new URLSearchParams("code=VXL01&tab=practice&session=quiz"), READY);
    const crumb = items[1];
    expect(crumb).toEqual({ label: "Vi xử lý", href: "/subject?code=VXL01&tab=practice", replace: true });
    expect(crumb?.href).not.toContain("session");
  });

  it("ngoài lúc làm bài: không có mục nào thay lịch sử", () => {
    const items = [
      ...breadcrumbForLocation("/subject", new URLSearchParams("code=VXL01&tab=practice"), READY),
      ...breadcrumbForPath("/settings", null, READY),
      HOME_CRUMB,
    ];
    expect(items.every((item) => item.replace === undefined)).toBe(true);
  });
});
