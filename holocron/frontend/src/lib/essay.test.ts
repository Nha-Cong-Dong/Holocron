/**
 * Bắt buộc (kế hoạch UI mục 6, F4): phần kết quả tự luận KHÔNG BAO GIỜ hiện điểm, phần trăm hay thang điểm. Hai con số
 * duy nhất là `matched_count` và `total_points` do backend trả; câu giới hạn luôn có mặt.
 */

import { describe, expect, it } from "vitest";

import { ESSAY_GRADE_FIXTURES } from "@/mocks/fixtures/essay";

import {
  ESSAY_LIMITATION_VI,
  essayDisplayedStrings,
  essayHeadline,
  essayResultView,
  essaySystemStrings,
} from "./essay";
import { type EssayGradeView, type PracticeCitation, parseEssayGradeResult } from "./practice-data";

const CITATION: PracticeCitation = {
  doc_id: "doc_0000000000000001",
  page_number: 9,
  verbatim_quote: "Một đoạn trích nguyên văn đủ dài.",
  chunk_id: null,
};

function result(matched: number, total: number): EssayGradeView {
  const points = Array.from({ length: total }, (_, index) => ({
    index,
    text: `Ý nguyên văn số ${index + 1} của tài liệu`,
  }));
  return {
    twinId: "twin_x",
    matchedCount: matched,
    totalPoints: total,
    matched: points.slice(0, matched),
    missing: points.slice(matched),
    citation: CITATION,
  };
}

/** Bộ kết quả dựng sẵn: mọi tổ hợp nhỏ, cộng các trường hợp biên. */
function sampleResults(): EssayGradeView[] {
  const results: EssayGradeView[] = [];
  for (let total = 1; total <= 7; total += 1) {
    for (let matched = 0; matched <= total; matched += 1) results.push(result(matched, total));
  }
  results.push(result(0, 40), result(40, 40), result(13, 37));
  for (const fixture of Object.values(ESSAY_GRADE_FIXTURES)) {
    const view = parseEssayGradeResult(structuredClone(fixture));
    if (view === null) throw new Error("fixture chấm tự luận phải hợp lệ");
    results.push(view);
  }
  return results;
}

const SCALE = /\/\s*(10|100)\s*(điểm|$)|thang điểm|trên 10|trên 100/i;

describe("essayResultView", () => {
  it("câu chính đúng dạng 'Khớp x/y ý' với hai số backend trả", () => {
    expect(essayHeadline({ matchedCount: 1, totalPoints: 2 })).toBe("Khớp 1/2 ý");
    expect(essayResultView(result(3, 5)).headline).toBe("Khớp 3/5 ý");
  });

  it("không bao giờ có %, chữ 'điểm', thang điểm hay giá trị ngram_coverage; câu giới hạn luôn có", () => {
    const coverageValues = Object.values(ESSAY_GRADE_FIXTURES).flatMap((fixture) =>
      [...fixture.matched_points, ...fixture.missing_points].flatMap((point) => [
        point.ngram_coverage.bigram,
        point.ngram_coverage.trigram,
        point.ngram_coverage.combined,
      ]),
    );
    for (const sample of sampleResults()) {
      const view = essayResultView(sample);
      const displayed = essayDisplayedStrings(view);
      for (const text of displayed) {
        expect(text).not.toContain("%");
        expect(text).not.toMatch(SCALE);
      }
      for (const text of essaySystemStrings(view)) {
        expect(text.toLocaleLowerCase("vi")).not.toContain("điểm");
      }
      const joined = displayed.join("\n");
      for (const value of coverageValues.filter((v) => v > 0 && v < 1)) {
        expect(joined).not.toContain(String(value));
      }
      expect(view.limitation).toBe(ESSAY_LIMITATION_VI);
      expect(essaySystemStrings(view)).toContain(ESSAY_LIMITATION_VI);
    }
  });

  it("hai số duy nhất trong các chuỗi hệ thống là matched_count và total_points", () => {
    for (const sample of sampleResults()) {
      const numbers = essaySystemStrings(essayResultView(sample)).flatMap((text) => text.match(/\d+/g) ?? []);
      expect(numbers).toEqual([String(sample.matchedCount), String(sample.totalPoints)]);
    }
  });

  it("danh sách rỗng có câu thay thế, văn bản các ý giữ nguyên", () => {
    const none = essayResultView(result(0, 2));
    expect(none.matchedEmpty).not.toBeNull();
    expect(none.missingEmpty).toBeNull();
    expect(none.missing.map((p) => p.text)).toEqual([
      "Ý nguyên văn số 1 của tài liệu",
      "Ý nguyên văn số 2 của tài liệu",
    ]);
    const all = essayResultView(result(2, 2));
    expect(all.missingEmpty).not.toBeNull();
    expect(all.citation).toEqual(CITATION);
  });
});
