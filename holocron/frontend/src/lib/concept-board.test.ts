import { describe, expect, it } from "vitest";
import type { ConceptPair, ConceptPairStatus } from "@/types/holocron";
import {
  acceptToast,
  type BulkCandidate,
  type BulkEvent,
  bulkCount,
  bulkMessageOf,
  bulkReducer,
  CONCEPT_CONFLICT_VI,
  conceptErrorText,
  conceptsScreen,
  conceptView,
  focusAfterRemoval,
  groupPairs,
  IDLE_BULK,
  newlyConfirmed,
  screenErrorText,
  sendQueued,
  visibleConceptEntrances,
} from "./concept-board";
import { ApiError, classifyHttpError, classifyNetworkError } from "./errors";
import type { ChunkView } from "./vault";

function pair(id: string, status: ConceptPairStatus, overrides: Partial<ConceptPair> = {}): ConceptPair {
  return {
    pair_id: id,
    chunk_id: `chk_${id}`,
    subject_code: "MAU101",
    chapter_code: "ch2",
    term: `Thuật ngữ ${id}`,
    definition_quote: "Đoạn định nghĩa nguyên văn.",
    term_spans: [],
    status,
    proposed_by: "llm",
    mapping_confidence: null,
    version: 3,
    created_at: "",
    confirmed_at: null,
    ...overrides,
  };
}

function chunk(chunkId: string): ChunkView {
  return {
    chunkId,
    docId: "doc_0000000000000001",
    subjectCode: "MAU101",
    chapterCode: "ch2",
    pageNumber: 14,
    pageEnd: null,
    rawText: "Đoạn định nghĩa nguyên văn.",
    verbatimQuote: "Đoạn định nghĩa nguyên văn.",
    category: "THEORY",
    topicKeys: [],
    labelStatus: "confirmed",
    version: 1,
  };
}

const conflictError = () => classifyHttpError(409, { detail: "version" }, { versioned: true, context: "pair_confirm" });

describe("groupPairs và newlyConfirmed", () => {
  it("gom theo trạng thái, giữ thứ tự backend", () => {
    const list = [pair("a", "confirmed"), pair("b", "proposed"), pair("c", "rejected"), pair("d", "proposed")];
    const groups = groupPairs(list);
    expect(groups.proposed.map((item) => item.pair_id)).toEqual(["b", "d"]);
    expect(groups.confirmed.map((item) => item.pair_id)).toEqual(["a"]);
    expect(groups.rejected.map((item) => item.pair_id)).toEqual(["c"]);
  });

  it("chỉ khái niệm mới thành confirmed; lần tải đầu không có", () => {
    const before = [pair("a", "confirmed"), pair("b", "proposed")];
    const after = [pair("a", "confirmed"), pair("b", "confirmed"), pair("c", "confirmed", { proposed_by: "user" })];
    expect([...newlyConfirmed(before, after)]).toEqual(["b", "c"]);
    expect(newlyConfirmed(null, after).size).toBe(0);
  });
});

describe("conceptsScreen", () => {
  it("bốn trạng thái của tab", () => {
    expect(conceptsScreen("loading", 0, "loading", 0)).toBe("loading");
    expect(conceptsScreen("error", 0, "complete", 3)).toBe("error");
    expect(conceptsScreen("ready", 0, "loading", 0)).toBe("loading");
    expect(conceptsScreen("ready", 0, "complete", 0)).toBe("empty");
    expect(conceptsScreen("ready", 0, "complete", 2)).toBe("ready");
    expect(conceptsScreen("ready", 2, "loading", 0)).toBe("ready");
    expect(conceptsScreen("ready", 0, "error", 0)).toBe("ready");
  });
});

describe("conceptView: cùng một luật I3 cho mọi nhóm", () => {
  const map = new Map([["chk_a", chunk("chk_a")]]);

  it("tra được đoạn: có trang trong chú thích, nút bật", () => {
    const view = conceptView(pair("a", "proposed"), "complete", map);
    expect(view.chunk?.chunkId).toBe("chk_a");
    expect(view.caption).toBe("Chương 2 · tr. 14");
    expect(view.unverified).toBeNull();
    expect(view.actions.confirm).toEqual({ shown: true, enabled: true });
    expect(view.sourceLabel).toBe("Holocron đề xuất");
  });

  it("không tra được đoạn ở cả ba nhóm: không có số trang, có câu chưa kiểm chứng, nút khoá kèm lý do", () => {
    const missingMaps: [Parameters<typeof conceptView>[1], ReadonlyMap<string, ChunkView>][] = [
      ["complete", new Map()],
      ["partial", new Map()],
      ["error", new Map()],
      ["loading", new Map()],
    ];
    for (const [status, emptyMap] of missingMaps) {
      for (const pairStatus of ["proposed", "confirmed", "rejected"] satisfies ConceptPairStatus[]) {
        const view = conceptView(pair("x", pairStatus, { proposed_by: "user" }), status, emptyMap);
        expect(view.chunk).toBeNull();
        expect(view.caption).toBe("Chương 2");
        expect(view.caption).not.toContain("tr.");
        expect(view.unverified).toContain("chưa kiểm chứng được trang");
        expect(view.sourceLabel).toBe("Do bạn tạo");
        const gate = pairStatus === "confirmed" ? view.actions.edit : view.actions.confirm;
        expect(gate).toMatchObject({ shown: true, enabled: false });
        if (gate.shown && !gate.enabled) expect(gate.reason).not.toBe("");
        if (pairStatus !== "rejected") expect(view.actions.reject).toEqual({ shown: true, enabled: true });
      }
    }
  });
});

describe("lối vào tạo khái niệm", () => {
  it("đề xuất tự động chưa có ⇒ không có lối vào đó, chỉ còn tự tạo", () => {
    const entrances = visibleConceptEntrances({ autoProposalAvailable: false });
    expect(entrances).toEqual(["manual_create"]);
    expect(entrances).not.toContain("auto_proposal");
  });

  it("đề xuất tự động có sẵn ⇒ có lối vào, tự tạo vẫn còn", () => {
    expect(visibleConceptEntrances({ autoProposalAvailable: true })).toEqual(["auto_proposal", "manual_create"]);
  });
});

describe("focus và câu chữ", () => {
  it("focus thẻ kế tiếp; thẻ cuối hoặc thẻ lạ ⇒ null (tiêu đề khối)", () => {
    expect(focusAfterRemoval(["a", "b", "c"], "a")).toBe("b");
    expect(focusAfterRemoval(["a", "b", "c"], "c")).toBeNull();
    expect(focusAfterRemoval(["a"], "z")).toBeNull();
  });

  it("thông báo chỉ nói số sao khi bầu trời thực sự sáng thêm", () => {
    expect(acceptToast(3, 2)).toBe("Đã nhận 3 khái niệm, 2 ngôi sao sáng lên");
    expect(acceptToast(3, 0)).toBe("Đã nhận 3 khái niệm");
  });

  it("lỗi kết nối, máy chủ, lỗi lạ: câu không lộ từ kỹ thuật; lỗi theo luồng giữ thông điệp", () => {
    for (const error of [classifyNetworkError("http://127.0.0.1:8000"), classifyHttpError(500, null)]) {
      const text = screenErrorText(error);
      expect(text).not.toMatch(/\b(backend|LLM|VLM|OCR|chunk|vault|pair|blueprint)\b/i);
      expect(text).toContain("Thử lại");
    }
    const missing = classifyHttpError(404, { detail: "x" }, { context: "pair_confirm" });
    expect(screenErrorText(missing)).toBe(missing.message);
    expect(conceptErrorText(conflictError())).toBe(CONCEPT_CONFLICT_VI);
  });
});

describe("Nhận tất cả", () => {
  function candidates(...items: [string, boolean][]): BulkCandidate[] {
    return items.map(([pairId, allowed]) => ({
      pairId,
      version: 3,
      allowed,
      reason: allowed ? null : "Chưa tải hết đoạn của môn.",
    }));
  }

  async function run(
    list: BulkCandidate[],
    send: (pairId: string, version: number) => Promise<unknown>,
  ): Promise<{ state: ReturnType<typeof bulkReducer>; events: BulkEvent[] }> {
    let state = bulkReducer(IDLE_BULK, { type: "start", candidates: list });
    const events: BulkEvent[] = [];
    await sendQueued(state, send, (event) => {
      events.push(event);
      state = bulkReducer(state, event);
    });
    return { state, events };
  }

  it("lỗi giữa chừng không dừng hàng; 409 là vừa đổi ở nơi khác; đếm đúng số đã nhận", async () => {
    const sent: string[] = [];
    const { state } = await run(candidates(["a", true], ["b", true], ["c", true], ["d", true]), (pairId) => {
      sent.push(pairId);
      if (pairId === "b") return Promise.reject(new ApiError(null, classifyNetworkError("")));
      if (pairId === "c") return Promise.reject(new ApiError(409, conflictError()));
      return Promise.resolve();
    });
    expect(sent).toEqual(["a", "b", "c", "d"]);
    expect(state.running).toBe(false);
    expect(bulkCount(state, "accepted")).toBe(2);
    expect(bulkCount(state, "failed")).toBe(1);
    expect(bulkCount(state, "conflict")).toBe(1);
    expect(bulkMessageOf(state, "c")).toBe(CONCEPT_CONFLICT_VI);
    expect(bulkMessageOf(state, "b")).not.toMatch(/backend/i);
    expect(bulkMessageOf(state, "a")).toBeNull();
  });

  it("khái niệm khoá bị bỏ qua kèm lý do, không gửi", async () => {
    const sent: string[] = [];
    const { state } = await run(candidates(["a", false], ["b", true]), (pairId) => {
      sent.push(pairId);
      return Promise.resolve();
    });
    expect(sent).toEqual(["b"]);
    expect(state.items.find((item) => item.pairId === "a")).toMatchObject({
      status: "skipped",
      message: "Chưa tải hết đoạn của môn.",
    });
  });

  it("danh sách rỗng hoặc toàn khái niệm khoá: không chạy", () => {
    expect(bulkReducer(IDLE_BULK, { type: "start", candidates: [] })).toEqual({ running: false, items: [] });
    expect(bulkReducer(IDLE_BULK, { type: "start", candidates: candidates(["a", false]) }).running).toBe(false);
  });

  it("chặn bấm lặp khi đang gửi; một khái niệm có hai lần chỉ gửi một lần", async () => {
    const started = bulkReducer(IDLE_BULK, { type: "start", candidates: candidates(["a", true], ["a", true]) });
    expect(started.items).toHaveLength(1);
    expect(bulkReducer(started, { type: "start", candidates: candidates(["z", true]) })).toBe(started);
    const sent: string[] = [];
    await sendQueued(
      started,
      (pairId) => {
        sent.push(pairId);
        return Promise.resolve();
      },
      () => undefined,
    );
    expect(sent).toEqual(["a"]);
  });

  it("gửi kèm phiên bản backend trả và bỏ câu lỗi khi người dùng thử lại một thẻ", async () => {
    const versions: number[] = [];
    const { state } = await run(candidates(["a", true]), (_pairId, version) => {
      versions.push(version);
      return Promise.reject(new ApiError(null, classifyNetworkError("")));
    });
    expect(versions).toEqual([3]);
    expect(bulkReducer(state, { type: "dismiss", pairId: "a" }).items).toEqual([]);
  });
});
