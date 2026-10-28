import { describe, expect, it } from "vitest";

import { isSubjectCode, parseSubjectLocation, subjectConceptsHref, subjectHref } from "./subject-route";

function params(query: string): URLSearchParams {
  return new URLSearchParams(query);
}

describe("subjectHref", () => {
  it("dựng /subject?code=&tab= và nối tham số của phần theo thứ tự", () => {
    expect(subjectHref("MAU101", "documents")).toBe("/subject?code=MAU101&tab=documents");
    expect(subjectHref("MAU101", "concepts", { chapter: "ch2", status: "pending" })).toBe(
      "/subject?code=MAU101&tab=concepts&chapter=ch2&status=pending",
    );
  });

  it("bỏ tham số rỗng hoặc null; không cho ghi đè code/tab", () => {
    expect(subjectHref("MAU101", "practice", { chapter: null, status: "", chunk: undefined })).toBe(
      "/subject?code=MAU101&tab=practice",
    );
    expect(subjectHref("MAU101", "practice", { code: "KHAC01", tab: "documents" })).toBe(
      "/subject?code=MAU101&tab=practice",
    );
  });

  it("mã hoá mọi giá trị bằng encodeURIComponent", () => {
    expect(subjectHref("A B&C", "documents", { chunk: "x/y?z=1#2" })).toBe(
      "/subject?code=A%20B%26C&tab=documents&chunk=x%2Fy%3Fz%3D1%232",
    );
  });

  it("dựng rồi đọc lại cho đúng môn, phần và tham số", () => {
    const href = subjectConceptsHref("MAU101", { subject: "KHAC01", chapter: "ch1", status: null, chunk: "c 1/2" });
    expect(href).toBe("/subject?code=MAU101&tab=concepts&chapter=ch1&chunk=c%201%2F2");
    const query = new URLSearchParams(href.slice(href.indexOf("?") + 1));
    expect(parseSubjectLocation(query)).toEqual({ code: "MAU101", tab: "concepts" });
    expect(query.get("chunk")).toBe("c 1/2");
    expect(query.get("subject")).toBeNull();
  });
});

describe("parseSubjectLocation", () => {
  it("đọc mã đúng mẫu (bỏ khoảng trắng hai đầu) và tab", () => {
    expect(parseSubjectLocation(params("code=MAU101&tab=practice"))).toEqual({ code: "MAU101", tab: "practice" });
    expect(parseSubjectLocation(params("code=%20VXL01%20"))).toEqual({ code: "VXL01", tab: "documents" });
  });

  it("mã thiếu hoặc sai mẫu ⇒ null; tab lạ ⇒ Tài liệu", () => {
    expect(parseSubjectLocation(params(""))).toEqual({ code: null, tab: "documents" });
    expect(parseSubjectLocation(params("code=mau101"))).toEqual({ code: null, tab: "documents" });
    expect(parseSubjectLocation(params("code=MAU101%3Cscript%3E&tab=x"))).toEqual({ code: null, tab: "documents" });
    expect(parseSubjectLocation(params("code=M1"))).toEqual({ code: null, tab: "documents" });
  });

  it("isSubjectCode theo mẫu mã môn: chữ hoa rồi chữ số", () => {
    expect(isSubjectCode("MAU101")).toBe(true);
    expect(isSubjectCode("VXL01")).toBe(true);
    expect(isSubjectCode("vxl01")).toBe(false);
    expect(isSubjectCode("MAU")).toBe(false);
    expect(isSubjectCode("")).toBe(false);
  });
});
