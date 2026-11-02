/** Màn kết quả nhập (T2.2): kiểm dạng contract, nhãn phủ đủ enum, điều kiện nút, 409 và job. */

import { describe, expect, it } from "vitest";

import type { ProposalJobAbsentReason, ProposalRejectReason } from "@/types/contract-pending";
import { REVIEW_REASONS } from "@/types/contract-pending";

import { REVIEW_REASON_LABEL_VI, reviewReasonLabel, UNKNOWN_REVIEW_REASON_VI } from "./documents";
import {
  absentJobView,
  acceptGate,
  acceptGateText,
  acceptOutcome,
  classifyConfirmConflict,
  confirmConflictError,
  DROPPED_ROLE_UNKNOWN_VI,
  droppedGroups,
  droppedRoleLabel,
  firstNeedsReviewPage,
  type ImportResultView,
  importSummaryLine,
  isJobActive,
  jobHeadline,
  jobLines,
  pagesText,
  parseImportResult,
  parseProposalJob,
  REJECT_REASON_LABEL_VI,
  REJECT_REASON_UNKNOWN_VI,
  rejectedLines,
  rejectReasonLabel,
  shouldPollJob,
} from "./import-result";

const DOC_ID = "doc_0123456789abcdef";

function rawPage(pageNumber: number, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    page_number: pageNumber,
    status: "ok",
    tier_used: "text_layer",
    review_reasons: [],
    confirmed: false,
    page_role: "content",
    dropped: false,
    ...extra,
  };
}

function rawJob(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    job_id: "pjob_00112233445566ff",
    status: "running",
    batches_total: 4,
    batches_done: 1,
    batches_failed: 0,
    proposed_count: 2,
    rejected_count: 1,
    rejected_by_reason: { duplicate: 1 },
    ...extra,
  };
}

function rawImport(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    meta: {
      doc_id: DOC_ID,
      subject_code: "MAU101",
      title: "Tài liệu thử",
      source_kind: "lecture_slides",
      page_count: 3,
      has_text_layer: true,
      uploaded_at: "2026-11-01T08:00:00Z",
    },
    filename: "thu.pdf",
    size_bytes: 1000,
    chapter_code: "ch1",
    status: "review",
    pages: [rawPage(1), rawPage(2, { status: "needs_review", review_reasons: ["LOW_OCR_SCORE"] }), rawPage(3)],
    pages_kept: 3,
    pages_dropped: 0,
    pages_needs_review: 1,
    pages_auto_confirmed: 1,
    pages_kept_unconfirmed: 1,
    pages_failed: 0,
    review_reason_counts: [{ reason: "LOW_OCR_SCORE", count: 1, pages: [2] }],
    dropped_role_counts: [],
    proposal_job: null,
    proposal_job_absent_reason: "not_confirmed",
    ...extra,
  };
}

function parsed(extra: Record<string, unknown> = {}): ImportResultView {
  const view = parseImportResult(rawImport(extra));
  if (view === null) throw new Error("fixture phải parse được");
  return view;
}

describe("parseImportResult: dữ liệu sai dạng không sập", () => {
  it("không phải object, thiếu meta hay pages ⇒ null", () => {
    for (const bad of [null, undefined, 3, "x", [], {}, rawImport({ meta: null }), rawImport({ pages: "x" })]) {
      expect(parseImportResult(bad)).toBeNull();
    }
  });

  it("chép nguyên các số backend trả", () => {
    const view = parsed();
    expect(view).toMatchObject({
      pagesKept: 3,
      pagesDropped: 0,
      pagesNeedsReview: 1,
      pagesAutoConfirmed: 1,
      pagesKeptUnconfirmed: 1,
      pagesFailed: 0,
      absentReason: "not_confirmed",
      job: null,
    });
    expect(view.reviewReasons).toEqual([{ reason: "LOW_OCR_SCORE", count: 1, pages: [2] }]);
  });

  it("số âm, số lẻ, chuỗi, null ⇒ bỏ số đó (undefined), không hiện số sai", () => {
    const view = parsed({
      pages_kept: -1,
      pages_dropped: 1.5,
      pages_needs_review: "3",
      pages_auto_confirmed: null,
      pages_kept_unconfirmed: Number.NaN,
      pages_failed: {},
    });
    expect(view.pagesKept).toBeUndefined();
    expect(view.pagesDropped).toBeUndefined();
    expect(view.pagesNeedsReview).toBeUndefined();
    expect(view.pagesAutoConfirmed).toBeUndefined();
    expect(view.pagesKeptUnconfirmed).toBeUndefined();
    expect(view.pagesFailed).toBeUndefined();
    expect(importSummaryLine(view)).toBe("");
  });

  it("backend cũ không có field T1e ⇒ parse được, số vắng, danh sách rỗng, không có job", () => {
    const old = rawImport();
    for (const key of [
      "pages_auto_confirmed",
      "pages_kept_unconfirmed",
      "pages_failed",
      "review_reason_counts",
      "dropped_role_counts",
      "proposal_job",
      "proposal_job_absent_reason",
    ]) {
      delete old[key];
    }
    const view = parseImportResult(old);
    expect(view).not.toBeNull();
    expect(view?.pagesAutoConfirmed).toBeUndefined();
    expect(view?.reviewReasons).toEqual([]);
    expect(view?.droppedRoles).toEqual([]);
    expect(view?.job).toBeNull();
    expect(view?.absentReason).toBeNull();
  });

  it("danh sách sai dạng ⇒ rỗng; phần tử hỏng bị bỏ, phần tử tốt giữ", () => {
    expect(parsed({ review_reason_counts: "x", dropped_role_counts: { a: 1 } }).reviewReasons).toEqual([]);
    const view = parsed({
      review_reason_counts: [
        null,
        { reason: "", count: 1, pages: [1] },
        { reason: "LOW_OCR_SCORE", count: 0, pages: [1] },
        { reason: "LOW_OCR_SCORE", count: -2, pages: [1] },
        { reason: "TEXT_OCCLUDED", count: 2, pages: [5, "x", 3, 3, 0, 1.5] },
      ],
    });
    expect(view.reviewReasons).toEqual([{ reason: "TEXT_OCCLUDED", count: 2, pages: [3, 5] }]);
  });

  it("mã lạ giữ dạng chuỗi và rơi về nhãn chung", () => {
    const view = parsed({
      status: "weird",
      review_reason_counts: [{ reason: "NEW_CODE_FROM_FUTURE", count: 1, pages: [2] }],
      proposal_job_absent_reason: "from_future",
    });
    expect(view.document.status).toBe("unknown");
    expect(view.reviewReasons[0]?.reason).toBe("NEW_CODE_FROM_FUTURE");
    expect(reviewReasonLabel("NEW_CODE_FROM_FUTURE")).toBe(UNKNOWN_REVIEW_REASON_VI);
    expect(absentJobView(view.absentReason).kind).toBe("message");
  });
});

describe("parseProposalJob", () => {
  it("chép số của backend; mục rejected_by_reason hợp lệ giữ thứ tự", () => {
    const job = parseProposalJob(rawJob({ rejected_by_reason: { duplicate: 2, quote_not_verbatim: 1, x: 0, y: -1 } }));
    expect(job).toMatchObject({ status: "running", batchesTotal: 4, batchesDone: 1, proposedCount: 2 });
    expect(job?.rejectedByReason).toEqual([
      { reason: "duplicate", count: 2 },
      { reason: "quote_not_verbatim", count: 1 },
    ]);
  });

  it("sai dạng ⇒ null hoặc bỏ số; trạng thái lạ thành unknown", () => {
    for (const bad of [null, 1, [], {}, { job_id: "pjob_x" }, { status: "running" }]) {
      expect(parseProposalJob(bad)).toBeNull();
    }
    const job = parseProposalJob(rawJob({ status: "exploded", batches_done: -3, rejected_by_reason: [1, 2] }));
    expect(job?.status).toBe("unknown");
    expect(job?.batchesDone).toBeUndefined();
    expect(job?.rejectedByReason).toEqual([]);
  });

  it("proposal_job hỏng trong DocumentImport ⇒ coi như không có job, màn vẫn dựng", () => {
    const view = parsed({ proposal_job: { job_id: 5 } });
    expect(view.job).toBeNull();
  });
});

describe("nhãn phủ đủ enum", () => {
  it("mọi ReviewReason của spec có nhãn riêng (không rơi về nhãn chung)", () => {
    for (const reason of REVIEW_REASONS) {
      expect(REVIEW_REASON_LABEL_VI[reason]).toBeTruthy();
      expect(reviewReasonLabel(reason)).not.toBe(UNKNOWN_REVIEW_REASON_VI);
    }
  });

  it("đủ 13 ProposalRejectReason có nhãn tiếng Việt, không lộ mã", () => {
    const all: Record<ProposalRejectReason, true> = {
      quote_not_verbatim: true,
      unknown_chunk: true,
      invalid_term: true,
      invalid_quote: true,
      duplicate: true,
      chunk_limit: true,
      proposal_limit: true,
      unknown_span: true,
      span_cross_chunk: true,
      term_span_cross_chunk: true,
      term_not_in_span: true,
      anchor_not_found: true,
      anchor_ambiguous: true,
    };
    const codes = Object.keys(all);
    expect(codes).toHaveLength(13);
    expect(Object.keys(REJECT_REASON_LABEL_VI).sort()).toEqual([...codes].sort());
    for (const code of codes) {
      const label = rejectReasonLabel(code);
      expect(label).not.toBe(REJECT_REASON_UNKNOWN_VI);
      expect(label).not.toContain("_");
    }
    expect(rejectReasonLabel("from_future")).toBe(REJECT_REASON_UNKNOWN_VI);
    expect(rejectReasonLabel("constructor")).toBe(REJECT_REASON_UNKNOWN_VI);
  });

  it("vai trò trang bị ẩn có nhãn; mã lạ rơi về nhãn chung", () => {
    for (const role of ["cover_or_course_nav", "chapter_nav", "build_step"]) {
      expect(droppedRoleLabel(role)).not.toBe(DROPPED_ROLE_UNKNOWN_VI);
    }
    expect(droppedRoleLabel("zzz")).toBe(DROPPED_ROLE_UNKNOWN_VI);
  });
});

describe("importSummaryLine", () => {
  it("chép đúng số backend, theo thứ tự cố định", () => {
    const view = parsed({
      pages_kept: 7,
      pages_auto_confirmed: 5,
      pages_needs_review: 2,
      pages_dropped: 3,
      pages_failed: 1,
    });
    expect(importSummaryLine(view)).toBe(
      "7 trang giữ lại · 5 trang tự xác nhận · 2 trang cần xem · 3 trang đã ẩn tự động · 1 trang đọc lỗi",
    );
  });

  it("không tự cộng: số kept khác tổng các số khác vẫn hiện nguyên", () => {
    const view = parsed({
      pages_kept: 99,
      pages_auto_confirmed: 0,
      pages_needs_review: 0,
      pages_dropped: 0,
      pages_failed: 0,
    });
    expect(importSummaryLine(view)).toContain("99 trang giữ lại");
  });

  it("số vắng thì bỏ đoạn đó", () => {
    const view = parsed({
      pages_kept: 4,
      pages_auto_confirmed: "x",
      pages_needs_review: 1,
      pages_dropped: null,
      pages_failed: -1,
    });
    expect(importSummaryLine(view)).toBe("4 trang giữ lại · 1 trang cần xem");
  });
});

describe("acceptGate: điều kiện nút theo số backend trả", () => {
  it("còn trang giữ chưa xác nhận ⇒ bật", () => {
    expect(acceptGate(parsed({ pages_kept_unconfirmed: 2, pages_auto_confirmed: 0 }))).toBe("enabled");
  });

  it("chỉ có trang tự xác nhận chờ chốt ⇒ bật (chỉ bước chốt mới đóng được tài liệu)", () => {
    expect(acceptGate(parsed({ pages_kept_unconfirmed: 0, pages_auto_confirmed: 3, pages_needs_review: 0 }))).toBe(
      "enabled",
    );
  });

  it("chỉ còn trang cần xem ⇒ khoá, nêu số trang", () => {
    const view = parsed({ pages_kept_unconfirmed: 0, pages_auto_confirmed: 0, pages_needs_review: 2 });
    expect(acceptGate(view)).toBe("review_first");
    expect(acceptGateText("review_first", view)).toContain("2 trang cần xem");
  });

  it("không còn gì ⇒ khoá", () => {
    const view = parsed({ pages_kept_unconfirmed: 0, pages_auto_confirmed: 0, pages_needs_review: 0 });
    expect(acceptGate(view)).toBe("nothing");
  });

  it("đã confirmed, đang đọc, trạng thái lạ ⇒ khoá dù số đếm còn", () => {
    expect(acceptGate(parsed({ status: "confirmed", pages_kept_unconfirmed: 5 }))).toBe("accepted");
    expect(acceptGate(parsed({ status: "processing", pages_kept_unconfirmed: 5 }))).toBe("reading");
    expect(acceptGate(parsed({ status: "zzz", pages_kept_unconfirmed: 5 }))).toBe("unknown");
  });

  it("số đếm vắng ⇒ khoá (không đoán)", () => {
    expect(acceptGate(parsed({ pages_kept_unconfirmed: "x", pages_auto_confirmed: null }))).toBe("unknown");
  });

  it("trạng thái failed vẫn bật khi còn trang để chấp nhận (bước chốt chạy cả đường failed)", () => {
    expect(acceptGate(parsed({ status: "failed", pages_kept_unconfirmed: 1 }))).toBe("enabled");
  });

  it("câu giải thích có cho mọi trạng thái khoá và không có khi bật", () => {
    const view = parsed();
    expect(acceptGateText("enabled", view)).toBeNull();
    for (const gate of ["accepted", "reading", "review_first", "nothing", "unknown"] as const) {
      expect(acceptGateText(gate, view)).toBeTruthy();
    }
  });
});

describe("acceptOutcome sau khi gọi confirm trả 200", () => {
  it("confirmed ⇒ chấp nhận xong (hiện bước job)", () => {
    expect(acceptOutcome(parsed({ status: "confirmed", pages_needs_review: 0 }))).toEqual({ kind: "accepted" });
  });

  it("status khác confirmed ⇒ không hiện job, nêu N = pages_needs_review và trang đầu", () => {
    const outcome = acceptOutcome(
      parsed({
        status: "review",
        pages_needs_review: 2,
        pages: [
          rawPage(1, { confirmed: true }),
          rawPage(4, { status: "needs_review" }),
          rawPage(6, { status: "needs_review" }),
        ],
      }),
    );
    expect(outcome.kind).toBe("pages_left");
    if (outcome.kind === "pages_left") {
      expect(outcome.message).toContain("Còn 2 trang cần xem");
      expect(outcome.firstPage).toBe(4);
    }
  });

  it("không có trang trong hàng đợi thì lấy trang nhỏ nhất của lý do; không có cả hai ⇒ null", () => {
    const withReasons = parsed({
      status: "review",
      pages: [rawPage(1)],
      review_reason_counts: [
        { reason: "LOW_OCR_SCORE", count: 2, pages: [9, 5] },
        { reason: "TEXT_OCCLUDED", count: 1, pages: [7] },
      ],
    });
    expect(firstNeedsReviewPage(withReasons)).toBe(5);
    expect(firstNeedsReviewPage(parsed({ pages: [rawPage(1)], review_reason_counts: [] }))).toBeNull();
  });

  it("status review mà không còn trang cần xem ⇒ câu chung, không in Còn 0 trang", () => {
    const outcome = acceptOutcome(parsed({ status: "review", pages_needs_review: 0 }));
    expect(outcome.kind).toBe("pages_left");
    if (outcome.kind === "pages_left") expect(outcome.message).not.toContain("Còn 0");
  });
});

describe("409 khi chấp nhận", () => {
  const before = parsed({
    status: "review",
    pages_needs_review: 1,
    pages_kept_unconfirmed: 2,
    pages_auto_confirmed: 1,
  });

  it("lệch version: số đếm hay trạng thái đã đổi ⇒ changed, tải lại", () => {
    const after = parsed({
      status: "review",
      pages_needs_review: 0,
      pages_kept_unconfirmed: 2,
      pages_auto_confirmed: 1,
    });
    const conflict = classifyConfirmConflict(before, after);
    expect(conflict).toBe("changed");
    const error = confirmConflictError(conflict);
    expect(error.action).toBe("reload");
    expect(error.kind).toBe("version_conflict");
    expect(error.message).toContain("Tải lại");
  });

  it("đang chốt: không có gì đổi ⇒ settling, thử lại sau", () => {
    const after = parsed({
      status: "review",
      pages_needs_review: 1,
      pages_kept_unconfirmed: 2,
      pages_auto_confirmed: 1,
    });
    const conflict = classifyConfirmConflict(before, after);
    expect(conflict).toBe("settling");
    const error = confirmConflictError(conflict);
    expect(error.action).toBe("retry");
    expect(error.message).toContain("thử lại");
  });

  it("không đọc lại được ⇒ settling (an toàn: cho thử lại)", () => {
    expect(classifyConfirmConflict(before, null)).toBe("settling");
  });

  it("còn trang đang đọc ⇒ reading; tài liệu nhập từ bản cũ chưa có chương ⇒ legacy", () => {
    expect(classifyConfirmConflict(before, parsed({ status: "processing" }))).toBe("reading");
    expect(classifyConfirmConflict(before, parsed({ chapter_code: null }))).toBe("legacy");
    expect(confirmConflictError("legacy").action).toBe("none");
    expect(confirmConflictError("reading").action).toBe("reload");
  });

  it("câu 409 không lộ mã HTTP hay tên field", () => {
    for (const conflict of ["changed", "reading", "settling", "legacy"] as const) {
      const message = confirmConflictError(conflict).message;
      expect(message).not.toMatch(/409|pages_|confirmed|status|expected_version/);
    }
  });
});

describe("job đề xuất cặp", () => {
  it("chỉ queued và running còn chạy; trạng thái kết thúc và mã lạ thì không", () => {
    expect(isJobActive("queued")).toBe(true);
    expect(isJobActive("running")).toBe(true);
    for (const status of ["succeeded", "partial", "failed", "cancelled", "unknown"] as const) {
      expect(isJobActive(status)).toBe(false);
    }
  });

  it("shouldPollJob: có job còn chạy mới hỏi tiếp", () => {
    expect(shouldPollJob(parsed({ proposal_job: rawJob({ status: "queued" }) }))).toBe(true);
    expect(shouldPollJob(parsed({ proposal_job: rawJob({ status: "succeeded" }) }))).toBe(false);
    expect(shouldPollJob(parsed({ proposal_job: null }))).toBe(false);
  });

  it("dòng số và chi tiết bị loại chép số backend, nhãn tiếng Việt", () => {
    const job = parseProposalJob(
      rawJob({ batches_failed: 1, rejected_by_reason: { duplicate: 1, quote_not_verbatim: 2 }, rejected_count: 3 }),
    );
    if (job === null) throw new Error("job phải parse được");
    expect(jobLines(job)).toEqual(["Lô đã xong: 1/4", "Lô lỗi: 1", "Cặp đã đề xuất: 2", "Đề xuất bị loại: 3"]);
    expect(rejectedLines(job)).toEqual(["Trùng cặp đã có: 1", "Trích dẫn không nguyên văn trong tài liệu: 2"]);
    expect(jobHeadline(job)).toBe("Đang đề xuất cặp khái niệm: lô 1/4…");
  });

  it("số vắng thì bỏ dòng; câu chạy không còn số lô", () => {
    const job = parseProposalJob(
      rawJob({ batches_done: "x", batches_failed: null, proposed_count: -1, rejected_count: 1.2 }),
    );
    if (job === null) throw new Error("job phải parse được");
    expect(jobLines(job)).toEqual([]);
    expect(jobHeadline(job)).toBe("Đang đề xuất cặp khái niệm…");
  });

  it("mọi trạng thái có câu, không lộ mã", () => {
    for (const status of ["queued", "running", "succeeded", "partial", "failed", "cancelled", "zzz"]) {
      const job = parseProposalJob(rawJob({ status }));
      if (job === null) throw new Error("job phải parse được");
      const text = jobHeadline(job);
      expect(text).toBeTruthy();
      expect(text).not.toMatch(/succeeded|partial|cancelled|queued/);
    }
  });
});

describe("absentJobView: đủ 5 lý do không có job", () => {
  const reasons: ProposalJobAbsentReason[] = [
    "not_confirmed",
    "past_exam",
    "no_chunks",
    "llm_unavailable",
    "not_retained",
  ];

  it("not_confirmed ẩn khối job", () => {
    expect(absentJobView("not_confirmed")).toEqual({ kind: "hidden" });
    expect(absentJobView(null)).toEqual({ kind: "hidden" });
  });

  it("past_exam và no_chunks chỉ có câu", () => {
    expect(absentJobView("past_exam").kind).toBe("message");
    expect(absentJobView("no_chunks").kind).toBe("message");
  });

  it("llm_unavailable: câu cần cấu hình LLM và đường tới tự bôi", () => {
    const view = absentJobView("llm_unavailable");
    expect(view.kind).toBe("configure_llm");
    if (view.kind === "configure_llm") expect(view.text).toContain("Cần cấu hình LLM để tự đề xuất");
  });

  it("not_retained: có nút Đề xuất lại", () => {
    expect(absentJobView("not_retained").kind).toBe("restart");
  });

  it("không lộ enum thô ở bất kỳ nhánh nào, kể cả mã lạ", () => {
    for (const reason of [...reasons, "from_future"]) {
      const view = absentJobView(reason);
      if (view.kind !== "hidden") {
        expect(view.text).not.toContain("_");
      }
    }
    expect(absentJobView("from_future")).toEqual({
      kind: "message",
      text: "Chưa có đề xuất cặp khái niệm cho tài liệu này.",
    });
  });
});

describe("droppedGroups và pagesText", () => {
  it("theo dropped_role_counts của backend, giữ thứ tự", () => {
    const view = parsed({
      dropped_role_counts: [
        { reason: "cover_or_course_nav", count: 1, pages: [1] },
        { reason: "build_step", count: 2, pages: [4, 5] },
      ],
    });
    expect(droppedGroups(view).map((group) => [group.key, group.pages])).toEqual([
      ["cover_or_course_nav", [1]],
      ["build_step", [4, 5]],
    ]);
    expect(pagesText([4, 5])).toBe("trang 4, 5");
    expect(pagesText([])).toBe("");
  });

  it("backend không trả danh sách vai trò ⇒ lấy trang dropped trong pages[] thành một nhóm chung", () => {
    const view = parsed({
      pages: [rawPage(1), rawPage(2, { dropped: true }), rawPage(3, { dropped: true })],
      dropped_role_counts: "x",
    });
    const groups = droppedGroups(view);
    expect(groups).toHaveLength(1);
    expect(groups[0]?.pages).toEqual([2, 3]);
    expect(groups[0]?.label).toBe(DROPPED_ROLE_UNKNOWN_VI);
  });

  it("không có trang bỏ mềm ⇒ không có nhóm", () => {
    expect(droppedGroups(parsed())).toEqual([]);
  });
});
