/**
 * Logic thuần của danh sách cặp `proposed` (T2.3): chọn nhiều cặp, nhóm "Cần kiểm tra", hàng đợi tuần tự (404, 409, 422,
 * dừng giữa chừng, không gửi lại cặp đã ghi), điều kiện nút, đích điều hướng và đọc danh sách job.
 */

import { describe, expect, it } from "vitest";

import type { ConceptPair } from "@/types/holocron";

import {
  BATCH_EDITING_VI,
  BATCH_NONE_SELECTED_VI,
  BATCH_RUNNING_VI,
  type BatchItem,
  type BatchOutcome,
  batchButtons,
  batchCompleted,
  batchSummary,
  canStartJob,
  chosenIds,
  deselectAfter,
  EMPTY_SELECTION,
  editingAmong,
  gateLabel,
  LLM_NEEDED_VI,
  latestJob,
  needsCheck,
  needsCheckReason,
  PAIR_GATE_UNKNOWN_VI,
  parseProposalJobList,
  practiceTarget,
  progressText,
  runBatch,
  type SelectionState,
  selectAllEvent,
  selectAllState,
  selectionReducer,
  shouldOpenPractice,
  splitProposals,
  withoutSettled,
} from "./concept-proposals";
import { ApiError, classifyHttpError } from "./errors";
import { subjectHref } from "./subject-route";

function pair(id: string, extra: Partial<ConceptPair> = {}): ConceptPair {
  return {
    pair_id: id,
    chunk_id: "chunk_0000000000000001",
    subject_code: "MAU101",
    chapter_code: "ch1",
    term: `Thuật ngữ ${id}`,
    definition_quote: "Một định nghĩa.",
    term_spans: [],
    status: "proposed",
    proposed_by: "llm",
    mapping_confidence: null,
    version: 1,
    created_at: "2026-11-01T00:00:00Z",
    confirmed_at: null,
    pre_selected: false,
    failed_gates: [],
    ...extra,
  };
}

function select(state: SelectionState, ...ids: string[]): SelectionState {
  return selectionReducer(state, { type: "set_many", pairIds: ids, on: true });
}

describe("chọn nhiều cặp", () => {
  it("mặc định không tích; chỉ cặp có pre_selected = true được tích sẵn", () => {
    const state = selectionReducer(EMPTY_SELECTION, {
      type: "sync",
      pairs: [pair("a"), pair("b", { pre_selected: true }), pair("c", { pre_selected: false })],
    });
    expect([...state.selected]).toEqual(["b"]);
  });

  it("cặp có failed_gates mà pre_selected = false thì không tích (lọc theo field, không tự tính cổng)", () => {
    const gated = pair("g", { failed_gates: ["G5_NUMBERS"], pre_selected: false });
    const state = selectionReducer(EMPTY_SELECTION, { type: "sync", pairs: [gated] });
    expect(state.selected.size).toBe(0);
  });

  it("sync lần sau giữ lựa chọn của người dùng, không tích lại cặp đã bỏ tích", () => {
    const pairs = [pair("a", { pre_selected: true }), pair("b", { pre_selected: true })];
    let state = selectionReducer(EMPTY_SELECTION, { type: "sync", pairs });
    state = selectionReducer(state, { type: "toggle", pairId: "a" });
    state = selectionReducer(state, { type: "sync", pairs });
    expect([...state.selected]).toEqual(["b"]);
  });

  it("sync bỏ lựa chọn của cặp đã rời danh sách; cặp mới được xét mặc định", () => {
    let state = selectionReducer(EMPTY_SELECTION, {
      type: "sync",
      pairs: [pair("a", { pre_selected: true }), pair("b")],
    });
    state = select(state, "b");
    state = selectionReducer(state, { type: "sync", pairs: [pair("b"), pair("c", { pre_selected: true })] });
    expect([...state.selected].sort()).toEqual(["b", "c"]);
  });

  it("toggle chọn rồi bỏ; remove bỏ đúng các cặp nêu", () => {
    let state = selectionReducer(EMPTY_SELECTION, { type: "toggle", pairId: "a" });
    expect(state.selected.has("a")).toBe(true);
    state = selectionReducer(state, { type: "toggle", pairId: "a" });
    expect(state.selected.has("a")).toBe(false);
    state = select(state, "a", "b", "c");
    state = selectionReducer(state, { type: "remove", pairIds: ["a", "c"] });
    expect([...state.selected]).toEqual(["b"]);
  });

  it("Chọn tất cả là hộp ba trạng thái theo các cặp chọn được", () => {
    const ids = ["a", "b", "c"];
    expect(selectAllState(ids, new Set())).toBe("none");
    expect(selectAllState(ids, new Set(["a"]))).toBe("some");
    expect(selectAllState(ids, new Set(ids))).toBe("all");
    expect(selectAllState([], new Set(["a"]))).toBe("none");
    // Cặp được chọn nhưng không còn chọn được (đang bận) không tính vào hộp.
    expect(selectAllState(["a"], new Set(["a", "zzz"]))).toBe("all");
  });

  it("bấm Chọn tất cả: chưa đủ ⇒ chọn hết; đã đủ ⇒ bỏ chọn hết; chỉ đụng các cặp chọn được", () => {
    const ids = ["a", "b"];
    const part = new Set(["a", "keep"]);
    const on = selectionReducer({ selected: part, seen: new Set() }, selectAllEvent(ids, part));
    expect([...on.selected].sort()).toEqual(["a", "b", "keep"]);
    const full = new Set(["a", "b", "keep"]);
    const off = selectionReducer({ selected: full, seen: new Set() }, selectAllEvent(ids, full));
    expect([...off.selected]).toEqual(["keep"]);
  });

  it("chosenIds giữ thứ tự của danh sách chọn được", () => {
    expect(chosenIds(["c", "a", "b"], new Set(["b", "c", "x"]))).toEqual(["c", "b"]);
  });
});

describe("nhóm Cần kiểm tra", () => {
  it("lọc theo failed_gates không rỗng, giữ thứ tự backend", () => {
    const pairs = [
      pair("a"),
      pair("b", { failed_gates: ["EXERCISE"] }),
      pair("c"),
      pair("d", { failed_gates: ["G1_SOURCE"] }),
    ];
    expect(pairs.map(needsCheck)).toEqual([false, true, false, true]);
    const groups = splitProposals(pairs);
    expect(groups.ready.map((item) => item.pair_id)).toEqual(["a", "c"]);
    expect(groups.needsCheck.map((item) => item.pair_id)).toEqual(["b", "d"]);
  });

  it("câu lý do không lộ mã cổng thô; mã lạ ⇒ câu chung", () => {
    const reason = needsCheckReason(pair("x", { failed_gates: ["G5_NUMBERS", "EXERCISE"] }));
    expect(reason).not.toBeNull();
    expect(reason).not.toMatch(/G5_NUMBERS|EXERCISE/);
    expect(needsCheckReason(pair("y"))).toBeNull();
    expect(gateLabel("GX_NEW")).toBe(PAIR_GATE_UNKNOWN_VI);
  });
});

describe("điều kiện các nút", () => {
  it("không chọn gì ⇒ khoá kèm lý do; chọn ⇒ bật; đang gửi ⇒ khoá; đang sửa tên ⇒ khoá", () => {
    expect(batchButtons({ chosen: 0, running: false, editing: false })).toEqual({
      acceptEnabled: false,
      rejectEnabled: false,
      reason: BATCH_NONE_SELECTED_VI,
    });
    expect(batchButtons({ chosen: 2, running: false, editing: false })).toEqual({
      acceptEnabled: true,
      rejectEnabled: true,
      reason: null,
    });
    expect(batchButtons({ chosen: 2, running: true, editing: false }).reason).toBe(BATCH_RUNNING_VI);
    expect(batchButtons({ chosen: 2, running: false, editing: true }).reason).toBe(BATCH_EDITING_VI);
  });

  it("editingAmong chỉ trả cặp đã chọn đang mở khung sửa", () => {
    expect(editingAmong(["a", "b", "c"], new Set(["b", "z"]))).toEqual(["b"]);
  });
});

function httpError(status: number, detail: unknown = "x"): ApiError {
  return new ApiError(status, classifyHttpError(status, { detail }, { context: "pair_confirm", versioned: true }));
}

function items(...ids: string[]): BatchItem[] {
  return ids.map((pairId) => ({ pairId, version: 1, term: null }));
}

describe("runBatch (hàng đợi tuần tự)", () => {
  it("gửi lần lượt theo thứ tự, báo tiến độ i/n trước mỗi cặp, mỗi cặp đúng một lần", async () => {
    const sent: string[] = [];
    const progress: string[] = [];
    const outcome = await runBatch(
      items("a", "b", "c"),
      async (item) => {
        sent.push(item.pairId);
      },
      (p) => progress.push(progressText("accept", p)),
    );
    expect(sent).toEqual(["a", "b", "c"]);
    expect(progress).toEqual(["Đang chấp nhận 1/3…", "Đang chấp nhận 2/3…", "Đang chấp nhận 3/3…"]);
    expect(outcome).toEqual({ done: ["a", "b", "c"], conflicts: [], stopped: null, untouched: [] });
    expect(batchCompleted(outcome)).toBe(true);
    expect(progressText("reject", { current: 1, total: 2 })).toBe("Đang từ chối 1/2…");
  });

  it("không gửi chồng: cặp sau chỉ gửi khi cặp trước đã xong", async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    await runBatch(items("a", "b", "c"), async () => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await Promise.resolve();
      inFlight -= 1;
    });
    expect(maxInFlight).toBe(1);
  });

  it("409 chỉ bỏ qua cặp đó, đi tiếp các cặp sau", async () => {
    const outcome = await runBatch(items("a", "b", "c"), async (item) => {
      if (item.pairId === "b") throw httpError(409);
    });
    expect(outcome.done).toEqual(["a", "c"]);
    expect(outcome.conflicts).toEqual(["b"]);
    expect(outcome.stopped).toBeNull();
    expect(batchCompleted(outcome)).toBe(false);
    expect(shouldOpenPractice("accept", outcome)).toBe(false);
  });

  it("404 dừng hàng đợi: cặp sau chưa gửi, nằm ở untouched", async () => {
    const sent: string[] = [];
    const outcome = await runBatch(items("a", "b", "c", "d"), async (item) => {
      sent.push(item.pairId);
      if (item.pairId === "b") throw httpError(404);
    });
    expect(sent).toEqual(["a", "b"]);
    expect(outcome.done).toEqual(["a"]);
    expect(outcome.stopped?.pairId).toBe("b");
    expect(outcome.stopped?.kind).toBe("not_found");
    expect(outcome.untouched).toEqual(["c", "d"]);
  });

  it("422 (trích dẫn không khớp) dừng hàng đợi, kind quote_mismatch", async () => {
    const detail = [{ loc: ["body", "definition_quote"], msg: "x", type: "value_error" }];
    const outcome = await runBatch(items("a", "b"), async (item) => {
      if (item.pairId === "a") throw httpError(422, detail);
    });
    expect(outcome.done).toEqual([]);
    expect(outcome.stopped?.kind).toBe("quote_mismatch");
    expect(outcome.untouched).toEqual(["b"]);
  });

  it("lỗi mạng hoặc máy chủ dừng giữa chừng, không mất lựa chọn còn lại", async () => {
    const outcome = await runBatch(items("a", "b", "c"), async (item) => {
      if (item.pairId === "b") throw new Error("mất mạng");
    });
    expect(outcome.done).toEqual(["a"]);
    expect(outcome.stopped?.kind).toBe("other");
    expect(outcome.untouched).toEqual(["c"]);
    // Lỗi tạm: cặp lỗi và cặp chưa gửi vẫn được chọn; chỉ cặp đã ghi bị bỏ chọn.
    expect(deselectAfter(outcome)).toEqual(["a"]);
  });

  it("deselectAfter bỏ chọn cả cặp 404 và 422 (gửi lại vẫn lỗi), giữ cặp lệch phiên bản", async () => {
    const outcome = await runBatch(items("a", "b", "c"), async (item) => {
      if (item.pairId === "a") throw httpError(409);
      if (item.pairId === "b") throw httpError(404);
    });
    expect(outcome.conflicts).toEqual(["a"]);
    expect(deselectAfter(outcome)).toEqual(["b"]);
  });

  it("cặp trùng trong hàng đợi chỉ gửi một lần (không idempotent)", async () => {
    const sent: string[] = [];
    await runBatch(items("a", "a", "b"), async (item) => {
      sent.push(item.pairId);
    });
    expect(sent).toEqual(["a", "b"]);
  });

  it("gửi kèm tên đã sửa và phiên bản của từng cặp", async () => {
    const seen: BatchItem[] = [];
    await runBatch(
      [
        { pairId: "a", version: 3, term: "Tên mới" },
        { pairId: "b", version: 1, term: null },
      ],
      async (item) => {
        seen.push(item);
      },
    );
    expect(seen).toEqual([
      { pairId: "a", version: 3, term: "Tên mới" },
      { pairId: "b", version: 1, term: null },
    ]);
  });

  it("cặp ghi xong rời danh sách proposed ngay", () => {
    const proposed = [pair("a"), pair("b"), pair("c")];
    expect(withoutSettled(proposed, new Set(["a", "c"])).map((item) => item.pair_id)).toEqual(["b"]);
  });
});

describe("điều hướng sau khi nhận", () => {
  const whole: BatchOutcome = { done: ["a"], conflicts: [], stopped: null, untouched: [] };

  it("chỉ sang ôn tập khi là lượt nhận, đã nhận ≥ 1 cặp và hàng đợi chạy trọn", () => {
    expect(shouldOpenPractice("accept", whole)).toBe(true);
    expect(shouldOpenPractice("reject", whole)).toBe(false);
    expect(shouldOpenPractice("accept", { ...whole, done: [] })).toBe(false);
    expect(shouldOpenPractice("accept", { ...whole, untouched: ["z"] })).toBe(false);
  });

  it("đích là tab Ôn tập của môn, lấy qua helper của route cũ (không tự viết URL)", () => {
    expect(practiceTarget("MAU101")).toBe(subjectHref("MAU101", "practice"));
  });

  it("câu tóm tắt nêu số đã ghi, số lệch và số chưa gửi; không lộ mã HTTP", () => {
    const text = batchSummary("accept", {
      done: ["a", "b"],
      conflicts: ["c"],
      stopped: {
        pairId: "d",
        kind: "not_found",
        error: new ApiError(404, classifyHttpError(404, { detail: "x" }, { context: "pair_confirm" })).info,
      },
      untouched: ["e"],
    });
    expect(text).toContain("Đã chấp nhận 2 khái niệm.");
    expect(text).toContain("1 khái niệm vừa đổi");
    expect(text).toContain("1 khái niệm chưa gửi vẫn đang được chọn.");
    expect(text).not.toMatch(/404|409|422|HTTP/);
    expect(batchSummary("reject", { done: [], conflicts: [], stopped: null, untouched: [] })).toBe(
      "Chưa từ chối được khái niệm nào.",
    );
  });
});

describe("job đề xuất của môn", () => {
  const raw = (id: string, status: string) => ({ job_id: id, status, batches_total: 3, batches_done: 1 });

  it("parse danh sách, bỏ phần tử sai dạng; thân không phải mảng ⇒ null", () => {
    const jobs = parseProposalJobList([raw("pjob_1", "running"), { status: "running" }, 5, raw("pjob_2", "succeeded")]);
    expect(jobs?.map((job) => job.jobId)).toEqual(["pjob_1", "pjob_2"]);
    expect(parseProposalJobList({ jobs: [] })).toBeNull();
    expect(parseProposalJobList([])).toEqual([]);
  });

  it("job mới nhất là phần tử đầu (backend trả mới nhất trước); danh sách rỗng ⇒ null", () => {
    const jobs = parseProposalJobList([raw("pjob_new", "running"), raw("pjob_old", "succeeded")]) ?? [];
    expect(latestJob(jobs)?.jobId).toBe("pjob_new");
    expect(latestJob([])).toBeNull();
  });

  it("lối vào tạo job chỉ khi khả năng báo khả dụng và không có job đang chạy", () => {
    const running = parseProposalJobList([raw("pjob_1", "running")])?.[0] ?? null;
    const done = parseProposalJobList([raw("pjob_1", "succeeded")])?.[0] ?? null;
    expect(canStartJob("available", null)).toBe(true);
    expect(canStartJob("available", done)).toBe(true);
    expect(canStartJob("available", running)).toBe(false);
    expect(canStartJob("unavailable", null)).toBe(false);
    expect(canStartJob("unknown", null)).toBe(false);
    expect(LLM_NEEDED_VI).toBe("Cần cấu hình LLM để tự đề xuất");
  });
});
