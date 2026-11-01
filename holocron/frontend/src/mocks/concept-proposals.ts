/**
 * Dữ liệu mẫu cho màn khái niệm với cặp `proposed` (T2.3; spec §5.8, §6.6): một môn mẫu có vài đoạn văn, năm cặp đề xuất
 * (có `pre_selected` và `failed_gates`) và một job đề xuất chạy qua các lô khi bật `?mock_runtime=llm`. Gỡ cùng thư mục
 * `src/mocks/` khi backend có đủ field và endpoint job.
 *
 * Đây là NHÀ GIẢ của backend: nó giữ trạng thái cặp trong phiên (nhận, từ chối, sửa tên đều được lưu) và kiểm
 * `expected_version` như backend. Frontend KHÔNG dùng lại các hàm này; nó chỉ đọc response (I1).
 *
 * Môn mẫu là `MOCK_CONCEPT_PROPOSAL_SUBJECT`: tạo môn mang mã đó rồi mở tab Khái niệm. Để thử nhánh lỗi: cặp
 * "Bài tập 3" luôn trả 422 khi nhận (đoạn trích không khớp); mọi cặp trả 409 khi `expected_version` đã cũ.
 */

import type { ConceptPairEdits } from "@/lib/api";
import { parseProposalJobList } from "@/lib/concept-proposals";
import { type DocumentSummaryView, parseDocumentSummaries } from "@/lib/documents-data";
import { ApiError, type ClassifyOptions, classifyHttpError, invalidResponseError } from "@/lib/errors";
import type { ProposalJobView } from "@/lib/import-result";
import { type ConceptPairList, parseConceptPair, parseConceptPairList } from "@/lib/pairs";
import { type ChunkFilter, type ChunkListView, parseChunkList } from "@/lib/vault";
import type { ConceptPair, DocumentMeta, DocumentSummary, KnowledgeChunk } from "@/types/holocron";

import { RUNTIME_FIXTURES } from "./fixtures/runtime";
import { currentRuntimeScenario } from "./store";

export const MOCK_CONCEPT_PROPOSAL_SUBJECT = "MAU103";
export const MOCK_CONCEPT_PROPOSAL_DOC_ID = "doc_a1b2c3d4e5f60718";

const PAIR_ID_PREFIX = "pair_cp";
const EXERCISE_PAIR_ID = `${PAIR_ID_PREFIX}0000000000000004`;

/** Môn của dữ liệu mẫu này: chỉ mã này dùng nhà giả. */
export function isConceptProposalMock(subjectCode: string): boolean {
  return subjectCode === MOCK_CONCEPT_PROPOSAL_SUBJECT;
}

/** Cặp của dữ liệu mẫu này (theo tiền tố mã). */
export function isConceptProposalPair(pairId: string): boolean {
  return pairId.startsWith(PAIR_ID_PREFIX);
}

const META: DocumentMeta = {
  doc_id: MOCK_CONCEPT_PROPOSAL_DOC_ID,
  subject_code: MOCK_CONCEPT_PROPOSAL_SUBJECT,
  title: "Tài liệu mẫu: Đề xuất khái niệm",
  source_kind: "textbook",
  sha256: "a1b2c3d4e5f607186e1d2c3b4a59687f0e1d2c3b4a59687f0e1d2c3b4a596871",
  page_count: 6,
  has_text_layer: true,
  uploaded_at: "2026-11-01T09:00:00Z",
};

const CHUNK_1_TEXT =
  "Xác suất của một biến cố là số thực trong đoạn từ 0 đến 1, đo khả năng biến cố xảy ra.\nBiến cố đối của A là biến cố xảy ra khi và chỉ khi A không xảy ra.";
const CHUNK_2_TEXT =
  "Kỳ vọng của biến ngẫu nhiên rời rạc là trung bình có trọng số của các giá trị có thể nhận.\nBài tập 3: Tính kỳ vọng của số chấm khi gieo một con xúc xắc cân đối.";
/** Đoạn 3 lưu dạng NFD (chữ rời dấu), trích dẫn dạng NFC: thử tô sáng qua chuẩn hoá. */
const CHUNK_3_TEXT =
  "Phương sai đo mức độ phân tán của các giá trị quanh kỳ vọng.\nĐộ lệch chuẩn là căn bậc hai của phương sai.".normalize(
    "NFD",
  );

function chunk(suffix: string, chapter: string, page: number, rawText: string): KnowledgeChunk {
  return {
    chunk_id: `chunk_cp${suffix}`,
    doc_id: MOCK_CONCEPT_PROPOSAL_DOC_ID,
    subject_code: MOCK_CONCEPT_PROPOSAL_SUBJECT,
    chapter_code: chapter,
    page_number: page,
    page_end: null,
    raw_text: rawText,
    verbatim_quote: rawText.split("\n")[0] ?? rawText,
    category: "THEORY",
    tags: [],
    topic_keys: [],
    linked_chunk_ids: [],
    token_count: 40,
    embedding_model: null,
    label_status: "confirmed",
    mapping_confidence: null,
    version: 1,
    is_exercise: null,
  };
}

const CHUNKS: readonly KnowledgeChunk[] = [
  chunk("0000000000000001", "ch1", 2, CHUNK_1_TEXT),
  chunk("0000000000000002", "ch1", 3, CHUNK_2_TEXT),
  chunk("0000000000000003", "ch2", 5, CHUNK_3_TEXT),
];

function pair(
  index: number,
  chunkIndex: number,
  term: string,
  quote: string,
  options: { preSelected: boolean; failedGates: ConceptPair["failed_gates"] },
): ConceptPair {
  const target = CHUNKS[chunkIndex];
  return {
    pair_id: `${PAIR_ID_PREFIX}${String(index).padStart(16, "0")}`,
    chunk_id: target?.chunk_id ?? "",
    subject_code: MOCK_CONCEPT_PROPOSAL_SUBJECT,
    chapter_code: target?.chapter_code ?? "ch1",
    term,
    definition_quote: quote,
    term_spans: [],
    status: "proposed",
    proposed_by: "llm",
    mapping_confidence: null,
    version: 1,
    created_at: "2026-11-01T09:30:00Z",
    confirmed_at: null,
    pre_selected: options.preSelected,
    failed_gates: options.failedGates,
  };
}

function initialPairs(): ConceptPair[] {
  return [
    pair(1, 0, "Xác suất", "Xác suất của một biến cố là số thực trong đoạn từ 0 đến 1, đo khả năng biến cố xảy ra.", {
      preSelected: true,
      failedGates: [],
    }),
    pair(2, 0, "Biến cố đối", "Biến cố đối của A là biến cố xảy ra khi và chỉ khi A không xảy ra.", {
      preSelected: true,
      failedGates: [],
    }),
    pair(
      3,
      1,
      "Kỳ vọng",
      "Kỳ vọng của biến ngẫu nhiên rời rạc là trung bình có trọng số của các giá trị có thể nhận.",
      { preSelected: false, failedGates: ["G5_NUMBERS"] },
    ),
    pair(4, 1, "Bài tập 3", "Bài tập 3: Tính kỳ vọng của số chấm khi gieo một con xúc xắc cân đối.", {
      preSelected: false,
      failedGates: ["EXERCISE"],
    }),
    pair(5, 2, "Phương sai", "Phương sai đo mức độ phân tán của các giá trị quanh kỳ vọng.", {
      preSelected: true,
      failedGates: [],
    }),
  ];
}

/** Job mẫu qua ba bước: lô 1/3, lô 2/3, xong. Số do nhà giả đặt sẵn, giao diện chỉ hiển thị. */
const JOB_STEPS: readonly Record<string, unknown>[] = [1, 2, 3].map((done, index) => ({
  job_id: "pjob_00112233445566ff",
  subject_code: MOCK_CONCEPT_PROPOSAL_SUBJECT,
  trigger: "manual",
  document_id: MOCK_CONCEPT_PROPOSAL_DOC_ID,
  status: done === 3 ? "succeeded" : index === 0 ? "queued" : "running",
  chunk_count: 3,
  batch_size: 1,
  batches_total: 3,
  batches_done: done === 3 ? 3 : done - 1,
  batches_failed: 0,
  batch_errors: {},
  proposed_count: 5,
  pre_selected_count: 3,
  rejected_count: 1,
  rejected_by_reason: { duplicate: 1 },
  exercise_chunks: 0,
  cancel_requested: false,
  created_at: "2026-11-01T09:30:00Z",
  started_at: null,
  finished_at: null,
}));

interface Session {
  pairs: ConceptPair[];
  /** `null` ⇒ chưa có job nào trong phiên. */
  jobStep: number | null;
}

let current: Session | null = null;

function session(): Session {
  current ??= { pairs: initialPairs(), jobStep: 0 };
  return current;
}

/** Đặt lại trạng thái phiên (dùng trong test). */
export function resetConceptProposalMock(): void {
  current = null;
}

function llmAvailable(): boolean {
  const capabilities = RUNTIME_FIXTURES[currentRuntimeScenario()].capabilities;
  return capabilities.some((item) => item.name === "concept_pair_llm_proposal" && item.available);
}

function checked<T>(value: T | null): T {
  if (value === null) throw new ApiError(200, invalidResponseError());
  return value;
}

function reject(status: number, detail: unknown, options: ClassifyOptions): Promise<never> {
  return Promise.reject(new ApiError(status, classifyHttpError(status, { detail }, options)));
}

const CONFIRM: ClassifyOptions = { context: "pair_confirm", versioned: true };
const REJECT: ClassifyOptions = { context: "pair_reject", versioned: true };

function listDocuments(subjectCode: string): DocumentSummaryView[] {
  if (!isConceptProposalMock(subjectCode)) return [];
  const summary: DocumentSummary = {
    meta: structuredClone(META),
    status: "confirmed",
    pages_pending: 0,
    pages_needs_review: 0,
    pages_failed: 0,
    pages_confirmed: META.page_count,
    pages_dropped: 0,
  };
  return checked(parseDocumentSummaries([summary]));
}

function browseVaultChunks(filter: ChunkFilter): Promise<ChunkListView> {
  const chunks = isConceptProposalMock(filter.subjectCode) ? CHUNKS : [];
  const page = chunks.slice(filter.offset, filter.offset + filter.limit);
  return Promise.resolve(checked(parseChunkList(structuredClone({ total: chunks.length, chunks: page }))));
}

function listConceptPairs(subjectCode: string): Promise<ConceptPairList> {
  const pairs = isConceptProposalMock(subjectCode) ? session().pairs : [];
  return Promise.resolve(checked(parseConceptPairList(structuredClone(pairs))));
}

function find(pairId: string): ConceptPair | null {
  return session().pairs.find((item) => item.pair_id === pairId) ?? null;
}

function confirmConceptPair(pairId: string, expectedVersion: number, edits: ConceptPairEdits): Promise<ConceptPair> {
  const target = find(pairId);
  if (target === null) return reject(404, "không tìm thấy cặp khái niệm", CONFIRM);
  if (target.version !== expectedVersion || target.status !== "proposed") {
    return reject(409, "phiên bản đã cũ", CONFIRM);
  }
  if (pairId === EXERCISE_PAIR_ID) {
    return reject(
      422,
      [{ loc: ["body", "definition_quote"], msg: "definition_quote không khớp nguyên văn", type: "value_error" }],
      CONFIRM,
    );
  }
  target.status = "confirmed";
  target.version += 1;
  target.confirmed_at = "2026-11-01T10:00:00Z";
  if (edits.term !== undefined) target.term = edits.term;
  return Promise.resolve(checked(parseConceptPair(structuredClone(target))));
}

function rejectConceptPair(pairId: string, expectedVersion: number): Promise<ConceptPair> {
  const target = find(pairId);
  if (target === null) return reject(404, "không tìm thấy cặp khái niệm", REJECT);
  if (target.version !== expectedVersion || target.status !== "proposed") {
    return reject(409, "phiên bản đã cũ", REJECT);
  }
  target.status = "rejected";
  target.version += 1;
  return Promise.resolve(checked(parseConceptPair(structuredClone(target))));
}

/** Mỗi lần hỏi, job tiến một bước tới khi xong. Không có LLM ở kịch bản hiện tại ⇒ chưa có job nào. */
function listProposalJobs(subjectCode: string): Promise<ProposalJobView[]> {
  if (!isConceptProposalMock(subjectCode) || !llmAvailable()) return Promise.resolve([]);
  const state = session();
  if (state.jobStep === null) return Promise.resolve([]);
  const raw = JOB_STEPS[state.jobStep];
  if (state.jobStep < JOB_STEPS.length - 1) state.jobStep += 1;
  return Promise.resolve(checked(parseProposalJobList(structuredClone([raw]))));
}

function startProposalJob(subjectCode: string): Promise<ProposalJobView> {
  if (!llmAvailable() || !isConceptProposalMock(subjectCode)) {
    return reject(
      409,
      {
        code: "CAPABILITY_UNAVAILABLE",
        capability: "concept_pair_llm_proposal",
        reason: "llm_not_configured",
        message: "concept_pair_llm_proposal không khả dụng",
      },
      {},
    );
  }
  session().jobStep = 0;
  return listProposalJobs(subjectCode).then((jobs) => checked(jobs[0] ?? null));
}

/** Handler của môn mẫu đề xuất khái niệm (xem `createApi`). */
export const mockConceptProposalApi = {
  listDocuments,
  browseVaultChunks,
  listConceptPairs,
  confirmConceptPair,
  rejectConceptPair,
  listProposalJobs,
  startProposalJob,
};
