/**
 * `request()` và `getRuntime()` với `fetch` giả: không gọi mạng. Kiểm phân loại lỗi ở ranh giới, gộp header token và
 * việc cờ mock chỉ bù field còn thiếu của `/v1/runtime` thay vì thay cả endpoint.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  INITIAL_PAGES,
  MOCK_DOCUMENT_SCENARIO,
  MOCK_IMPORT_DOC_ID,
  MOCK_IMPORT_SUBJECT,
} from "@/mocks/fixtures/documents";
import { RUNTIME_FIXTURES } from "@/mocks/fixtures/runtime";
import { resetMockDocumentSessions } from "@/mocks/store";

import { ApiError, createApi, LOCAL_EDITOR_ID, realApi, toUiError } from "./api";
import { DEFAULT_API_BASE, TOKEN_HEADER } from "./config";

interface FetchCall {
  url: string;
  init: RequestInit | undefined;
}

/** Thay `fetch` toàn cục bằng hàm trả `respond()` và ghi lại các lời gọi. */
function stubFetch(respond: () => Promise<Response>): FetchCall[] {
  const calls: FetchCall[] = [];
  vi.stubGlobal("fetch", (input: string, init?: RequestInit) => {
    calls.push({ url: input, init });
    return respond();
  });
  return calls;
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function textResponse(status: number, body: string): Response {
  return new Response(body, { status, headers: { "Content-Type": "text/html" } });
}

async function captureError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) return error;
    throw error;
  }
  throw new Error("lời gọi không ném lỗi");
}

function headersOf(call: FetchCall | undefined): Record<string, string> {
  const headers = call?.init?.headers;
  const result: Record<string, string> = {};
  new Headers(headers).forEach((value, key) => {
    result[key] = value;
  });
  return result;
}

const BACKEND_RUNTIME = { demo_mode: true, llm_configured: false, vlm_configured: false };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("request()", () => {
  it("fetch ném lỗi: lỗi mạng, status null, có nút thử lại", async () => {
    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    const error = await captureError(realApi.getItem("ing_1"));
    expect(error.status).toBeNull();
    expect(error.info.kind).toBe("network");
    expect(error.info.action).toBe("retry");
    expect(error.message).toContain(DEFAULT_API_BASE);
  });

  it("503 có thân JSON là kho bận; 503 thân không phải JSON là không tới được backend", async () => {
    stubFetch(() => Promise.resolve(jsonResponse(503, { detail: "Kho dữ liệu đang bận" })));
    expect((await captureError(realApi.getItem("ing_1"))).info.kind).toBe("busy");

    stubFetch(() => Promise.resolve(textResponse(503, "<html>Service Unavailable</html>")));
    const proxy = await captureError(realApi.getItem("ing_1"));
    expect(proxy.status).toBe(503);
    expect(proxy.info.kind).toBe("server");
  });

  it("500 thân không phải JSON là lỗi server, có nút thử lại", async () => {
    stubFetch(() => Promise.resolve(textResponse(500, "Internal Server Error")));
    const error = await captureError(realApi.getItem("ing_1"));
    expect(error.info.kind).toBe("server");
    expect(error.info.action).toBe("retry");
  });

  it("mã thành công nhưng thân không phải JSON là phản hồi không hợp lệ", async () => {
    stubFetch(() => Promise.resolve(textResponse(200, "<!doctype html><html></html>")));
    const error = await captureError(realApi.getItem("ing_1"));
    expect(error.status).toBe(200);
    expect(error.info.kind).toBe("server");
  });

  it("gộp header token với header riêng của request; header riêng không ghi đè được token", async () => {
    vi.stubGlobal("window", {
      __HOLOCRON_RUNTIME__: { apiBase: "http://127.0.0.1:9000/", token: "tok-123" },
      location: { search: "" },
    });
    const calls = stubFetch(() => Promise.resolve(jsonResponse(201, { batch_id: "b" })));
    await realApi.createTwinBatch("bp_1", { count: 2, base_seed: null }, "idem-1");
    expect(calls[0]?.url).toBe("http://127.0.0.1:9000/v1/blueprints/bp_1/twins");
    expect(headersOf(calls[0])).toEqual({
      "content-type": "application/json",
      "idempotency-key": "idem-1",
      [TOKEN_HEADER.toLowerCase()]: "tok-123",
    });
  });

  it("không có vỏ desktop thì không gửi header token", async () => {
    const calls = stubFetch(() => Promise.resolve(jsonResponse(200, { item_id: "ing_1" })));
    await realApi.getItem("ing 1/2");
    expect(calls[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/ingestion/items/ing%201%2F2`);
    expect(headersOf(calls[0])).toEqual({});
  });

  it("luồng gọi truyền ngữ cảnh: 422 khi tải đề có câu riêng", async () => {
    stubFetch(() =>
      Promise.resolve(jsonResponse(422, { detail: [{ loc: ["body", "quantum"], msg: "x", type: "x" }] })),
    );
    const file = new File(["x"], "de.png", { type: "image/png" });
    const error = await captureError(realApi.uploadDocument(file, "a.b.c", 30));
    expect(error.info.kind).toBe("validation");
    expect(error.info.message).toContain("quantum");
    expect(error.info.message).not.toMatch(/body|loc|422/);
  });
});

describe("getRuntime()", () => {
  it("không bật mock: trả đúng dữ liệu backend đã kiểm dạng", async () => {
    stubFetch(() => Promise.resolve(jsonResponse(200, BACKEND_RUNTIME)));
    const runtime = await realApi.getRuntime();
    expect(runtime).toEqual({ ...BACKEND_RUNTIME, mock_extractor: null, capabilities: null });
  });

  it("thân thiếu cờ bắt buộc là phản hồi không hợp lệ", async () => {
    stubFetch(() => Promise.resolve(jsonResponse(200, { demo_mode: true })));
    expect((await captureError(realApi.getRuntime())).info.kind).toBe("server");
  });

  it("bật mock: vẫn gọi backend thật và chỉ bù field còn thiếu", async () => {
    const calls = stubFetch(() => Promise.resolve(jsonResponse(200, BACKEND_RUNTIME)));
    const runtime = await createApi(true).getRuntime();
    expect(calls.map((c) => c.url)).toEqual([`${DEFAULT_API_BASE}/v1/runtime`]);
    expect(runtime.llm_configured).toBe(false);
    expect(runtime.capabilities?.map((c) => c.name)).toEqual(RUNTIME_FIXTURES.offline.capabilities?.map((c) => c.name));
    expect(runtime.mock_extractor).toBe(true);
  });

  it("bật mock: field backend đã trả được giữ, không bị fixture ghi đè", async () => {
    const own = [{ name: "ocr", available: false, reason: "ocr_engine_missing", detail_vi: "Cài Tesseract." }];
    stubFetch(() =>
      Promise.resolve(jsonResponse(200, { ...BACKEND_RUNTIME, capabilities: own, mock_extractor: false })),
    );
    const runtime = await createApi(true).getRuntime();
    expect(runtime.capabilities).toEqual(own);
    expect(runtime.mock_extractor).toBe(false);
  });

  it("bật mock mà backend không chạy: vẫn báo không kết nối được, không trả fixture", async () => {
    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    const error = await captureError(createApi(true).getRuntime());
    expect(error.info.kind).toBe("network");
    expect(toUiError(error).kind).toBe("network");
  });

  it("bật mock: endpoint backend chưa có lấy từ dữ liệu mẫu, không gọi fetch", async () => {
    const calls = stubFetch(() => Promise.reject(new TypeError("không được gọi")));
    const subjects = await createApi(true).listSubjects();
    expect(subjects.length).toBeGreaterThan(0);
    expect(calls).toEqual([]);
  });
});

describe("màn luyện tập ở ranh giới", () => {
  it("backend chưa có POST /v1/blueprints (404 JSON): câu riêng, không phải lỗi kết nối", async () => {
    stubFetch(() => Promise.resolve(jsonResponse(404, { detail: "Not Found" })));
    const error = await captureError(realApi.createBlueprint({ subject_code: "MAU101", theory_slots: [] }));
    expect(error.info.kind).toBe("not_found");
    expect(error.info.message).toContain("chưa hỗ trợ tạo bộ đề");
  });

  it("404 thân không phải JSON (proxy không tới backend) là không kết nối được", async () => {
    stubFetch(() => Promise.resolve(textResponse(404, "<html>Not Found</html>")));
    const error = await captureError(realApi.listSubjects());
    expect(error.info.kind).toBe("server");
  });

  it("danh sách môn sai dạng ở cấp ngoài là phản hồi không hợp lệ; phần tử hỏng bị bỏ", async () => {
    stubFetch(() => Promise.resolve(jsonResponse(200, { items: [] })));
    expect((await captureError(realApi.listSubjects())).info.kind).toBe("server");
    stubFetch(() => Promise.resolve(jsonResponse(200, [null, { subject_code: "x" }])));
    expect(await realApi.listSubjects()).toEqual([]);
  });

  it("sinh câu: 409 khi đề chưa duyệt không có nút gửi lại; thân lạ không làm sập", async () => {
    stubFetch(() => Promise.resolve(jsonResponse(409, { detail: "item chưa approved" })));
    const conflict = await captureError(realApi.generatePracticeBatch("bp_1", { count: 1 }, "k"));
    expect(conflict.info.kind).toBe("unapproved_source");
    expect(conflict.info.action).toBe("none");
    stubFetch(() => Promise.resolve(jsonResponse(201, { twins: "hỏng" })));
    expect((await captureError(realApi.generatePracticeBatch("bp_1", { count: 1 }, "k"))).info.kind).toBe("server");
  });

  it("chấm tự luận gửi answer_text và kiểm dạng kết quả", async () => {
    const calls = stubFetch(() => Promise.resolve(jsonResponse(200, { twin_id: "t", total_points: 0 })));
    const error = await captureError(realApi.gradeEssay("twin 1", "câu trả lời"));
    expect(error.info.kind).toBe("server");
    expect(calls[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/twins/twin%201/essay-grade`);
    expect(calls[0]?.init?.body).toBe(JSON.stringify({ answer_text: "câu trả lời" }));
  });

  it("bật mock: blueprint thật vẫn gọi backend để sinh câu; blueprint mẫu dùng batch mẫu", async () => {
    const calls = stubFetch(() => Promise.resolve(jsonResponse(404, { detail: "không có blueprint" })));
    const mocked = createApi(true);
    const error = await captureError(mocked.generatePracticeBatch("bp_that", { count: 1 }, null));
    expect(error.info.kind).toBe("not_found");
    expect(calls).toHaveLength(1);
    const blueprint = await mocked.createBlueprint({ subject_code: "MAU101", theory_slots: [] });
    const batch = await mocked.generatePracticeBatch(blueprint.blueprintId, { count: 1 }, null);
    expect(batch.questions.length).toBeGreaterThan(0);
    expect(calls).toHaveLength(1);
  });
});

// ------------------------------------------------------------------------- bộ nhập tài liệu và Vault (§6.5, §6.6)

const REAL_DOC_ID = "doc_ffffffffffffffff";

/** `DocumentImport` hợp lệ của một tài liệu thật (không phải mã tài liệu mẫu). */
function realDocumentBody(): unknown {
  const document = structuredClone(MOCK_DOCUMENT_SCENARIO.initial.document);
  document.meta.doc_id = REAL_DOC_ID;
  return document;
}

function realSummaryBody(): unknown {
  const summary = structuredClone(MOCK_DOCUMENT_SCENARIO.initial.summary);
  summary.meta.doc_id = REAL_DOC_ID;
  summary.meta.title = "Tài liệu thật";
  return summary;
}

function pageBody(): unknown {
  const page = structuredClone(INITIAL_PAGES[0]);
  return page === undefined ? null : { ...page, document_id: REAL_DOC_ID };
}

function jsonBody(call: FetchCall | undefined): unknown {
  const body = call?.init?.body;
  return typeof body === "string" ? JSON.parse(body) : undefined;
}

const PDF = new File(["%PDF-1.7"], "chuong-2.pdf", { type: "application/pdf" });

describe("bộ nhập tài liệu ở ranh giới", () => {
  it("importDocument gửi multipart đủ field (kể cả chapter_code, source_kind); 202 ⇒ created, 200 ⇒ đã có", async () => {
    const calls = stubFetch(() => Promise.resolve(jsonResponse(202, realDocumentBody())));
    const input = { file: PDF, subjectCode: "MAU101", sourceKind: "textbook", chapterCode: "ch2", title: "Chương 2" };
    const created = await realApi.importDocument(input);
    expect(created.created).toBe(true);
    expect(created.document.docId).toBe(REAL_DOC_ID);
    expect(calls[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/documents`);
    expect(calls[0]?.init?.method).toBe("POST");
    const form = calls[0]?.init?.body;
    expect(form).toBeInstanceOf(FormData);
    if (form instanceof FormData) {
      expect(form.get("subject_code")).toBe("MAU101");
      expect(form.get("source_kind")).toBe("textbook");
      expect(form.get("chapter_code")).toBe("ch2");
      expect(form.get("title")).toBe("Chương 2");
      expect(form.get("file")).toBeInstanceOf(File);
    }

    stubFetch(() => Promise.resolve(jsonResponse(200, realDocumentBody())));
    const existing = await realApi.importDocument({ ...input, title: null });
    expect(existing.created).toBe(false);
  });

  it("importDocument không gửi title khi bỏ trống; 409 dùng câu của luồng nhập tài liệu", async () => {
    const calls = stubFetch(() => Promise.resolve(jsonResponse(409, { detail: "đã thuộc môn ABC123" })));
    const input = { file: PDF, subjectCode: "MAU101", sourceKind: "textbook", chapterCode: "ch2", title: null };
    const error = await captureError(realApi.importDocument(input));
    expect(error.info.kind).toBe("duplicate");
    expect(error.info.message).toContain("môn khác");
    const form = calls[0]?.init?.body;
    expect(form).toBeInstanceOf(FormData);
    if (form instanceof FormData) expect(form.has("title")).toBe(false);
  });

  it("deleteDocument: 204 không đọc thân", async () => {
    const response = new Response(null, { status: 204 });
    const json = vi.spyOn(response, "json");
    const calls = stubFetch(() => Promise.resolve(response));
    await expect(realApi.deleteDocument("doc 1")).resolves.toBeUndefined();
    expect(calls[0]?.init?.method).toBe("DELETE");
    expect(calls[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/documents/doc%201`);
    expect(json).not.toHaveBeenCalled();
  });

  it("patchPageLines gửi editor_id, expected_version, edits, appended_lines", async () => {
    const calls = stubFetch(() => Promise.resolve(jsonResponse(200, pageBody())));
    await realApi.patchPageLines(REAL_DOC_ID, 2, 3, [{ line_index: 1, text: "MOV R0, #1" }], ["dòng thêm"]);
    expect(calls[0]?.init?.method).toBe("PATCH");
    expect(calls[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/documents/${REAL_DOC_ID}/pages/2/lines`);
    expect(jsonBody(calls[0])).toEqual({
      editor_id: LOCAL_EDITOR_ID,
      expected_version: 3,
      edits: [{ line_index: 1, text: "MOV R0, #1" }],
      appended_lines: ["dòng thêm"],
    });
  });

  it("confirmPage gửi editor_id và expected_version; 409 nhắc trang không có dòng có văn bản", async () => {
    const calls = stubFetch(() => Promise.resolve(jsonResponse(200, pageBody())));
    await realApi.confirmPage(REAL_DOC_ID, 1, 2);
    expect(calls[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/documents/${REAL_DOC_ID}/pages/1/confirm`);
    expect(jsonBody(calls[0])).toEqual({ editor_id: LOCAL_EDITOR_ID, expected_version: 2 });

    stubFetch(() => Promise.resolve(jsonResponse(409, { detail: "x" })));
    const error = await captureError(realApi.confirmPage(REAL_DOC_ID, 1, 2));
    expect(error.info.action).toBe("reload");
    expect(error.info.message).toContain("không có dòng nào có văn bản");
  });

  it("confirmDocument CHỈ gửi {editor_id}, không có expected_version (§6.5); 409 là còn trang đang đọc", async () => {
    const calls = stubFetch(() => Promise.resolve(jsonResponse(200, realDocumentBody())));
    await realApi.confirmDocument(REAL_DOC_ID);
    expect(calls[0]?.init?.method).toBe("POST");
    expect(calls[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/documents/${REAL_DOC_ID}/confirm`);
    const body = jsonBody(calls[0]);
    expect(body).toEqual({ editor_id: LOCAL_EDITOR_ID });
    expect(typeof body === "object" && body !== null && "expected_version" in body).toBe(false);

    stubFetch(() => Promise.resolve(jsonResponse(409, { detail: "còn trang pending" })));
    expect((await captureError(realApi.confirmDocument(REAL_DOC_ID))).info.message).toContain("còn trang đang đọc");
  });
});

describe("sửa nhãn chunk ở ranh giới", () => {
  const CHUNK_BODY = {
    chunk_id: "chk 1",
    doc_id: REAL_DOC_ID,
    subject_code: "MAU101",
    chapter_code: "ch2",
    page_number: 1,
    page_end: null,
    raw_text: "Văn bản đã xác nhận của trang một.",
    verbatim_quote: "Văn bản đã xác nhận",
    category: "THEORY",
    tags: [],
    topic_keys: [],
    linked_chunk_ids: [],
    token_count: 7,
    embedding_model: null,
    label_status: "confirmed",
    mapping_confidence: null,
    version: 5,
  };

  it("PATCH …/labels gửi editor_id kèm đúng body đã dựng (chỉ field đổi, topic_keys rỗng có chủ đích)", async () => {
    const calls = stubFetch(() => Promise.resolve(jsonResponse(200, CHUNK_BODY)));
    const chunk = await realApi.patchChunkLabels("chk 1", { expected_version: 4, topic_keys: [] });
    expect(chunk).toMatchObject({ chunkId: "chk 1", labelStatus: "confirmed", version: 5 });
    expect(calls[0]?.init?.method).toBe("PATCH");
    expect(calls[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/vault/chunks/chk%201/labels`);
    expect(jsonBody(calls[0])).toEqual({ editor_id: LOCAL_EDITOR_ID, expected_version: 4, topic_keys: [] });
  });

  it("404, 409 (tải lại bản mới nhất), 422 có câu riêng; thân trả về sai dạng là phản hồi không hợp lệ", async () => {
    const change = { expected_version: 1 };
    stubFetch(() => Promise.resolve(jsonResponse(404, { detail: "không tìm thấy chunk" })));
    expect((await captureError(realApi.patchChunkLabels("c", change))).info.kind).toBe("not_found");
    stubFetch(() => Promise.resolve(jsonResponse(409, { detail: "version lệch" })));
    const conflict = await captureError(realApi.patchChunkLabels("c", change));
    expect(conflict.info).toMatchObject({ kind: "version_conflict", action: "reload" });
    const invalid = { detail: [{ loc: ["body", "chapter_code"], msg: "x", type: "x" }] };
    stubFetch(() => Promise.resolve(jsonResponse(422, invalid)));
    expect((await captureError(realApi.patchChunkLabels("c", change))).info.kind).toBe("validation");
    stubFetch(() => Promise.resolve(jsonResponse(200, { chunk_id: "c" })));
    expect((await captureError(realApi.patchChunkLabels("c", change))).info.kind).toBe("server");
  });
});

describe("cặp khái niệm ở ranh giới (§6.6)", () => {
  const PAIR = {
    pair_id: "cp 1",
    chunk_id: "chk_1",
    subject_code: "MAU101",
    chapter_code: "ch1",
    term: "tiến trình",
    definition_quote: "Tiến trình là chương trình đang chạy.",
    term_spans: [[0, 10]],
    status: "confirmed",
    proposed_by: "user",
    mapping_confidence: null,
    version: 3,
    created_at: "2026-10-28T00:00:00Z",
    confirmed_at: "2026-10-28T00:00:00Z",
  };

  it("GET danh sách: query đúng, chỉ gửi bộ lọc có giá trị; giữ thứ tự backend, bỏ phần tử sai dạng", async () => {
    const calls = stubFetch(() => Promise.resolve(jsonResponse(200, [PAIR, { pair_id: "x" }])));
    const list = await realApi.listConceptPairs({ subjectCode: "MAU101", chapterCode: "ch2.1", status: "proposed" });
    expect(calls[0]?.url).toBe(
      `${DEFAULT_API_BASE}/v1/vault/concept-pairs?subject_code=MAU101&chapter_code=ch2.1&status=proposed`,
    );
    expect(calls[0]?.init?.method).toBe("GET");
    expect(list.pairs.map((pair) => pair.pair_id)).toEqual(["cp 1"]);
    expect(list.hasSkipped).toBe(true);
    await realApi.listConceptPairs({ subjectCode: "MAU101", chapterCode: null, status: null });
    expect(calls[1]?.url).toBe(`${DEFAULT_API_BASE}/v1/vault/concept-pairs?subject_code=MAU101`);
    stubFetch(() => Promise.resolve(jsonResponse(200, { pairs: [] })));
    const bad = await captureError(
      realApi.listConceptPairs({ subjectCode: "MAU101", chapterCode: null, status: null }),
    );
    expect(bad.info.kind).toBe("server");
  });

  it("POST tạo cặp: body đúng, gửi nguyên chuỗi đoạn định nghĩa", async () => {
    const calls = stubFetch(() => Promise.resolve(jsonResponse(201, PAIR)));
    const quote = "  Tiến trình là\r\nchương trình  ";
    const pair = await realApi.createConceptPair("chk_1", "tiến trình", quote);
    expect(pair.pair_id).toBe("cp 1");
    expect(calls[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/vault/concept-pairs`);
    expect(calls[0]?.init?.method).toBe("POST");
    expect(jsonBody(calls[0])).toEqual({
      editor_id: LOCAL_EDITOR_ID,
      chunk_id: "chk_1",
      term: "tiến trình",
      definition_quote: quote,
    });
  });

  it("POST xác nhận: chỉ gửi field sửa; POST từ chối: editor_id và expected_version", async () => {
    const calls = stubFetch(() => Promise.resolve(jsonResponse(200, PAIR)));
    await realApi.confirmConceptPair("cp 1", 2, {});
    expect(calls[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/vault/concept-pairs/cp%201/confirm`);
    expect(jsonBody(calls[0])).toEqual({ editor_id: LOCAL_EDITOR_ID, expected_version: 2 });
    await realApi.confirmConceptPair("cp 1", 2, { term: "a", definitionQuote: "đoạn mới đủ dài" });
    expect(jsonBody(calls[1])).toEqual({
      editor_id: LOCAL_EDITOR_ID,
      expected_version: 2,
      term: "a",
      definition_quote: "đoạn mới đủ dài",
    });
    await realApi.rejectConceptPair("cp/1", 3);
    expect(calls[2]?.url).toBe(`${DEFAULT_API_BASE}/v1/vault/concept-pairs/cp%2F1/reject`);
    expect(calls[2]?.init?.method).toBe("POST");
    expect(jsonBody(calls[2])).toEqual({ editor_id: LOCAL_EDITOR_ID, expected_version: 3 });
  });

  it("lỗi: 404, 409 lệch phiên bản, 422 theo field, 503 kho bận, mất kết nối", async () => {
    stubFetch(() => Promise.resolve(jsonResponse(404, { detail: "không tìm thấy chunk" })));
    expect((await captureError(realApi.createConceptPair("c", "t", "đoạn đủ dài"))).info.kind).toBe("not_found");
    expect((await captureError(realApi.rejectConceptPair("p", 1))).info.kind).toBe("not_found");

    stubFetch(() => Promise.resolve(jsonResponse(409, { detail: "version lệch" })));
    const conflict = await captureError(realApi.confirmConceptPair("p", 1, {}));
    expect(conflict.info).toMatchObject({ kind: "version_conflict", action: "reload" });
    expect((await captureError(realApi.rejectConceptPair("p", 1))).info.kind).toBe("version_conflict");

    const term = { detail: [{ loc: ["body", "term"], msg: "x", type: "string_too_long" }] };
    stubFetch(() => Promise.resolve(jsonResponse(422, term)));
    expect((await captureError(realApi.createConceptPair("c", "t", "đoạn đủ dài"))).info.message).toContain(
      "Thuật ngữ",
    );
    const quote = { detail: [{ loc: ["body", "definition_quote"], msg: "x", type: "value_error" }] };
    stubFetch(() => Promise.resolve(jsonResponse(422, quote)));
    expect((await captureError(realApi.confirmConceptPair("p", 1, {}))).info.message).toContain("Đoạn định nghĩa");
    expect((await captureError(realApi.rejectConceptPair("p", 1))).info.message).not.toContain("Đoạn định nghĩa");

    stubFetch(() => Promise.resolve(jsonResponse(503, { detail: "kho bận" })));
    expect((await captureError(realApi.createConceptPair("c", "t", "đoạn đủ dài"))).info.kind).toBe("busy");

    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    const offline = await captureError(
      realApi.listConceptPairs({ subjectCode: "MAU101", chapterCode: null, status: null }),
    );
    expect(offline.status).toBeNull();
    expect(offline.info.kind).toBe("network");

    stubFetch(() => Promise.resolve(jsonResponse(200, { pair_id: "p" })));
    expect((await captureError(realApi.confirmConceptPair("p", 1, {}))).info.kind).toBe("server");
  });
});

describe("bật dữ liệu mẫu: chỉ bản ghi mẫu dùng fixture", () => {
  it("mã tài liệu mẫu đọc từ fixture, không gọi fetch; tài liệu khác gọi backend thật", async () => {
    resetMockDocumentSessions();
    const calls = stubFetch(() => Promise.resolve(jsonResponse(200, realDocumentBody())));
    const mocked = createApi(true);
    const sample = await mocked.getDocument(MOCK_IMPORT_DOC_ID);
    expect(sample.docId).toBe(MOCK_IMPORT_DOC_ID);
    expect(calls).toHaveLength(0);
    const real = await mocked.getDocument(REAL_DOC_ID);
    expect(real.docId).toBe(REAL_DOC_ID);
    expect(calls).toHaveLength(1);
  });

  it("listDocuments gộp danh sách thật (giữ thứ tự backend) với tài liệu mẫu của môn ở cuối", async () => {
    resetMockDocumentSessions();
    const calls = stubFetch(() => Promise.resolve(jsonResponse(200, [realSummaryBody()])));
    const mocked = createApi(true);
    const sampleSubject = await mocked.listDocuments(MOCK_IMPORT_SUBJECT);
    expect(sampleSubject.map((document) => document.docId)).toEqual([REAL_DOC_ID, MOCK_IMPORT_DOC_ID]);
    expect(calls[0]?.url).toBe(`${DEFAULT_API_BASE}/v1/documents?subject_code=${MOCK_IMPORT_SUBJECT}`);
    const otherSubject = await mocked.listDocuments("MAU202");
    expect(otherSubject.map((document) => document.docId)).toEqual([REAL_DOC_ID]);
  });

  it("listVaultChunks: câu của blueprint mẫu dùng chunk mẫu; blueprint thật cùng môn mẫu vẫn gọi backend", async () => {
    const calls = stubFetch(() => Promise.resolve(jsonResponse(200, { total: 0, chunks: [] })));
    const mocked = createApi(true);
    const blueprint = await mocked.createBlueprint({ subject_code: MOCK_IMPORT_SUBJECT, theory_slots: [] });
    const query = { subjectCode: blueprint.subjectCode, chapterCode: null, offset: 0, limit: 50 };

    const sample = await mocked.listVaultChunks({ ...query, blueprintId: blueprint.blueprintId });
    expect(sample.chunks.length).toBeGreaterThan(0);
    expect(calls).toHaveLength(0);

    const real = await mocked.listVaultChunks({ ...query, blueprintId: "bp_that" });
    expect(real.chunks).toEqual([]);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).not.toContain("blueprint");

    await mocked.listVaultChunks(query);
    expect(calls).toHaveLength(2);
  });

  it("tắt dữ liệu mẫu: luôn gọi backend theo môn của blueprint, mã blueprint không gửi đi", async () => {
    const calls = stubFetch(() => Promise.resolve(jsonResponse(200, { total: 0, chunks: [] })));
    const query = { subjectCode: "MAU202", chapterCode: "ch1", offset: 0, limit: 50, blueprintId: "bp_bat_ky" };
    await createApi(false).listVaultChunks(query);
    expect(calls[0]?.url).toBe(
      `${DEFAULT_API_BASE}/v1/vault/chunks?subject_code=MAU202&offset=0&limit=50&chapter_code=ch1`,
    );
  });
});
