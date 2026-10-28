import { describe, expect, it } from "vitest";

import { SUBJECT_CODE_PATTERN } from "./limits";
import { nextSubjectCode, stripDiacritics, suggestSubjectCode } from "./subject-code";

describe("suggestSubjectCode", () => {
  it("bỏ dấu, lấy chữ cái đầu mỗi từ, số từ 01", () => {
    expect(suggestSubjectCode("Vi xử lý", [])).toBe("VXL01");
    expect(suggestSubjectCode("Điện tử số", [])).toBe("DTS01");
    expect(suggestSubjectCode("Vi xử lý".normalize("NFD"), [])).toBe("VXL01");
  });

  it("tên một từ: 2–6 chữ đầu", () => {
    expect(suggestSubjectCode("Toán", [])).toBe("TOAN01");
    expect(suggestSubjectCode("Kinhtếhọc", [])).toBe("KINHTE01");
    expect(suggestSubjectCode("A", [])).toBe("AM01");
  });

  it("nhiều hơn 6 từ: chỉ lấy 6 chữ đầu", () => {
    expect(suggestSubjectCode("Một hai ba bốn năm sáu bảy tám", [])).toBe("MHBBNS01");
  });

  it("trùng mã đã có thì tăng số, so không phân biệt hoa thường", () => {
    expect(suggestSubjectCode("Vi xử lý", ["VXL01"])).toBe("VXL02");
    expect(suggestSubjectCode("Vi xử lý", ["vxl01", "VXL02", "VXL04"])).toBe("VXL03");
  });

  it("ký tự lạ và chữ số trong tên bị bỏ", () => {
    expect(suggestSubjectCode("C++ & Lập trình (2)!", [])).toBe("CLT01");
    expect(suggestSubjectCode("数学 Giải tích 2", [])).toBe("GT01");
  });

  it("tên rỗng hay không có chữ Latin: dùng MH", () => {
    expect(suggestSubjectCode("", [])).toBe("MH01");
    expect(suggestSubjectCode("   ", ["MH01"])).toBe("MH02");
    expect(suggestSubjectCode("数学", [])).toBe("MH01");
  });

  it("kết quả luôn khớp mẫu mã môn", () => {
    const names = ["Vi xử lý", "", "A", "Một hai ba bốn năm sáu bảy", "Đ", "123", "x y"];
    for (const name of names) expect(suggestSubjectCode(name, [])).toMatch(SUBJECT_CODE_PATTERN);
  });

  it("stripDiacritics đổi đ/Đ và bỏ dấu", () => {
    expect(stripDiacritics("Đường đi")).toBe("Duong di");
  });
});

describe("suggestSubjectCode: tên đặc biệt", () => {
  it("toàn dấu câu hoặc toàn dấu thanh: dùng MH", () => {
    expect(suggestSubjectCode("!?… --- ###", [])).toBe("MH01");
    expect(suggestSubjectCode("́̀̃", [])).toBe("MH01");
  });

  it("toàn chữ số: dùng MH, số trong tên không vào mã", () => {
    expect(suggestSubjectCode("2026", [])).toBe("MH01");
    expect(suggestSubjectCode("123 456", ["MH01"])).toBe("MH02");
  });

  it("đúng 7 từ: lấy 6 chữ đầu", () => {
    expect(suggestSubjectCode("an bình cát dũng em giang hà", [])).toBe("ABCDEG01");
  });
});

describe("nextSubjectCode", () => {
  it("tăng phần số, giữ độ rộng tối thiểu", () => {
    expect(nextSubjectCode("VXL01", [])).toBe("VXL02");
    expect(nextSubjectCode("AB099", [])).toBe("AB100");
    expect(nextSubjectCode("vxl09", [])).toBe("VXL10");
  });

  it("bỏ qua mã đã có, so không phân biệt hoa thường", () => {
    expect(nextSubjectCode("VXL01", ["vxl02", "VXL03"])).toBe("VXL04");
  });

  it("mã sai mẫu hoặc hết số: null", () => {
    expect(nextSubjectCode("V1", [])).toBeNull();
    expect(nextSubjectCode("", [])).toBeNull();
    expect(nextSubjectCode("AB9999", [])).toBeNull();
  });

  it("kết quả luôn khớp mẫu mã môn", () => {
    for (const code of ["VXL01", "AB99", "ABCDEF0999"]) {
      expect(nextSubjectCode(code, [])).toMatch(SUBJECT_CODE_PATTERN);
    }
  });
});
