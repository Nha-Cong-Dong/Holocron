import { describe, expect, it } from "vitest";

import { legacyRedirectTarget, vaultLocationTarget } from "./legacy-routes";

const query = (text: string) => new URLSearchParams(text);

describe("legacyRedirectTarget: /pairs", () => {
  it("đủ tham số ⇒ tab Khái niệm của môn, giữ chương, trạng thái, đoạn (mã hoá)", () => {
    expect(
      legacyRedirectTarget("/pairs", query("subject=VXL01&chapter=ch2&status=proposed&chunk=c%201%2F2"), null),
    ).toBe("/subject?code=VXL01&tab=concepts&chapter=ch2&status=proposed&chunk=c%201%2F2");
  });

  it("tham số sai mẫu bị bỏ", () => {
    expect(legacyRedirectTarget("/pairs", query("subject=VXL01&chapter=chuong2&status=xx"), null)).toBe(
      "/subject?code=VXL01&tab=concepts",
    );
  });

  it("thiếu môn: môn gần nhất (bỏ bộ lọc), không có ⇒ trang chủ", () => {
    expect(legacyRedirectTarget("/pairs", query("chapter=ch2&chunk=c1"), "MAU101")).toBe(
      "/subject?code=MAU101&tab=concepts",
    );
    expect(legacyRedirectTarget("/pairs", query("subject=sai"), "MAU101")).toBe("/subject?code=MAU101&tab=concepts");
    expect(legacyRedirectTarget("/pairs", query(""), null)).toBe("/");
  });

  it("có / cuối vẫn nhận ra", () => {
    expect(legacyRedirectTarget("/pairs/", query("subject=VXL01"), null)).toBe("/subject?code=VXL01&tab=concepts");
  });
});

describe("legacyRedirectTarget: /practice", () => {
  it("môn gần nhất ⇒ tab Ôn tập; không có ⇒ trang chủ", () => {
    expect(legacyRedirectTarget("/practice", query(""), "VXL01")).toBe("/subject?code=VXL01&tab=practice");
    expect(legacyRedirectTarget("/practice/", query(""), "VXL01")).toBe("/subject?code=VXL01&tab=practice");
    expect(legacyRedirectTarget("/practice", query(""), null)).toBe("/");
  });

  it("môn gần nhất sai mẫu ⇒ trang chủ", () => {
    expect(legacyRedirectTarget("/practice", query(""), "vxl01")).toBe("/");
    expect(legacyRedirectTarget("/practice", query(""), "VXL01&tab=x")).toBe("/");
  });
});

describe("legacyRedirectTarget: /vault", () => {
  it("môn + tài liệu + trang ⇒ tab Tài liệu xem lại đúng trang", () => {
    expect(legacyRedirectTarget("/vault", query("subject=VXL01&doc=doc_0123456789abcdef&page=3"), null)).toBe(
      "/subject?code=VXL01&tab=documents&doc=doc_0123456789abcdef&page=3",
    );
  });

  it("chỉ có môn ⇒ tab Tài liệu; tab=chunks ⇒ tab Khái niệm", () => {
    expect(legacyRedirectTarget("/vault/", query("subject=VXL01"), null)).toBe("/subject?code=VXL01&tab=documents");
    expect(legacyRedirectTarget("/vault", query("subject=VXL01&tab=chunks"), null)).toBe(
      "/subject?code=VXL01&tab=concepts",
    );
  });

  it("tài liệu không kèm môn ⇒ trang chủ, kể cả có môn gần nhất", () => {
    expect(legacyRedirectTarget("/vault", query("doc=doc_0123456789abcdef&page=2"), "VXL01")).toBe("/");
  });

  it("không tham số: môn gần nhất (tab Tài liệu hoặc Khái niệm), không có ⇒ trang chủ", () => {
    expect(legacyRedirectTarget("/vault", query(""), "VXL01")).toBe("/subject?code=VXL01&tab=documents");
    expect(legacyRedirectTarget("/vault", query("tab=chunks"), "VXL01")).toBe("/subject?code=VXL01&tab=concepts");
    expect(legacyRedirectTarget("/vault", query(""), null)).toBe("/");
  });

  it("trang sai mẫu bị bỏ", () => {
    expect(legacyRedirectTarget("/vault", query("subject=VXL01&doc=doc_0123456789abcdef&page=0"), null)).toBe(
      "/subject?code=VXL01&tab=documents&doc=doc_0123456789abcdef",
    );
  });
});

describe("legacyRedirectTarget: đường dẫn khác", () => {
  it("không phải route cũ ⇒ trang chủ", () => {
    expect(legacyRedirectTarget("/khong-co", query("subject=VXL01"), "VXL01")).toBe("/");
    expect(legacyRedirectTarget("", query(""), "VXL01")).toBe("/");
  });
});

describe("vaultLocationTarget (mặc định của danh sách tài liệu)", () => {
  it("có môn ⇒ trang môn; không có môn ⇒ trang chủ", () => {
    expect(vaultLocationTarget({ subject: "VXL01", doc: "doc_0123456789abcdef", page: 2 })).toBe(
      "/subject?code=VXL01&tab=documents&doc=doc_0123456789abcdef&page=2",
    );
    expect(vaultLocationTarget({ subject: "VXL01" })).toBe("/subject?code=VXL01&tab=documents");
    expect(vaultLocationTarget({})).toBe("/");
    expect(vaultLocationTarget({ subject: "sai" })).toBe("/");
  });
});
