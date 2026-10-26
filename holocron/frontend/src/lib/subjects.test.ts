import { describe, expect, it } from "vitest";

import { SUBJECT_FIXTURES } from "@/mocks/fixtures/subjects";

import { parseSubjectList, selectedSubject } from "./subjects";

describe("parseSubjectList (kiểm dạng ở ranh giới)", () => {
  it("fixture đúng contract đi qua nguyên vẹn", () => {
    expect(parseSubjectList(SUBJECT_FIXTURES)).toEqual(SUBJECT_FIXTURES);
  });

  it("phần tử null hoặc sai dạng bị bỏ, phần tử đúng giữ thứ tự", () => {
    const [first, second] = SUBJECT_FIXTURES;
    const list = parseSubjectList([
      null,
      first,
      3,
      { ...second, subject_code: "mau202" },
      { ...second, exam_format: "oral" },
      { ...second, version: 0 },
      { ...second, display_name: "   " },
      second,
      first,
    ]);
    expect(list?.map((s) => s.subject_code)).toEqual(["MAU101", "MAU202"]);
  });

  it("thân không phải mảng thì không nhận (spec §6.8 trả list[Subject])", () => {
    expect(parseSubjectList({ items: SUBJECT_FIXTURES })).toBeNull();
    expect(parseSubjectList(null)).toBeNull();
    expect(parseSubjectList("MAU101")).toBeNull();
  });

  it("field thừa của backend mới hơn không lọt vào kết quả", () => {
    const list = parseSubjectList([{ ...SUBJECT_FIXTURES[0], secret: "x" }]);
    expect(list?.[0]).toEqual(SUBJECT_FIXTURES[0]);
  });
});

describe("selectedSubject", () => {
  it("chỉ trả môn khi danh sách đã tải và mã còn trong danh sách", () => {
    const ready = { kind: "ready" as const, subjects: SUBJECT_FIXTURES };
    expect(selectedSubject(ready, "MAU202")?.exam_format).toBe("essay");
    expect(selectedSubject(ready, "KHAC101")).toBeNull();
    expect(selectedSubject(ready, null)).toBeNull();
    expect(selectedSubject({ kind: "loading" }, "MAU101")).toBeNull();
  });
});
