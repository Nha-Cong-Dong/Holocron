import { describe, expect, it } from "vitest";
import type { ConceptPair, ConceptPairStatus } from "@/types/holocron";
import {
  ACCEPT_ALL_BLOCKED_VI,
  ACCEPT_ALL_BUSY_VI,
  ACCEPT_ALL_LOADING_VI,
  ACCEPT_ALL_STALE_RETRY_VI,
  ACCEPT_ALL_STALE_VI,
  acceptAllGate,
  acceptToast,
  type BulkCandidate,
  type BulkEvent,
  bulkCount,
  bulkMessageOf,
  bulkReducer,
  type ChangeNote,
  CONCEPT_CONFLICT_VI,
  changeEffects,
  clockLabel,
  conceptErrorText,
  conceptsScreen,
  conceptView,
  failedReloadEffects,
  focusAfterRemoval,
  groupPairs,
  IDLE_BULK,
  mergeChange,
  NO_STALE,
  newlyConfirmed,
  nextReloadNote,
  RELOAD_FAILED_VI,
  reloadedFocus,
  reloadNoteText,
  renameKeyAction,
  SOURCE_LOADING_REASON_VI,
  SOURCE_LOADING_VI,
  STALE_REASON_RETRY_VI,
  STALE_REASON_VI,
  type StaleLocks,
  screenErrorText,
  sendQueued,
  staleReason,
  staleReducer,
  staleView,
  visibleConceptEntrances,
  withoutIds,
  withStale,
} from "./concept-board";
import { ApiError, classifyHttpError, classifyNetworkError } from "./errors";
import type { ActionGate } from "./pairs";
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

describe("đoạn văn đang tải không bị coi là lỗi", () => {
  it("thẻ ở mọi nhóm: câu 'đang tải', lý do khoá 'đang tải', không dùng câu 'chưa tải được'", () => {
    for (const pairStatus of ["proposed", "confirmed", "rejected"] satisfies ConceptPairStatus[]) {
      const view = conceptView(pair("x", pairStatus), "loading", new Map());
      expect(view.sourceLoading).toBe(true);
      expect(view.chunk).toBeNull();
      expect(view.unverified).toBe(SOURCE_LOADING_VI);
      const gate = pairStatus === "confirmed" ? view.actions.edit : view.actions.confirm;
      expect(gate).toEqual({ shown: true, enabled: false, reason: SOURCE_LOADING_REASON_VI });
      if (pairStatus === "proposed") {
        expect(view.actions.edit).toEqual({ shown: true, enabled: false, reason: SOURCE_LOADING_REASON_VI });
      }
      expect(JSON.stringify(view.actions)).not.toContain("Chưa tải được");
      if (pairStatus !== "rejected") expect(view.actions.reject).toEqual({ shown: true, enabled: true });
      if (pairStatus === "rejected") expect(view.actions.edit).toEqual({ shown: false });
    }
  });

  it("đoạn văn tải lỗi vẫn dùng câu 'chưa tải được'; tải xong mà tra được thì không khoá", () => {
    const failed = conceptView(pair("x", "proposed"), "error", new Map());
    expect(failed.sourceLoading).toBe(false);
    expect(failed.unverified).toBe("Chưa tải được các đoạn của môn — chưa kiểm chứng được trang");
    const found = conceptView(pair("a", "proposed"), "partial", new Map([["chk_a", chunk("chk_a")]]));
    expect(found.sourceLoading).toBe(false);
    expect(found.unverified).toBeNull();
    expect(found.actions.confirm).toEqual({ shown: true, enabled: true });
  });
});

describe("Nhận tất cả: khoá kèm lý do khi mọi thẻ khoá", () => {
  const map = new Map([["chk_a", chunk("chk_a")]]);
  const ready = conceptView(pair("a", "proposed"), "complete", map);
  const missing = conceptView(pair("b", "proposed"), "complete", map);
  const loading = conceptView(pair("c", "proposed"), "loading", new Map());

  it("có ít nhất một thẻ rảnh nhận được ⇒ bấm được", () => {
    expect(
      acceptAllGate([
        { view: missing, busy: false },
        { view: ready, busy: false },
      ]),
    ).toEqual({ enabled: true });
  });

  it("mọi thẻ khoá: đang tải ⇒ lý do đang tải; không thì lý do chưa kiểm chứng", () => {
    expect(acceptAllGate([{ view: loading, busy: false }])).toEqual({ enabled: false, reason: ACCEPT_ALL_LOADING_VI });
    expect(acceptAllGate([{ view: missing, busy: false }])).toEqual({ enabled: false, reason: ACCEPT_ALL_BLOCKED_VI });
  });

  it("thẻ nhận được nhưng đang bận không tính; mọi thẻ bận ⇒ chờ gửi xong", () => {
    expect(
      acceptAllGate([
        { view: ready, busy: true },
        { view: missing, busy: false },
      ]),
    ).toEqual({
      enabled: false,
      reason: ACCEPT_ALL_BLOCKED_VI,
    });
    expect(acceptAllGate([{ view: ready, busy: true }])).toEqual({ enabled: false, reason: ACCEPT_ALL_BUSY_VI });
  });
});

describe("câu lỗi cố định theo loại", () => {
  const base = classifyNetworkError("http://127.0.0.1:8000");

  it("lỗi kết nối nói Holocron chưa chạy hoặc đã tắt", () => {
    expect(screenErrorText(base)).toBe("Holocron chưa chạy xong hoặc đã tắt. Mở lại Holocron rồi bấm Thử lại.");
  });

  it("lỗi máy chủ (500) có câu riêng, không nói chưa chạy hay đã tắt", () => {
    const text = screenErrorText(classifyHttpError(500, null));
    expect(text).toBe("Holocron gặp trục trặc khi xử lý yêu cầu này. Bấm Thử lại; nếu vẫn chưa được, mở lại Holocron.");
    expect(text).not.toMatch(/đã tắt|chưa chạy/);
  });

  it("kho bận và lỗi lạ: câu cố định đúng", () => {
    expect(screenErrorText({ ...base, kind: "busy" })).toBe(
      "Thư mục dữ liệu của Holocron đang bận. Đợi vài giây rồi bấm Thử lại.",
    );
    expect(screenErrorText({ ...base, kind: "unknown" })).toBe(
      "Holocron trả lời không như mong đợi. Bấm Thử lại; nếu vẫn lỗi, mở lại Holocron.",
    );
  });
});

describe("phím trong khung sửa tên", () => {
  it("Esc ở ô nhập hay ở nút đều huỷ; Enter chỉ lưu ở ô nhập; phím khác không làm gì", () => {
    expect(renameKeyAction("Escape", true)).toBe("cancel");
    expect(renameKeyAction("Escape", false)).toBe("cancel");
    expect(renameKeyAction("Enter", true)).toBe("save");
    expect(renameKeyAction("Enter", false)).toBeNull();
    expect(renameKeyAction("a", true)).toBeNull();
  });
});

describe("hai thao tác gần nhau không mất toast, focus, tải lại bầu trời", () => {
  const acceptA: ChangeNote = { accepted: 1, messages: [], focus: { kind: "concept", id: "b" } };
  const acceptB: ChangeNote = { accepted: 1, messages: [], focus: { kind: "proposals" } };
  const rename: ChangeNote = { accepted: 0, messages: ["Đã đổi tên thành “X”"], focus: null };

  it("cộng dồn số đã nhận, giữ thông báo theo thứ tự, focus theo thao tác sau cùng có focus", () => {
    const merged = mergeChange(mergeChange(mergeChange(null, acceptA), rename), acceptB);
    expect(merged).toEqual({ accepted: 2, messages: ["Đã đổi tên thành “X”"], focus: { kind: "proposals" } });
    expect(mergeChange(acceptA, rename)?.focus).toEqual({ kind: "concept", id: "b" });
  });

  it("lượt tải lại không kèm thao tác (lỗi 409, Thử lại) không xoá việc đang chờ", () => {
    expect(mergeChange(acceptA, null)).toBe(acceptA);
    expect(mergeChange(null, null)).toBeNull();
  });

  it("phát ở lượt mới nhất: toast gộp kèm số sao, rồi thông báo khác, focus, tải lại bầu trời", () => {
    const merged = mergeChange(mergeChange(acceptA, rename), acceptB);
    expect(changeEffects(merged, 2)).toEqual({
      toasts: ["Đã nhận 2 khái niệm, 2 ngôi sao sáng lên", "Đã đổi tên thành “X”"],
      focus: { kind: "proposals" },
      reloadSky: true,
    });
    expect(changeEffects(acceptA, 0).toasts).toEqual(["Đã nhận 1 khái niệm"]);
  });

  it("tạo khái niệm (không nhận, không thông báo) vẫn tải lại bầu trời; không có việc ⇒ không phát gì", () => {
    expect(changeEffects({ accepted: 0, messages: [], focus: null }, 3)).toEqual({
      toasts: [],
      focus: null,
      reloadSky: true,
    });
    expect(changeEffects(null, 3)).toEqual({ toasts: [], focus: null, reloadSky: false });
  });
});

describe("Nhận tất cả: trạng thái từng khái niệm", () => {
  it("lỗi mạng ⇒ failed; 409 ⇒ conflict; thành công ⇒ accepted", async () => {
    let state = bulkReducer(IDLE_BULK, {
      type: "start",
      candidates: ["a", "b", "c"].map((pairId) => ({ pairId, version: 3, allowed: true, reason: null })),
    });
    await sendQueued(
      state,
      (pairId) => {
        if (pairId === "b") return Promise.reject(new ApiError(null, classifyNetworkError("")));
        if (pairId === "c") return Promise.reject(new ApiError(409, conflictError()));
        return Promise.resolve();
      },
      (event) => {
        state = bulkReducer(state, event);
      },
    );
    expect(state.items.map((item) => [item.pairId, item.status])).toEqual([
      ["a", "accepted"],
      ["b", "failed"],
      ["c", "conflict"],
    ]);
  });
});

describe("conceptsScreen: danh sách chưa về thì luôn đang tải", () => {
  it("danh sách đang tải ⇒ đang tải, dù đoạn văn đã tải đủ (có hay không có đoạn)", () => {
    expect(conceptsScreen("loading", 3, "complete", 5)).toBe("loading");
    expect(conceptsScreen("loading", 0, "complete", 0)).toBe("loading");
  });
});

describe("Nhận tất cả: một thẻ đang tải đoạn là đủ để nói 'đang tải'", () => {
  it("hai thẻ rảnh đều khoá, chỉ một thẻ đang tải đoạn ⇒ lý do đang tải", () => {
    const missing = conceptView(pair("b", "proposed"), "complete", new Map());
    const loading = conceptView(pair("c", "proposed"), "loading", new Map());
    expect(
      acceptAllGate([
        { view: missing, busy: false },
        { view: loading, busy: false },
      ]),
    ).toEqual({ enabled: false, reason: ACCEPT_ALL_LOADING_VI });
  });
});

describe("mergeChange: thông báo theo thứ tự thao tác", () => {
  it("thông báo của việc đang chờ đứng trước thông báo của thao tác mới", () => {
    const first: ChangeNote = { accepted: 0, messages: ["a"], focus: null };
    const second: ChangeNote = { accepted: 0, messages: ["b"], focus: null };
    expect(mergeChange(first, second)?.messages).toEqual(["a", "b"]);
  });
});

describe("Nhận tất cả: hàng gửi", () => {
  it("nhận thành công xoá câu lỗi cũ của khái niệm", () => {
    const state = {
      running: true,
      items: [{ pairId: "a", version: 3, status: "sending" as const, message: "Câu lỗi cũ." }],
    };
    const next = bulkReducer(state, { type: "accepted", pairId: "a" });
    expect(next.items[0]).toEqual({ pairId: "a", version: 3, status: "accepted", message: null });
    expect(bulkMessageOf(next, "a")).toBeNull();
  });

  it("chỉ gửi khái niệm đang chờ gửi (queued), bỏ qua mọi trạng thái khác", async () => {
    const state = {
      running: true,
      items: (["queued", "skipped", "accepted", "conflict", "failed", "sending", "queued"] as const).map(
        (status, index) => ({ pairId: `p${index}`, version: index, status, message: null }),
      ),
    };
    const sent: [string, number][] = [];
    const events: BulkEvent[] = [];
    await sendQueued(
      state,
      (pairId, version) => {
        sent.push([pairId, version]);
        return Promise.resolve();
      },
      (event) => events.push(event),
    );
    expect(sent).toEqual([
      ["p0", 0],
      ["p6", 6],
    ]);
    expect(events.filter((event) => event.type === "sending")).toEqual([
      { type: "sending", pairId: "p0" },
      { type: "sending", pairId: "p6" },
    ]);
  });
});

describe("thao tác thành công mà lượt tải lại lỗi", () => {
  const accept: ChangeNote = { accepted: 1, messages: [], focus: { kind: "concept", id: "b" } };

  it("thẻ đã rời đi hiện lại (không lặp), focus về tiêu đề khối Đề xuất, vẫn báo toast không kèm số sao", () => {
    expect(failedReloadEffects(accept, ["a", "a", "c"])).toEqual({
      toasts: ["Đã nhận 1 khái niệm"],
      focus: { kind: "proposals" },
      reloadSky: true,
      reveal: ["a", "c"],
    });
  });

  it("không có thẻ nào rời đi (thao tác ở cột Đã nhận) ⇒ không dời focus", () => {
    const rename: ChangeNote = { accepted: 0, messages: ["Đã đổi tên thành “X”"], focus: { kind: "accepted" } };
    expect(failedReloadEffects(rename, [])).toEqual({
      toasts: ["Đã đổi tên thành “X”"],
      focus: null,
      reloadSky: true,
      reveal: [],
    });
  });

  it("không có việc chờ (Thử lại, 409) ⇒ không phát gì", () => {
    expect(failedReloadEffects(null, [])).toEqual({ toasts: [], focus: null, reloadSky: false, reveal: [] });
  });

  it("withoutIds bỏ đúng các thẻ liên quan, giữ thẻ khác đang rời đi", () => {
    const leaving = new Map([
      ["a", "accept"],
      ["b", "reject"],
      ["c", "accept"],
    ]);
    expect([...withoutIds(leaving, ["a", "c", "z"])]).toEqual([["b", "reject"]]);
    expect(leaving.size).toBe(3);
    expect(withoutIds(leaving, ["z"])).toBe(leaving);
    expect(withoutIds(leaving, [])).toBe(leaving);
  });
});

describe("conceptsScreen: môn có đúng một đoạn văn", () => {
  it("chưa có khái niệm, đã tải đủ một đoạn ⇒ thành công (tự tạo được), không phải rỗng", () => {
    expect(conceptsScreen("ready", 0, "complete", 1)).toBe("ready");
  });
});

describe("dòng báo tải lại lỗi và nút Thử lại", () => {
  const failure = classifyHttpError(500, null);

  it("giờ:phút hai chữ số mỗi phần", () => {
    expect(clockLabel(new Date(2026, 9, 29, 9, 5))).toBe("09:05");
    expect(clockLabel(new Date(2026, 9, 29, 14, 30))).toBe("14:30");
  });

  it("lần lỗi đầu: câu chung + cách sửa; lỗi sau khi bấm Thử lại thêm 'Lần thử lúc HH:MM.'", () => {
    const base = `${RELOAD_FAILED_VI} ${screenErrorText(failure)}`;
    expect(base).toBe(
      "Chưa cập nhật được danh sách khái niệm. Holocron gặp trục trặc khi xử lý yêu cầu này. Bấm Thử lại; nếu vẫn chưa được, mở lại Holocron.",
    );
    expect(reloadNoteText(failure, null)).toBe(base);
    expect(reloadNoteText(failure, new Date(2026, 9, 29, 8, 7))).toBe(`${base} Lần thử lúc 08:07.`);
  });

  it("mỗi lần lỗi có key mới kể cả khi câu trùng câu trước (vùng alert dựng lại để đọc lại)", () => {
    const first = nextReloadNote(null, "A");
    expect(first).toEqual({ key: 1, text: "A" });
    const second = nextReloadNote(first, "A");
    expect(second).toEqual({ key: 2, text: "A" });
    expect(nextReloadNote(second, "B")).toEqual({ key: 3, text: "B" });
  });

  it("tải thành công sau khi bấm Thử lại ⇒ focus tiêu đề khối; thao tác chờ có focus riêng thì giữ focus đó", () => {
    expect(reloadedFocus(null, true)).toEqual({ kind: "proposals" });
    expect(reloadedFocus(null, false)).toBeNull();
    expect(reloadedFocus({ kind: "accepted" }, true)).toEqual({ kind: "accepted" });
    expect(reloadedFocus({ kind: "concept", id: "b" }, false)).toEqual({ kind: "concept", id: "b" });
  });
});

describe("thẻ đã ghi mà danh sách chưa đọc lại được", () => {
  const ready = conceptView(pair("a", "proposed"), "complete", new Map([["chk_a", chunk("chk_a")]]));

  it("khoá Nhận, Bỏ, Sửa kèm lý do 'Đã ghi, đang cập nhật danh sách'", () => {
    expect(STALE_REASON_VI.startsWith("Đã ghi, đang cập nhật danh sách")).toBe(true);
    expect(STALE_REASON_VI).not.toContain("Thử lại");
    const locked = staleView(ready);
    const gate = { shown: true, enabled: false, reason: STALE_REASON_VI };
    expect(locked.actions).toEqual({ edit: gate, confirm: gate, reject: gate });
    expect(locked.chunk).toBe(ready.chunk);
    expect(locked.caption).toBe(ready.caption);
  });

  it("nút đang ẩn vẫn ẩn", () => {
    const hiddenGate: ActionGate = { shown: false };
    const hidden = { ...ready, actions: { ...ready.actions, edit: hiddenGate } };
    expect(staleView(hidden).actions.edit).toEqual({ shown: false });
  });

  it("tập thẻ chờ cộng dồn qua các lần lỗi, không lặp; không đổi thì trả đúng tập cũ", () => {
    const first = withStale(new Set(), ["a"]);
    expect([...first]).toEqual(["a"]);
    const second = withStale(first, ["a", "b"]);
    expect([...second]).toEqual(["a", "b"]);
    expect(withStale(second, ["b"])).toBe(second);
    expect(withStale(second, [])).toBe(second);
  });

  it("Nhận tất cả: mọi thẻ còn lại đều chờ cập nhật ⇒ khoá kèm lý do chờ cập nhật", () => {
    const locked = staleView(ready);
    expect(acceptAllGate([{ view: locked, busy: false, stale: true }])).toEqual({
      enabled: false,
      reason: ACCEPT_ALL_STALE_VI,
    });
    expect(
      acceptAllGate([
        { view: locked, busy: false, stale: true },
        { view: ready, busy: false },
      ]),
    ).toEqual({
      enabled: true,
    });
  });
});

describe("câu lý do khoá theo dòng báo tải lỗi", () => {
  const ready = conceptView(pair("a", "proposed"), "complete", new Map([["chk_a", chunk("chk_a")]]));

  it("chưa có dòng báo ⇒ không nhắc Thử lại; có dòng báo ⇒ nhắc Thử lại", () => {
    expect(staleReason(false)).toBe(STALE_REASON_VI);
    expect(staleReason(true)).toBe(STALE_REASON_RETRY_VI);
    expect(STALE_REASON_RETRY_VI).toContain("Bấm Thử lại ở dòng báo phía trên.");
    expect(staleView(ready, true).actions.confirm).toEqual({
      shown: true,
      enabled: false,
      reason: STALE_REASON_RETRY_VI,
    });
    expect(staleView(ready, false).actions.confirm).toEqual({ shown: true, enabled: false, reason: STALE_REASON_VI });
  });

  it("Nhận tất cả: hai nhánh câu lý do", () => {
    const entries = [{ view: staleView(ready), busy: false, stale: true }];
    expect(acceptAllGate(entries)).toEqual({ enabled: false, reason: ACCEPT_ALL_STALE_VI });
    expect(acceptAllGate(entries, true)).toEqual({ enabled: false, reason: ACCEPT_ALL_STALE_RETRY_VI });
    expect(ACCEPT_ALL_STALE_VI).not.toContain("Thử lại");
  });

  it("thẻ thiếu đoạn nguồn không qua staleView mà đã ghi vẫn tính là chờ cập nhật", () => {
    const missing = conceptView(pair("a", "proposed"), "complete", new Map());
    expect(acceptAllGate([{ view: missing, busy: false, stale: true }])).toEqual({
      enabled: false,
      reason: ACCEPT_ALL_STALE_VI,
    });
    expect(acceptAllGate([{ view: missing, busy: false, stale: false }])).toEqual({
      enabled: false,
      reason: ACCEPT_ALL_BLOCKED_VI,
    });
  });
});

describe("khoá chờ: thẻ đã ghi và Nhận tất cả", () => {
  const map = new Map([["chk_a", chunk("chk_a")]]);
  const ready = conceptView(pair("a", "proposed"), "complete", map);
  const loading = conceptView(pair("c", "proposed"), "loading", new Map());

  it("thẻ chờ cập nhật cùng thẻ khoá vì đang tải đoạn ⇒ lý do đang tải, không phải chờ cập nhật", () => {
    expect(
      acceptAllGate([
        { view: staleView(ready), busy: false, stale: true },
        { view: loading, busy: false, stale: false },
      ]),
    ).toEqual({ enabled: false, reason: ACCEPT_ALL_LOADING_VI });
  });

  it("thêm khái niệm vào tập đang có giữ khái niệm cũ", () => {
    expect(withStale(new Set(["a"]), ["b"])).toEqual(new Set(["a", "b"]));
  });

  it("conceptView chưa khoá; staleView đánh dấu khoá chờ", () => {
    expect(ready.stale).toBe(false);
    expect(staleView(ready).stale).toBe(true);
  });
});

describe("khoá chờ ngay khi ghi thành công", () => {
  const ids = (locks: StaleLocks): string[] => [...locks.locked].sort();

  it("ghi xong ⇒ khoá ngay, trước mọi lượt tải lại", () => {
    const written = staleReducer(NO_STALE, { type: "written", ids: ["a"] });
    expect(ids(written)).toEqual(["a"]);
  });

  it("tải lại thành công sau khi đã gửi lượt tải lại của khái niệm ⇒ mở khoá", () => {
    let locks = staleReducer(NO_STALE, { type: "written", ids: ["a", "b"] });
    locks = staleReducer(locks, { type: "requested", ids: ["a", "b"] });
    locks = staleReducer(locks, { type: "reloaded" });
    expect(ids(locks)).toEqual([]);
  });

  it("tải lại lỗi không phát sự kiện: khái niệm vẫn khoá tới lượt tải lại thành công kế tiếp", () => {
    let locks = staleReducer(NO_STALE, { type: "written", ids: ["a"] });
    locks = staleReducer(locks, { type: "requested", ids: ["a"] });
    expect(ids(locks)).toEqual(["a"]);
    locks = staleReducer(locks, { type: "reloaded" });
    expect(ids(locks)).toEqual([]);
  });

  it("lượt tải lại gửi trước khi thẻ rời đi xong không mở khoá thẻ đó", () => {
    // "a" ghi xong và đã gửi lượt tải lại; "b" ghi xong nhưng còn đang rời đi (chưa gửi lượt tải lại của nó).
    let locks = staleReducer(NO_STALE, { type: "written", ids: ["a"] });
    locks = staleReducer(locks, { type: "requested", ids: ["a"] });
    locks = staleReducer(locks, { type: "written", ids: ["b"] });
    locks = staleReducer(locks, { type: "reloaded" });
    expect(ids(locks)).toEqual(["b"]);
    locks = staleReducer(locks, { type: "requested", ids: ["b"] });
    locks = staleReducer(locks, { type: "reloaded" });
    expect(ids(locks)).toEqual([]);
  });

  it("lượt tải lại không kèm khái niệm nào (ví dụ đọc lại sau 409) vẫn mở khoá khái niệm đã gửi lượt tải lại", () => {
    let locks = staleReducer(NO_STALE, { type: "written", ids: ["a"] });
    locks = staleReducer(locks, { type: "requested", ids: ["a"] });
    locks = staleReducer(locks, { type: "requested", ids: [] });
    locks = staleReducer(locks, { type: "reloaded" });
    expect(ids(locks)).toEqual([]);
  });

  it("ghi, gửi lượt tải lại, ghi lại cùng khái niệm, tải lại ⇒ vẫn khoá (lượt tải sau không bỏ khoá khái niệm đã ghi lại)", () => {
    let locks = staleReducer(NO_STALE, { type: "written", ids: ["a"] });
    locks = staleReducer(locks, { type: "requested", ids: ["a"] });
    locks = staleReducer(locks, { type: "written", ids: ["a"] });
    locks = staleReducer(locks, { type: "reloaded" });
    expect(ids(locks)).toEqual(["a"]);
  });

  it("lượt tải lại của khái niệm khác không gửi cho 'a' ⇒ tải lại mở khoá cả hai khi đã gửi đủ; thiếu thì giữ", () => {
    let locks = staleReducer(NO_STALE, { type: "written", ids: ["a"] });
    locks = staleReducer(locks, { type: "requested", ids: ["a", "c"] });
    locks = staleReducer(locks, { type: "reloaded" });
    expect(ids(locks)).toEqual([]);
    expect(staleReducer(NO_STALE, { type: "requested", ids: [] })).toBe(NO_STALE);
  });

  it("khái niệm ghi sau lượt gửi trước vẫn khoá qua hai lượt tải lại liên tiếp nếu chưa được gửi", () => {
    let locks = staleReducer(NO_STALE, { type: "written", ids: ["a"] });
    locks = staleReducer(locks, { type: "requested", ids: ["a"] });
    locks = staleReducer(locks, { type: "written", ids: ["b"] });
    locks = staleReducer(locks, { type: "reloaded" });
    locks = staleReducer(locks, { type: "reloaded" });
    expect(ids(locks)).toEqual(["b"]);
  });

  it("không đổi thì trả đúng trạng thái cũ", () => {
    const written = staleReducer(NO_STALE, { type: "written", ids: ["a"] });
    expect(staleReducer(written, { type: "written", ids: ["a"] })).toBe(written);
    expect(staleReducer(NO_STALE, { type: "requested", ids: ["a"] })).toBe(NO_STALE);
    expect(staleReducer(NO_STALE, { type: "reloaded" })).toBe(NO_STALE);
    expect(staleReducer(written, { type: "reloaded" })).toBe(written);
  });
});

describe("khoá chờ: từng nút và thứ tự kiểm của Nhận tất cả", () => {
  const ready = conceptView(pair("a", "proposed"), "complete", new Map([["chk_a", chunk("chk_a")]]));

  it("Sửa và Bỏ cũng nhắc Thử lại khi dòng báo tải lỗi đang hiện", () => {
    const retry = staleView(ready, true).actions;
    expect(retry.edit).toEqual({ shown: true, enabled: false, reason: STALE_REASON_RETRY_VI });
    expect(retry.reject).toEqual({ shown: true, enabled: false, reason: STALE_REASON_RETRY_VI });
  });

  it("mỗi nút giữ khoá của chính nó: Nhận ẩn vẫn ẩn, Bỏ hiện thì bị khoá", () => {
    const view = {
      ...ready,
      actions: {
        ...ready.actions,
        confirm: { shown: false } as ActionGate,
        reject: { shown: true, enabled: true } as ActionGate,
      },
    };
    const locked = staleView(view).actions;
    expect(locked.confirm).toEqual({ shown: false });
    expect(locked.reject).toEqual({ shown: true, enabled: false, reason: STALE_REASON_VI });
  });

  it("Nhận tất cả: thẻ chờ cập nhật nhưng thẻ kia đang bận ⇒ khoá vì chờ cập nhật, không phải vì bận", () => {
    expect(
      acceptAllGate([
        { view: staleView(ready), busy: false, stale: true },
        { view: ready, busy: true },
      ]),
    ).toEqual({ enabled: false, reason: ACCEPT_ALL_STALE_VI });
  });

  it("Nhận tất cả: thẻ nhận được thì bật dù đã đánh dấu chờ (kiểm nhận được trước)", () => {
    expect(acceptAllGate([{ view: ready, busy: false, stale: true }])).toEqual({ enabled: true });
  });
});
