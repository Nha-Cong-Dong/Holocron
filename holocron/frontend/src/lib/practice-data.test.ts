import { describe, expect, it } from "vitest";

import { BLUEPRINT_FIXTURE } from "@/mocks/fixtures/blueprints";
import { CHUNK_FIXTURES } from "@/mocks/fixtures/chunks";
import { ESSAY_GRADE_FIXTURES } from "@/mocks/fixtures/essay";
import { ESSAY_SPACED_REVIEW_TWIN_ID, TWIN_BATCH_FIXTURE } from "@/mocks/fixtures/twins";

import { parseBlueprint, parseChunkPage, parseEssayGradeResult, parsePracticeBatch, parseTwin } from "./practice-data";

const [definitionChoice, conceptMatch, essayTwin] = TWIN_BATCH_FIXTURE.twins;

function requireValue<T>(value: T | undefined): T {
  if (value === undefined) throw new Error("fixture thiếu phần tử");
  return value;
}

describe("parsePracticeBatch", () => {
  it("batch mẫu đi qua đủ, sắp theo slot, ticket lý thuyết được nhận ra", () => {
    const batch = parsePracticeBatch(structuredClone(TWIN_BATCH_FIXTURE));
    expect(batch?.questions.map((q) => [q.slotIndex, q.kind])).toEqual([
      [1, "mcq"],
      [2, "mcq"],
      [3, "essay"],
      [4, "essay"],
    ]);
    expect(batch?.skipped).toEqual({ unsupported: 0, malformed: 0 });
    expect(batch?.tickets).toEqual([{ slotIndex: 5, reason: "không đủ căn cứ trong Vault", theory: true }]);
    expect(batch?.batchId).toBe(TWIN_BATCH_FIXTURE.batch_id);
  });

  it("cấp ngoài cùng lạ thì không nhận cả phản hồi", () => {
    for (const bad of [null, [], "x", { twins: [] }, { blueprint_id: "bp", twins: "x" }]) {
      expect(parsePracticeBatch(bad)).toBeNull();
    }
  });

  it("câu sai dạng bị bỏ và được đếm, không làm hỏng câu khác", () => {
    const batch = parsePracticeBatch({
      blueprint_id: "bp_x",
      twins: [null, 3, { ...definitionChoice, citation: null }, definitionChoice, { ...definitionChoice, format: "X" }],
      review_tickets: [null, { slot_index: 0, reason: "x" }],
    });
    expect(batch?.questions).toHaveLength(1);
    expect(batch?.skipped).toEqual({ unsupported: 0, malformed: 4 });
    expect(batch?.tickets).toEqual([]);
  });

  it("câu điền số chưa luyện được ở màn này: đếm riêng", () => {
    const batch = parsePracticeBatch({
      blueprint_id: "bp_x",
      twins: [{ ...definitionChoice, format: "NUMERICAL", options: null, correct_key: null }],
      review_tickets: [],
    });
    expect(batch?.skipped).toEqual({ unsupported: 1, malformed: 0 });
  });
});

describe("parseTwin: luật I2 và I3 ở ranh giới", () => {
  const twin = requireValue(definitionChoice);

  it("thiếu misconception cho một phương án sai, hoặc map chứa đáp án đúng, thì câu bị bỏ", () => {
    const { C: _dropped, ...partial } = twin.distractor_to_misconception_map;
    expect(parseTwin({ ...twin, distractor_to_misconception_map: partial }).kind).toBe("malformed");
    const withCorrect = { ...twin.distractor_to_misconception_map, B: twin.distractor_to_misconception_map.A };
    expect(parseTwin({ ...twin, distractor_to_misconception_map: withCorrect }).kind).toBe("malformed");
    const badCode = {
      ...twin.distractor_to_misconception_map,
      A: { misconception_code: "mã thường", label_vi: "x", rationale_vi: "y" },
    };
    expect(parseTwin({ ...twin, distractor_to_misconception_map: badCode }).kind).toBe("malformed");
  });

  it("thiếu phương án, đáp án ngoài A–D hay trích dẫn không có số trang thì câu bị bỏ", () => {
    expect(parseTwin({ ...twin, options: { A: "a", B: "b", C: "c" } }).kind).toBe("malformed");
    expect(parseTwin({ ...twin, correct_key: "E" }).kind).toBe("malformed");
    expect(parseTwin({ ...twin, citation: { ...twin.citation, page_number: 0 } }).kind).toBe("malformed");
    expect(parseTwin({ ...twin, citation: { ...twin.citation, verbatim_quote: "  " } }).kind).toBe("malformed");
  });

  it("misconception có trích dẫn riêng (câu lý thuyết) hoặc không có (câu tính toán, backend cũ)", () => {
    const parsed = parseTwin(twin);
    if (parsed.kind !== "ok" || parsed.question.kind !== "mcq") throw new Error("câu mẫu phải hợp lệ");
    expect(parsed.question.misconceptions.A?.citation?.chunk_id).toBe("chk_mau101_p4");
    const legacyMap = Object.fromEntries(
      Object.entries(twin.distractor_to_misconception_map).map(([key, ref]) => [
        key,
        { misconception_code: ref.misconception_code, label_vi: ref.label_vi, rationale_vi: ref.rationale_vi },
      ]),
    );
    const legacy = parseTwin({ ...twin, distractor_to_misconception_map: legacyMap });
    if (legacy.kind !== "ok" || legacy.question.kind !== "mcq") throw new Error("câu cũ phải hợp lệ");
    expect(legacy.question.misconceptions.A?.citation).toBeNull();
  });

  it("twin của backend cũ (không có question_kind, trace không có kind, gantt thay artifacts)", () => {
    const legacy = {
      ...twin,
      question_kind: undefined,
      artifacts: undefined,
      gantt: [
        { process: "P1", start: 0, end: 3 },
        { process: "P2", start: 3, end: 5 },
      ],
    };
    const parsed = parseTwin(legacy);
    if (parsed.kind !== "ok") throw new Error("twin cũ phải hợp lệ");
    expect(parsed.question.questionKind).toBe("computational");
    expect(parsed.question.artifacts).toEqual([
      {
        kind: "timeline",
        caption: null,
        segments: [
          { label: "P1", start: 0, end: 3 },
          { label: "P2", start: 3, end: 5 },
        ],
      },
    ]);
  });

  it("artifact lạ hoặc sai dạng bị bỏ, artifact đúng giữ nguyên", () => {
    const parsed = parseTwin({
      ...twin,
      artifacts: [
        { kind: "chart", data: [] },
        { kind: "table", caption_vi: "Bảng", headers: ["a", "b"], rows: [["1", "2"]] },
        { kind: "table", caption_vi: null, headers: ["a"], rows: [["1", "2"]] },
        { kind: "timeline", segments: [{ label: "x", start: 2, end: 1 }] },
      ],
    });
    if (parsed.kind !== "ok") throw new Error("câu phải hợp lệ");
    expect(parsed.question.artifacts).toEqual([
      { kind: "table", caption: "Bảng", headers: ["a", "b"], rows: [["1", "2"]] },
    ]);
  });

  it("câu tự luận cần essay có term và ít nhất một ý", () => {
    const essay = requireValue(essayTwin);
    const parsed = parseTwin(essay);
    if (parsed.kind !== "ok" || parsed.question.kind !== "essay") throw new Error("câu tự luận mẫu phải hợp lệ");
    expect(parsed.question.points).toHaveLength(2);
    expect(parseTwin({ ...essay, essay: null }).kind).toBe("malformed");
    expect(parseTwin({ ...essay, essay: { ...essay.essay, points: [] } }).kind).toBe("malformed");
  });

  it("câu ghép khái niệm giữ thân câu nguyên văn backend trả", () => {
    const parsed = parseTwin(requireValue(conceptMatch));
    expect(parsed.kind === "ok" && parsed.question.stem).toBe(conceptMatch?.stem);
  });
});

describe("parseEssayGradeResult", () => {
  const fixture = requireValue(ESSAY_GRADE_FIXTURES[ESSAY_SPACED_REVIEW_TWIN_ID]);

  it("giữ hai con số backend trả và văn bản các ý; bỏ ngram_coverage", () => {
    const view = parseEssayGradeResult(structuredClone(fixture));
    expect(view?.matchedCount).toBe(1);
    expect(view?.totalPoints).toBe(2);
    expect(JSON.stringify(view)).not.toContain("ngram");
    expect(JSON.stringify(view)).not.toContain("0.674");
  });

  it("con số mâu thuẫn với danh sách thì không nhận", () => {
    expect(parseEssayGradeResult({ ...fixture, matched_count: 2 })).toBeNull();
    expect(parseEssayGradeResult({ ...fixture, total_points: 3 })).toBeNull();
    expect(
      parseEssayGradeResult({ ...fixture, missing_points: [{ ...fixture.missing_points[0], point_index: 0 }] }),
    ).toBeNull();
    expect(parseEssayGradeResult({ ...fixture, citation: null })).toBeNull();
    expect(parseEssayGradeResult("x")).toBeNull();
  });

  it("chỉ số các ý phải phủ đúng 0..total_points-1", () => {
    const [matchedPoint] = fixture.matched_points;
    const [missingPoint] = fixture.missing_points;
    if (matchedPoint === undefined || missingPoint === undefined) throw new Error("fixture thiếu ý");
    // Ngoài phạm vi (không trùng, đếm vẫn khớp).
    expect(parseEssayGradeResult({ ...fixture, missing_points: [{ ...missingPoint, point_index: 2 }] })).toBeNull();
    expect(parseEssayGradeResult({ ...fixture, matched_points: [{ ...matchedPoint, point_index: 7 }] })).toBeNull();
    // Trùng chỉ số.
    expect(parseEssayGradeResult({ ...fixture, missing_points: [{ ...missingPoint, point_index: 0 }] })).toBeNull();
    // Đảo chỉ số giữa hai danh sách vẫn phủ đúng 0..1 nên được nhận.
    const swapped = {
      ...fixture,
      matched_points: [{ ...matchedPoint, point_index: 1 }],
      missing_points: [{ ...missingPoint, point_index: 0 }],
    };
    expect(parseEssayGradeResult(swapped)?.totalPoints).toBe(2);
  });

  it("văn bản mỗi ý phải là chuỗi con của trích dẫn sau NFC và gộp khoảng trắng", () => {
    const [matchedPoint] = fixture.matched_points;
    const [missingPoint] = fixture.missing_points;
    if (matchedPoint === undefined || missingPoint === undefined) throw new Error("fixture thiếu ý");
    const invented = { ...fixture, missing_points: [{ ...missingPoint, text: "Một ý không có trong tài liệu" }] };
    expect(parseEssayGradeResult(invented)).toBeNull();
    const blank = { ...fixture, missing_points: [{ ...missingPoint, text: " \n " }] };
    expect(parseEssayGradeResult(blank)).toBeNull();
    // Ý ở dạng NFD, khoảng trắng khác (kể cả U+0085 của Python) vẫn khớp trích dẫn NFC.
    const respaced = matchedPoint.text.normalize("NFD").replaceAll(" ", "\u0085\n ");
    const variant = { ...fixture, matched_points: [{ ...matchedPoint, text: respaced }] };
    expect(parseEssayGradeResult(variant)?.matched[0]?.text).toBe(respaced);
  });
});

describe("parseBlueprint và parseChunkPage", () => {
  it("blueprint mẫu đi qua; slot sai dạng thì không nhận", () => {
    expect(parseBlueprint(BLUEPRINT_FIXTURE)?.slots).toHaveLength(5);
    expect(parseBlueprint({ ...BLUEPRINT_FIXTURE, slots: [{ slot_index: 1 }] })).toBeNull();
    expect(parseBlueprint(null)).toBeNull();
  });

  it("chunk sai dạng bị bỏ, chunk đúng giữ nguyên văn bản", () => {
    const page = parseChunkPage({ total: 4, chunks: [null, ...CHUNK_FIXTURES] });
    expect(page?.chunks.map((c) => c.chunkId)).toEqual(CHUNK_FIXTURES.map((c) => c.chunk_id));
    expect(page?.chunks[0]?.rawText).toBe(CHUNK_FIXTURES[0]?.raw_text);
    expect(parseChunkPage({ chunks: [] })).toBeNull();
  });
});
