import { describe, expect, it } from "vitest";

import { filterCommands, foldForSearch, NAV_ITEMS, navItemForPath, titleForPath } from "./navigation";

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
