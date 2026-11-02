import { describe, expect, it } from "vitest";

import {
  crumbTitleForPath,
  filterCommands,
  foldForSearch,
  NAV_ITEMS,
  navItemForPath,
  shellHeadingForPath,
  titleForPath,
} from "./navigation";

describe("navItemForPath", () => {
  it("khớp đường dẫn, chấp nhận / cuối của bản export tĩnh", () => {
    expect(navItemForPath("/")?.label).toBe("Bắt đầu");
    expect(navItemForPath("/ingestion")?.href).toBe("/ingestion");
    expect(navItemForPath("/ingestion/")?.href).toBe("/ingestion");
    expect(navItemForPath("/khong-co")).toBeNull();
    expect(titleForPath("/khong-co")).toBe("Holocron");
  });

  it("không có route động: mọi href là đường dẫn tĩnh", () => {
    for (const item of NAV_ITEMS) {
      expect(item.href).not.toMatch(/\[|\?/);
      expect(item.short.length).toBeLessThanOrEqual(3);
    }
  });
});

describe("mục Cặp khái niệm", () => {
  it("có trong điều hướng, đường dẫn tĩnh, viết tắt không trùng mục khác", () => {
    const item = navItemForPath("/pairs/");
    expect(item?.label).toBe("Cặp khái niệm");
    expect(titleForPath("/pairs")).toBe("Cặp khái niệm");
    const shorts = NAV_ITEMS.map((entry) => entry.short);
    expect(new Set(shorts).size).toBe(shorts.length);
  });
});

describe("foldForSearch và filterCommands", () => {
  const commands = [
    { id: "a", label: "Nhập đề thi", hint: "Tải ảnh đề" },
    { id: "b", label: "Bảng phím tắt", hint: "Phím" },
    { id: "c", label: "Kiểm tra lại kết nối", hint: "Backend" },
  ];

  it("bỏ dấu và đ, không phân biệt hoa thường, NFC hay NFD", () => {
    expect(foldForSearch("Đề THI  ")).toBe("de thi");
    expect(foldForSearch("Nhập".normalize("NFD"))).toBe(foldForSearch("Nhập".normalize("NFC")));
  });

  it("lọc theo mọi từ của truy vấn", () => {
    expect(filterCommands(commands, "").map((c) => c.id)).toEqual(["a", "b", "c"]);
    expect(filterCommands(commands, "de thi").map((c) => c.id)).toEqual(["a"]);
    expect(filterCommands(commands, "PHÍM").map((c) => c.id)).toEqual(["b"]);
    expect(filterCommands(commands, "ket noi backend").map((c) => c.id)).toEqual(["c"]);
    expect(filterCommands(commands, "khong co")).toEqual([]);
  });
});

describe("tiêu đề trang theo luồng mới", () => {
  it("trang môn và Cài đặt có tiêu đề riêng, chấp nhận / cuối", () => {
    expect(titleForPath("/subject")).toBe("Trang môn");
    expect(titleForPath("/subject/")).toBe("Trang môn");
    expect(titleForPath("/settings")).toBe("Cài đặt");
    expect(titleForPath("/settings/")).toBe("Cài đặt");
  });

  it("không thêm hai trang này vào điều hướng cũ; route cũ giữ tiêu đề", () => {
    expect(navItemForPath("/subject")).toBeNull();
    expect(navItemForPath("/settings")).toBeNull();
    expect(titleForPath("/ingestion")).toBe("Nhập đề thi");
    expect(titleForPath("/")).toBe("Bắt đầu");
  });
});

describe("crumbTitleForPath", () => {
  it("route cũ dùng chữ của luồng mới; NAV_ITEMS và titleForPath giữ nguyên", () => {
    expect(crumbTitleForPath("/vault/")).toBe("Tài liệu");
    expect(crumbTitleForPath("/pairs")).toBe("Khái niệm");
    expect(crumbTitleForPath("/practice")).toBe("Ôn tập");
    expect(crumbTitleForPath("/ingestion")).toBe("Nhập đề thi cũ");
    expect(crumbTitleForPath("/twins")).toBe("Câu hỏi song sinh");
    expect(titleForPath("/vault")).toBe("Vault");
    expect(navItemForPath("/pairs")?.label).toBe("Cặp khái niệm");
  });

  it("trang mới và đường dẫn lạ dùng titleForPath", () => {
    expect(crumbTitleForPath("/settings")).toBe("Cài đặt");
    expect(crumbTitleForPath("/khong-co")).toBe("Holocron");
  });
});

describe("shellHeadingForPath", () => {
  it("trang tự có h1 (trang môn, Cài đặt): khung không render, kể cả có / cuối", () => {
    expect(shellHeadingForPath("/subject")).toBeNull();
    expect(shellHeadingForPath("/subject/")).toBeNull();
    expect(shellHeadingForPath("/settings")).toBeNull();
  });

  it("trang chủ tự có h1 (Bầu trời của bạn hoặc lời chào): khung không render", () => {
    expect(shellHeadingForPath("/")).toBeNull();
  });

  it("route cũ: h1 hiện rõ theo chữ của luồng mới", () => {
    expect(shellHeadingForPath("/vault/")).toEqual({ text: "Tài liệu", visuallyHidden: false });
    expect(shellHeadingForPath("/pairs")).toEqual({ text: "Khái niệm", visuallyHidden: false });
    expect(shellHeadingForPath("/practice")).toEqual({ text: "Ôn tập", visuallyHidden: false });
    expect(shellHeadingForPath("/ingestion")).toEqual({ text: "Nhập đề thi cũ", visuallyHidden: false });
    expect(shellHeadingForPath("/twins")).toEqual({ text: "Câu hỏi song sinh", visuallyHidden: false });
    expect(shellHeadingForPath("/khong-co")).toEqual({ text: "Holocron", visuallyHidden: false });
  });
});
