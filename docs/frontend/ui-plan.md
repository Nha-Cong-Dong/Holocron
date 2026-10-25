# Kế hoạch UI frontend desktop (F0)

- Trạng thái: kế hoạch, chưa có code. Viết ngày 2026-10-26.
- Phạm vi: các giai đoạn F1–F5 của frontend desktop (Next.js 15, đóng gói Tauri sau này).
- Nguồn chân lý: `SPECIFICATION.md` v0.7 (bổ sung 2026-10-26), §0, §1, §4, §5.4–§5.14, §6, §11.4, §13.1, §16, §17; ADR-005…008; `.claude/CLAUDE.md` §1, §2.1, §2.3, §4.2, §4.3. Kế hoạch này không đổi contract nào. Chỗ nào cần đổi contract thì ghi ở mục 9 để người dùng quyết.
- Dữ liệu khảo sát: OpenAPI xuất từ `app.openapi()`, không chạy server, trên hai nhánh:
  - `feat/api-document-import` (`a22b037`, gọi tắt **b3**): bộ nhập tài liệu và Vault.
  - `feat/vault-theory-llm` (`95745c5`, gọi tắt **b6**): đề xuất cặp khái niệm bằng LLM, kiểu `Capability`, bộ sinh câu lý thuyết.
  - `origin/develop` (`99a3d2e`, đã tách router ở PR #37) chưa có router `vault` hay `documents`, tức chưa có endpoint nào của hai nhánh này. Cột "Đã có ở" trong mục 2 nói về hai nhánh trên, chưa phải `develop`.

## 1. Hiện trạng frontend (nhánh b3)

| Mục | Hiện trạng | Hệ quả cho kế hoạch |
|---|---|---|
| Dependency | `next 15.5.28`, `react 19.1.10`, `react-dom 19.1.10`; dev: `@biomejs/biome 2.5.15`, `typescript 5.9.3`, `vitest 3.2.4`, `@types/*` | Không có thư viện component, icon, PDF.js, KaTeX hay testing-library. Kế hoạch chỉ dùng những thứ này (đề xuất thêm ở mục 8) |
| Test | Vitest `environment: "node"`, chỉ nhận `src/**/*.test.ts` | Chỉ test được logic thuần ở `src/lib/` và `src/mocks/`. Không test component được nếu chưa thêm dependency |
| Trang | `/` (upload đề, danh sách dạng bài gắn cứng RR), `/ingestion/[itemId]`, `/blueprints/[blueprintId]` | Hai route động không build được khi static export (mục 4) |
| `lib/api.ts` | `API_BASE = "/api/holocron"` ghi cứng; một hàm `request<T>` là chỗ duy nhất ép `as T` | Nơi duy nhất cần đổi để chạy dưới Tauri (mục 4) |
| `next.config.ts` | `rewrites` `/api/holocron/:path*` → `HOLOCRON_API_URL` | Không chạy khi `output: "export"` (mục 4) |
| `globals.css` | Biến màu sáng/tối theo `prefers-color-scheme`, font `system-ui, …, Roboto` | Giữ cơ chế, thay bộ token (mục 5) |
| `types/holocron.ts` | Có kiểu cho bộ nhập tài liệu và Vault của b3 | Lệch spec v0.7 ở 3 chỗ: `ReviewReason` thiếu `LOW_TEXT_SCORE` (§5.7); `DocumentImport` thiếu `chapter_code` (§5.7, duyệt 2026-10-26); `ApiErrorBody` chưa có dạng `CapabilityError` và `LLM_UPSTREAM_ERROR` (§6.0) |
| Lỗi tiềm ẩn | `describeError` trong `lib/api.ts` coi mọi `detail` dạng object là `{message, issues}` rồi đọc `detail.issues.length` | Khi backend trả 409 `CapabilityError` hoặc 502 `LLM_UPSTREAM_ERROR` (b6 đã trả), `issues` là `undefined` nên hàm ném `TypeError`. Sửa ở F1 (mục 6) |
| Nợ ARCH-001 | `UploadForm.tsx` gắn cứng topic RR và ô `quantum`; `GanttChart` vẽ riêng một dạng bài | Không động tới trong F1–F5, không thêm chỗ gắn cứng mới (CLAUDE.md §1) |

## 2. Màn hình và endpoint

Ký hiệu cột "Đã có ở": **b3**, **b6** là nhánh có endpoint (đã kiểm bằng OpenAPI); **CHƯA CÓ** là chưa có trên nhánh nào; "lệch" là endpoint có nhưng model khác spec.

### F1. Khung app và thanh trạng thái khả năng

| Việc trên UI | Method, path | Request → Response | Đã có ở | Ghi chú |
|---|---|---|---|---|
| Thanh trạng thái, băng "chạy offline", kiểm kết nối backend | GET `/v1/runtime` | — → `RuntimeInfo` | b3, b6 (lệch) | Code chỉ trả `{demo_mode, llm_configured, vlm_configured}`. Spec §5.12 cần thêm `capabilities: Capability[]` (đã duyệt) và `mock_extractor` (đề xuất). Kiểu `Capability`, `CapabilityError` và hàm `compute_capabilities` đã có ở b6 (`domain/capabilities.py`, `services/capabilities.py`) nhưng chưa nối vào `/v1/runtime` |
| Chọn môn ở thanh bên | GET `/v1/subjects` | — → `Subject[]` | CHƯA CÓ | §6.8, đường dẫn còn đề xuất. Tạm thời lấy danh sách `subject_code` từ GET `/v1/documents` (b3) |

### F2. Môn học, nhập tài liệu, cảnh báo theo trang, Vault và xem chunk

| Việc trên UI | Method, path | Request → Response | Đã có ở | Ghi chú |
|---|---|---|---|---|
| Danh sách môn, tạo môn | GET/POST `/v1/subjects`, GET `/v1/subjects/{code}` | `{subject_code, display_name?, exam_format?}` → `Subject` | CHƯA CÓ | §5.14 (model `Subject` đã duyệt), §6.8 (đường dẫn đề xuất) |
| Danh sách tài liệu của môn | GET `/v1/documents?subject_code=` | → `DocumentSummary[]` | b3 | Các số đếm trang (`pages_*`) do backend trả |
| Tải PDF lên | POST `/v1/documents` (multipart) | `file, subject_code, source_kind, chapter_code, output_language?, title?` → 202/200 `DocumentImport` | b3 (lệch) | b3 chưa nhận `chapter_code` (field bắt buộc, duyệt 2026-10-26, §6.5) và `DocumentImport` chưa có `chapter_code` (§5.7). `source_kind = "study_guide"` còn đề xuất (§5.8) |
| Hỏi trạng thái khi đang đọc | GET `/v1/documents/{id}` | → `DocumentImport` | b3 | Hỏi lại 1–2 s một lần (§6.5) |
| Xem một trang | GET `/v1/documents/{id}/pages/{n}` | → `PageExtraction` | b3 (lệch) | `ReviewReason` của b3 thiếu `LOW_TEXT_SCORE` (§5.7) |
| Sửa dòng | PATCH `/v1/documents/{id}/pages/{n}/lines` | `{editor_id, expected_version, edits[], appended_lines[]}` → `PageExtraction` | CHƯA CÓ | §6.5, quy tắc duyệt 2026-10-26. Tên model request chưa có trong spec; frontend đặt `PageLinesPatch` theo đúng các field của §6.5 |
| Xác nhận một trang | POST `/v1/documents/{id}/pages/{n}/confirm` | `{editor_id, expected_version}` → `PageExtraction` | CHƯA CÓ | §6.5. Nhận trang `ok` và `needs_review` |
| Xác nhận cả tài liệu | POST `/v1/documents/{id}/confirm` | `{editor_id}` → `DocumentImport` | CHƯA CÓ | §6.5. Chỉ xác nhận trang `ok` |
| Xoá tài liệu (để nhập lại trang `failed`) | DELETE `/v1/documents/{id}` | → 204 | b3 | §5.7: trang `failed` không sửa được trong MVP |
| Ảnh trang và mở trích dẫn đúng trang | (tải file gốc) | — | CHƯA CÓ, cũng chưa có contract | §5.7 và §12.2 cần PDF.js render "từ file gốc", nhưng không endpoint nào trả file gốc. Xem mục 9, câu 5 |
| Duyệt chunk của Vault | GET `/v1/vault/chunks` | query `subject_code, chapter_code, topic_key[], label_status, offset, limit` → `ChunkListResponse` | b3, b6 | §6.6 |
| Tìm trong Vault | GET `/v1/vault/search` | query `q, subject_code, topic_key[], limit` → `SearchHit[]` | b3, b6 | Không hiện `score` (§6.6) |
| Sửa nhãn chunk | PATCH `/v1/vault/chunks/{id}/labels` | `ChunkLabelsPatch` → `KnowledgeChunk` | b3, b6 | Sau khi sửa, `label_status = "confirmed"` |

### F3. Xác nhận cặp khái niệm

| Việc trên UI | Method, path | Request → Response | Đã có ở | Ghi chú |
|---|---|---|---|---|
| Danh sách cặp theo môn, chương, trạng thái | GET `/v1/vault/concept-pairs` | query `subject_code, chapter_code, status` → `ConceptPair[]` | b3, b6 | Backend sắp theo `(chapter_code, term, pair_id)` |
| Bôi đoạn trong chunk để tạo cặp | POST `/v1/vault/concept-pairs` | `ConceptPairCreate` → 201 `ConceptPair` | b3, b6 | 422 khi trích dẫn không khớp nguyên văn (I3) |
| Sửa rồi xác nhận | POST `/v1/vault/concept-pairs/{id}/confirm` | `ConceptPairConfirm` → `ConceptPair` | b3, b6 | 409 khi lệch version |
| Từ chối | POST `/v1/vault/concept-pairs/{id}/reject` | `ConceptPairReject` → `ConceptPair` | b3, b6 | |
| LLM đề xuất cặp | POST `/v1/vault/concept-pairs/propose` | `ConceptPairProposeRequest {subject_code, chunk_ids}` → `ConceptPairProposeResponse {pairs, rejected_count}` | chỉ b6 | 409 `CapabilityError` khi thiếu LLM; 502 `LLM_UPSTREAM_ERROR`. Nút bật/tắt theo khả năng `concept_pair_llm_proposal` (chưa có trong `/v1/runtime`) |
| Văn bản nguồn để bôi | GET `/v1/vault/chunks` | như F2 | b3, b6 | |

### F4. Luyện tập MCQ/ESSAY và kết quả

| Việc trên UI | Method, path | Request → Response | Đã có ở | Ghi chú |
|---|---|---|---|---|
| Tạo bộ đề lý thuyết | POST `/v1/blueprints` | `BlueprintCreate {subject_code, item_ids, theory_slots[], duration_minutes?}` → 201 `ExamBlueprint` | CHƯA CÓ | §5.9, §6.7. `TheorySlotSpec` có `format` (duyệt 2026-10-26). 409 `CapabilityError` khi có slot `ESSAY` mà `essay_grading_offline` không khả dụng |
| Sinh câu | POST `/v1/blueprints/{id}/twins` | `TwinBatchRequest` (+ header `Idempotency-Key`) → 201 `TwinBatch` | b3, b6 (lệch) | Code chưa có `question_kind`, `trace` dạng union (`TheoryTrace`), `template_id`/`topic_key` nullable, `artifacts`, `essay`, `MisconceptionRef.citation` (§5.11, §5.14); `TwinBatch` chưa có `batch_id`, `created_at` (§6.7). Slot lý thuyết chưa được xử lý: bộ sinh có ở b6 nhưng chưa nối vào twin |
| Chấm câu tự luận | POST `/v1/twins/{twin_id}/essay-grade` | `EssayGradeRequest {answer_text}` → `EssayGradeResult` | CHƯA CÓ | §5.14, §6.9. Đường dẫn đã duyệt; mã lỗi còn đề xuất. "MVP nếu còn thời gian" (mốc 29/10) |
| Mở lại một lượt sinh đã có | GET `/v1/blueprints/{id}/twins/{batch_id}` | → `TwinBatch` | CHƯA CÓ | §6.7 |

### F5. Lịch sử và cài đặt

| Việc trên UI | Method, path | Request → Response | Đã có ở | Ghi chú |
|---|---|---|---|---|
| Danh sách bộ đề đã tạo | GET `/v1/blueprints?subject_code=` | → `BlueprintSummary[]` | CHƯA CÓ | §6.7; model `BlueprintSummary` còn đề xuất (§17 D8) |
| Chi tiết bộ đề | GET `/v1/blueprints/{id}` | → `ExamBlueprint` | CHƯA CÓ | §6.7 |
| Các lượt sinh của bộ đề | GET `/v1/blueprints/{id}/twins` | → `TwinBatchSummary[]` | CHƯA CÓ | §6.7; model còn đề xuất |
| Lịch sử lượt làm bài (đã chọn gì, đúng/sai) | — | — | CHƯA CÓ, cũng chưa có contract | §5.13: "Lịch sử luyện tập là việc sau MVP"; kết quả chấm tự luận không được lưu. Không dựng fixture vì không có shape để theo (mục 9, câu 4) |
| Hình thức thi của môn | PATCH `/v1/subjects/{code}` | `{editor_id, expected_version, display_name?, exam_format?}` → `Subject` | CHƯA CÓ | §6.8, đề xuất |
| Trạng thái khả năng, hướng dẫn cài Tesseract | GET `/v1/runtime` | → `RuntimeInfo` | b3, b6 (lệch) | Như F1 |
| Cấu hình key LLM | — | — | không có endpoint, theo thiết kế | §13.1: key nằm ở biến môi trường (webapp) hoặc kho khoá của hệ điều hành (Tauri). Màn cài đặt chỉ hiện trạng thái và thông báo dữ liệu rời máy |

### Endpoint ngoài F1–F5 nhưng frontend vẫn phải giữ chạy

Luồng đề thi hiện có (`POST /v1/ingestion/upload`, `GET/PATCH /v1/ingestion/items/…`, `…/approve`, `…/non-cloneable`) có ở cả b3 và b6, giữ nguyên hành vi. Chưa có: `PATCH /v1/ingestion/items/{id}/labels` (§6.7) và `POST /v1/documents/{id}/pages/{n}/items` (§6.5). F1 chỉ đổi đường dẫn trang của luồng này (mục 4.3).

## 3. Endpoint và field còn thiếu, kế hoạch dữ liệu mẫu

### 3.1 Danh sách

| # | Thiếu | Nguồn contract | Trạng thái duyệt trong spec | Fixture trong `src/mocks/` |
|---|---|---|---|---|
| M1 | `RuntimeInfo.capabilities` (9 phần tử theo thứ tự `CapabilityName`), `mock_extractor` | §5.12, §11.4 | `capabilities` đã duyệt; mã `CapabilityReason` và `mock_extractor` đề xuất | `fixtures/runtime.ts`: 3 kịch bản (offline không LLM; đủ LLM; thiếu Tesseract). `detail_vi` chép nguyên văn `REASON_DETAILS_VI` ở b6 |
| M2 | `CapabilityError` (409) và `{"code": "LLM_UPSTREAM_ERROR", "message"}` (502) trong kiểu lỗi phía frontend | §6.0 | đã duyệt | `fixtures/errors.ts`: hai thân lỗi mẫu cho handler giả |
| M3 | `Subject`, GET/POST/PATCH `/v1/subjects` | §5.14, §6.8 | model đã duyệt; đường dẫn đề xuất | `fixtures/subjects.ts`: 2 môn mẫu tự đặt (`MAU101` `exam_format = mcq`, `MAU202` `exam_format = essay`) |
| M4 | Field form `chapter_code` của POST `/v1/documents`, `DocumentImport.chapter_code`, `ReviewReason` `LOW_TEXT_SCORE` | §5.7, §6.5 | đã duyệt 2026-10-26 (vị trí field `DocumentImport.chapter_code` còn đề xuất) | `fixtures/documents.ts`: một tài liệu 4 trang (ok; `needs_review` với `TOKEN_MISMATCH` và `LOW_CONF_LINES`; `needs_review` với `LOW_TEXT_SCORE`; `failed` với `OCR_ENGINE_MISSING`) |
| M5 | PATCH `…/pages/{n}/lines`, POST `…/pages/{n}/confirm`, POST `/v1/documents/{id}/confirm` | §6.5 | đã duyệt 2026-10-26 (`PageEditAudit` và ghi đè `conf` còn đề xuất) | Handler giả trả bản `PageExtraction` dựng sẵn cho trạng thái "sau khi sửa" và "đã xác nhận"; không tự tính lại `review_reasons` (mục 3.3) |
| M6 | POST `/v1/vault/concept-pairs/propose` trên nhánh dùng chung với bộ nhập | §6.6 | đã duyệt | `fixtures/vault.ts`: `ConceptPairProposeResponse` với 2 cặp `proposed` và `rejected_count = 1` |
| M7 | POST `/v1/blueprints` (`BlueprintCreate`, `TheorySlotSpec`, `ExamBlueprint`, `BlueprintSlot` nullable) | §5.9, §6.7 | đã duyệt 2026-10-26 (mặc định `format` từ `exam_format` còn đề xuất) | `fixtures/blueprints.ts`: blueprint chỉ có slot lý thuyết |
| M8 | Câu lý thuyết trong twin: `question_kind`, `TheoryTrace`, `template_id`/`topic_key` nullable, `MisconceptionRef.citation`, `artifacts`, `TwinBatch.batch_id`/`created_at` | §5.11, §6.7 | đã duyệt 2026-10-25 | `fixtures/twins.ts`: một `TwinBatch` gồm 2 câu MCQ4 lý thuyết (dạng điền khuyết và chọn định nghĩa đúng) và một `ReviewTicket` "không đủ căn cứ trong Vault" |
| M9 | `TwinQuestion.essay` (`EssaySpec`, `EssayPoint`), POST `/v1/twins/{id}/essay-grade`, `EssayGradeResult` | §5.14, §6.9 | field đã duyệt; tên model và mã lỗi đề xuất | `fixtures/twins.ts` thêm một câu `ESSAY` theo đúng ví dụ JSON ở §6.9; `fixtures/essay.ts`: 2 kết quả (khớp 1/2 ý; khớp 2/2 ý) |
| M10 | GET `/v1/blueprints`, `/v1/blueprints/{id}`, `/v1/blueprints/{id}/twins`, `/v1/blueprints/{id}/twins/{batch_id}` (`BlueprintSummary`, `TwinBatchSummary`) | §6.7 | đường dẫn đã duyệt; hai model tóm tắt đề xuất | `fixtures/history.ts` |
| M11 | Lịch sử lượt làm bài | không có | spec ghi là việc sau MVP (§5.13) | Không có fixture. Không bịa shape |
| M12 | File gốc của tài liệu để render trang và mở trích dẫn | không có | §5.7, §12.2 giả định có file gốc ở phía client | Không có fixture (mục 9, câu 5) |
| M13 | Chế độ lưu trữ (in-memory hay SQLite) và tên bên nhận dữ liệu LLM trong `RuntimeInfo` | không có | §13.1 yêu cầu UI nêu tên bên nhận; §5.13 in-memory làm mất dữ liệu khi tắt backend | Không có fixture (mục 9, câu 6) |
| M14 | Header `X-Holocron-Token` và kiểm `Host` | §13.1 | đã duyệt; chưa có code | Không cần fixture; `lib/config.ts` chuẩn bị chỗ gắn header (mục 4) |

### 3.2 Một cờ cấu hình, một chỗ dễ gỡ

- **Cờ:** `NEXT_PUBLIC_HOLOCRON_MOCKS=1`. Chỉ `src/lib/config.ts` đọc biến này. Khi build tĩnh, Next chép thẳng giá trị vào bundle lúc build, nên đây là cờ build.
- **Cách nối, không ép kiểu:** `lib/api.ts` khai báo `interface HolocronApi` gồm mọi hàm gọi backend. `src/mocks/index.ts` export `mockApi: Partial<HolocronApi>`, chỉ chứa endpoint của bảng 3.1. `lib/api.ts` export đúng một dòng:

  ```ts
  export const api: HolocronApi = USE_MOCKS ? { ...realApi, ...mockApi } : realApi;
  ```

  Endpoint đã có ở backend vẫn gọi backend thật kể cả khi bật cờ. Không có dispatcher theo URL, nên không cần thêm `as T` ngoài chỗ parse JSON (CLAUDE.md §4.2).
- **Gỡ:** khi backend có một endpoint, xoá phần tử đó khỏi `mockApi` cùng fixture của nó. Khi mọi endpoint đã có, xoá `src/mocks/`, dòng trên và cờ trong `config.ts`.
- **Kiểu của contract chưa có trong backend:** đặt ở `src/types/contract-pending.ts`. Mỗi kiểu ghi mục § của spec và trạng thái duyệt; chuyển sang `types/holocron.ts` trong cùng PR backend triển khai model đó, để `holocron.ts` vẫn khớp 1:1 với Pydantic (CLAUDE.md §2.3). Cần người dùng chọn (mục 9, câu 2).
- **Khi bật cờ:** thanh trạng thái hiện nhãn cố định "Dữ liệu mẫu (mock)" trên mọi trang, để không ai nhầm dữ liệu mẫu với dữ liệu thật. Bản giao cho người dùng ôn thi không bật cờ này.
- **Nội dung fixture:** văn bản mẫu tự viết, không lấy từ tài liệu có bản quyền (CLAUDE.md §6). Mã môn trung tính (`MAU101`, `MAU202`); không fixture hay component nào rẽ nhánh theo môn.

### 3.3 Quy tắc cho handler giả

- Handler giả **không tính** điểm, `review_reasons`, `matched_count`, phương án hay đáp án. Nó chỉ trả fixture dựng sẵn (ví dụ `essay-grade` luôn trả một trong hai `EssayGradeResult` cố định, chọn theo `twin_id`, không theo nội dung câu trả lời). Làm vậy để frontend không vô tình chép một phần Deterministic Core (I1, CLAUDE.md §2.1).
- Trạng thái giả (cặp đã xác nhận, trang đã xác nhận) giữ trong một object của module `src/mocks/store.ts`, chỉ tồn tại trong phiên chạy, gỡ cùng thư mục.
- `src/mocks/fixtures.test.ts` kiểm chính fixture theo các luật của contract:
  - I3: mọi `verbatim_quote` và `definition_quote` là chuỗi con của `raw_text` của chunk tương ứng, sau NFC và gộp khoảng trắng;
  - I2: câu MCQ4 có đúng 3 khoá trong `distractor_to_misconception_map`, không chứa `correct_key`, mã thuộc `THEORY_MISCONCEPTIONS`;
  - `EssayGradeResult`: `matched_count + len(missing_points) = total_points`, `point_index` phủ đúng `0..total_points-1`;
  - `capabilities` đủ 9 phần tử theo thứ tự khai báo, `reason = null` khi và chỉ khi `available = true`;
  - `PageExtraction`: `status = "needs_review"` khi và chỉ khi `review_reasons` không rỗng và trang chưa `failed`.

## 4. Tương thích Tauri và static export

### 4.1 Vấn đề

1. `rewrites` trong `next.config.ts` cần Next server. Bản `output: "export"` nạp vào WebView của Tauri không có server, nên `/api/holocron/*` trả 404.
2. Route động `/ingestion/[itemId]`, `/blueprints/[blueprintId]` không build được khi export nếu không liệt kê trước tham số (`generateStaticParams`). ID do backend sinh lúc chạy nên không liệt kê trước được.
3. Sidecar nên tự chọn cổng trống (`docs/tauri-spike-plan.md` mục 5.1), nên địa chỉ backend chỉ biết lúc chạy, không biết lúc build.
4. Origin của WebView (`http://tauri.localhost` trên Windows, `tauri://localhost` trên Linux) khác origin backend. Spec §6.0 hiện ghi "cùng origin, không cần CORS". Header `X-Holocron-Token` (§13.1) khiến mọi request khác origin phải qua preflight.

### 4.2 Phương án nối WebView với backend

| Phương án | Cách làm | Ưu | Nhược | Đổi contract? |
|---|---|---|---|---|
| A. URL tuyệt đối + CORS | Frontend gọi `http://127.0.0.1:<port>`; backend bật CORS chỉ cho origin của Tauri và cho phép header token | Không thêm code Rust; ít đổi frontend | Origin của Tauri khác nhau giữa Windows và Linux; mọi request có token phải preflight; danh sách origin cho phép thành một cấu hình bảo mật mới | Có: §6.0 bỏ "không cần CORS"; cần ADR |
| B. Backend phục vụ bản export, cùng origin | Sidecar mount thư mục `out/` của Next; Tauri mở cửa sổ ở `http://127.0.0.1:<port>/`; token được Tauri chèn qua initialization script | Giữ "cùng origin, không CORS" của §6.0; `Host` luôn hợp lệ; base URL tương đối như hiện nay | Backend thêm phần phục vụ file tĩnh (đổi kiến trúc router); cửa sổ Tauri nạp URL ngoài, cần kiểm cấu hình IPC và CSP của Tauri | Không đổi API; đổi kiến trúc backend, cần người dùng duyệt |
| C. Proxy qua Tauri (custom protocol hoặc IPC) | JS gọi một scheme riêng; Rust chuyển tiếp tới sidecar và tự gắn token | Token không bao giờ nằm trong JS; backend không thấy origin của trình duyệt | Thêm code Rust và crate HTTP; file 50 MiB đi qua IPC, cần đo; thêm `@tauri-apps/api` nếu dùng IPC | Có thể không đổi API; cần duyệt crate |
| D. Cổng cố định, base URL lúc build | `NEXT_PUBLIC_HOLOCRON_API_BASE=http://127.0.0.1:8000` | Đơn giản nhất | Cổng bị chiếm thì app hỏng; vẫn cần CORS như A | Như A |

**Khuyến nghị.** Phía frontend: viết code để chạy được với cả A, B, D mà không sửa component, bằng một module cấu hình duy nhất (4.3). Cho spike Tauri: thử B trước, vì B giữ nguyên quy ước §6.0 và kiểm `Host` của §13.1 mà không cần CORS; A là phương án dự phòng. Chọn phương án nào vẫn là quyết định của người dùng sau spike (`tauri-spike-plan.md` mục 7, câu 2), và kế hoạch này không đổi kiến trúc.

Việc phải kiểm trong spike, liên quan tới frontend:
- CSP của Tauri: App Router của Next chèn script nội dòng (dữ liệu hydrate), nên `script-src 'self'` thuần sẽ chặn app. Cần xác nhận Tauri tự chèn hash/nonce cho HTML đóng gói, hoặc phải nới CSP.
- `trailingSlash: true` (xuất `route/index.html`) có giúp Tauri phân giải đường dẫn như `/practice` hay không.
- Với phương án B: initialization script của Tauri có chạy trên trang nạp từ URL ngoài hay không.

### 4.3 Quy ước code để luôn export tĩnh được

Áp dụng từ F1, kể cả khi còn chạy `next dev`:

1. Không Server Actions (`"use server"`), không Route Handlers (`app/**/route.ts`), không `middleware.ts`, không `cookies()`, `headers()`, `draftMode()`, không `revalidate` hay ISR.
2. Không `fetch` phía server: không gọi API trong Server Component, `generateMetadata` hay lúc build. Mọi lời gọi backend chạy trong client component, qua `lib/api.ts`.
3. Không route động `[param]`. ID đi qua query string: `/documents/review?id=…&page=…`, `/practice?blueprint=…&batch=…`. Component đọc `useSearchParams()` luôn nằm trong `<Suspense>` (Next 15 báo lỗi build nếu thiếu khi prerender).
4. Không `next/image` có tối ưu ảnh (dùng `<img>`), không `next/font/google` hay font tải từ mạng.
5. Không đọc `window`, `localStorage`, `navigator` trong lúc render; chỉ đọc trong `useEffect` hay handler, vì trang được prerender lúc build.
6. **Base URL API và token chỉ đọc ở `src/lib/config.ts`**, theo thứ tự:
   1. object do Tauri chèn lúc chạy, `window.__HOLOCRON_RUNTIME__ = {apiBase, token}`, kiểm bằng type guard (không ép kiểu); khai báo kiểu bằng `declare global` trong `src/types/runtime-global.d.ts`;
   2. biến build `NEXT_PUBLIC_HOLOCRON_API_BASE`;
   3. mặc định `/api/holocron` (đi qua rewrite khi `next dev`).

   `lib/api.ts` lấy `apiBase()` và `authHeaders()` từ module này; ngoài `config.ts`, không file nào đọc `process.env`.
7. `next.config.ts`: giữ `rewrites` cho `next dev`. Nhánh đóng gói Tauri thêm `output: "export"` (kèm `trailingSlash` nếu spike xác nhận), bật bằng biến build, và không khai báo `rewrites` trong chế độ đó.
8. Trước khi xin merge mỗi nhánh F, chạy tay một lần `next build` ở chế độ export, ngoài các cổng hiện có (không đề xuất cổng CI mới).

F1 chuyển hai trang động hiện có sang query string: `/ingestion/[itemId]` thành `/ingestion?item=…`, `/blueprints/[blueprintId]` thành `/twins?blueprint=…` (đổi URL của trang có sẵn, mục 9, câu 11).

## 5. Hệ thống thiết kế

### 5.1 Phong cách

"Kho lưu trữ học thuật": nền giấy ngà, chữ mực, một màu nhấn trầm, đường kẻ mảnh, bo góc nhỏ (2–4 px), không đổ bóng dày, không gradient, không emoji, không icon trang trí. Trạng thái luôn có chữ đi kèm, không chỉ dựa vào màu. Trích dẫn nguyên văn dùng font serif để tách khỏi văn bản do hệ thống viết.

### 5.2 Token màu (theo `prefers-color-scheme`, không có nút đổi tay)

| Token | Sáng | Tối | Dùng cho |
|---|---|---|---|
| `--bg` | `#f5f3ee` | `#161513` | nền app |
| `--surface` | `#fffdf9` | `#1e1c19` | thẻ, bảng |
| `--surface-sunken` | `#ece8df` | `#121110` | thanh bên, thanh trạng thái, ô nhập |
| `--border` | `#d8d2c4` | `#3a362f` | đường kẻ |
| `--text` | `#1f1d1a` | `#ece7dd` | chữ chính |
| `--text-muted` | `#5f5a50` | `#a59e90` | chữ phụ |
| `--accent` | `#1f4e5f` | `#7fb3c4` | liên kết, nút chính, focus |
| `--accent-contrast` | `#ffffff` | `#0f1416` | chữ trên nút chính |
| `--accent-soft` | `#e1ecef` | `#1d2c31` | nền mục đang chọn |
| `--quote-rule` | `#8a6d3b` | `#c8a76a` | vạch trái của trích dẫn, nhãn trang |
| `--quote-bg` | `#faf6ec` | `#221f1a` | nền khối trích dẫn |
| `--danger`, `--danger-soft` | `#a4262c`, `#f8e6e6` | `#f08080`, `#3a1d20` | lỗi, ô BLOCKING, phương án sai |
| `--warning`, `--warning-soft` | `#8a5a00`, `#fbf0da` | `#e0b25c`, `#3a2c16` | trang cần xem kỹ, LLM lỗi tạm thời |
| `--success`, `--success-soft` | `#2d6a3e`, `#e5f2e8` | `#7cc492`, `#17301f` | đã xác nhận, phương án đúng |
| `--info`, `--info-soft` | `#2c5d8a`, `#e4edf6` | `#8db8e8`, `#1a2633` | chạy offline, dữ liệu mẫu |

Giữ các biến `--gantt-*` hiện có cho tới khi ADR-005 bước 3 thay Gantt bằng `TimelineArtifact`. Tỉ lệ tương phản chữ thường trên nền ≥ 4.5:1; kiểm lại bằng công cụ khi viết CSS.

### 5.3 Chữ

Chỉ dùng font có sẵn trên máy, đủ dấu tiếng Việt, không tải font qua mạng:

```css
--font-sans: "Segoe UI", "Noto Sans", "DejaVu Sans", "Liberation Sans", system-ui, sans-serif;
--font-serif: "Cambria", "Noto Serif", "DejaVu Serif", "Times New Roman", serif;   /* trích dẫn nguyên văn */
--font-mono: "Cascadia Mono", "Consolas", "Noto Sans Mono", "DejaVu Sans Mono", monospace;   /* mã, hash, seed */
```

- Đặt font có đủ dấu lên trước `system-ui`, vì trên Linux `system-ui` có thể trỏ tới font thiếu dấu chồng (ệ, ở, ữ).
- Cỡ chữ: 12, 13, 14, 15 (thân), 17, 20, 24 px. `line-height` tối thiểu 1.5 cho thân và 1.6 cho trích dẫn, để dấu chồng không chạm dòng trên. Không đặt `overflow: hidden` lên hộp một dòng chứa chữ tiếng Việt.
- Đổi `lang` của `<html>` theo `output_language` thì để sau; trích dẫn giữ ngôn ngữ gốc (CLAUDE.md §4.3).

### 5.4 Khoảng cách, bố cục, chuyển động

- Thang khoảng cách gốc 4 px: `--space-1` 4, `--space-2` 8, `--space-3` 12, `--space-4` 16, `--space-5` 24, `--space-6` 32, `--space-7` 48.
- Khung app ở 1024 × 640 px trở lên:

```
+------------+-------------------------------------------------------------+
| Holocron   |  Tiêu đề trang                       [Ctrl+K Tìm]  [?]      |
|            |-------------------------------------------------------------|
| Môn: MAU101|                                                             |
|  Tài liệu  |   Nội dung chính, tối đa 880 px, cuộn riêng                 |
|  Vault     |                                                             |
|  Cặp KN    |                                                             |
|  Luyện tập |                                                             |
|  Lịch sử   |                                                             |
|  Cài đặt   |                                                             |
| 200 px     |                                                             |
+------------+-------------------------------------------------------------+
| Backend: đã kết nối | LLM: chưa cấu hình | OCR: thiếu Tesseract | Mock   | 28 px
+--------------------------------------------------------------------------+
```

- Dưới 1100 px, thanh bên thu còn 56 px (chỉ chữ viết tắt kèm `title`); màn xem lại trang dùng hai cột: danh sách trang 168 px và vùng dòng phần còn lại. Không có bố cục cho màn dưới 1024 px (desktop).
- Mọi chuyển động (nhấp nháy ô BLOCKING, mở panel) tắt dưới `prefers-reduced-motion: reduce`; chỉ dùng `opacity`/`transform`, tối đa 150 ms.
- Focus luôn thấy: viền 2 px `--accent`, không xoá `outline` mà không có thay thế.

### 5.5 Trích dẫn nguyên văn và số trang

Component `VerbatimQuote` dùng ở mọi chỗ hiện kiến thức (câu hỏi sau khi trả lời, phương án nhiễu lý thuyết có `MisconceptionRef.citation`, cặp khái niệm, chunk, kết quả tự luận):

```
| "…đoạn nguyên văn từ tài liệu, font serif, không cắt bớt…"
|  tr. 12  ·  Tên tài liệu (doc_ab12…)                      [Mở trang]
```

- Vạch trái 3 px `--quote-rule`, nền `--quote-bg`. Nhãn trang `tr. {page_number}` luôn hiện, không ẩn sau hover, không cắt (contract giới hạn quote 8–600 ký tự nên luôn hiện đủ).
- Dấu ngoặc kép là CSS (`::before`, `::after`), không chèn vào chuỗi, để thao tác chép giữ đúng nguyên văn.
- Không hiện quote ở dạng đã sửa chữ, đã dịch hay đã rút gọn. Nội dung không có trích dẫn thì không trình bày như kiến thức đã kiểm chứng (I3).
- Nút "Mở trang" chỉ hiện khi có cách mở file gốc (M12); chưa có thì không hiện nút. Tài liệu đã bị xoá: hiện quote kèm dòng "Tài liệu gốc đã bị xoá" (§12.2, ADR-006 mục 7).

### 5.6 Ba trạng thái LLM và các trạng thái khác

| Trạng thái | Nhận biết (chỉ từ dữ liệu backend) | Hiển thị |
|---|---|---|
| Chạy offline | `RuntimeInfo.demo_mode = true` | Băng `--info` mảnh dưới thanh tiêu đề trên mọi trang: "Đang chạy offline, không có LLM. Nhãn và cặp khái niệm cần nhập tay; câu tính toán chỉ dùng template đã kiểm chứng." Câu chữ theo §5.12 và đề xuất D10(a) (mục 9, câu 7) |
| Chưa cấu hình | khả năng cần LLM có `available = false`, `reason = "llm_not_configured"` | Nút gọi endpoint đó bị vô hiệu, ngay dưới nút hiện `detail_vi` của backend. Không chặn phần khác của màn hình (G6) |
| Lỗi tạm thời, thử lại | 502 với `detail.code = "LLM_UPSTREAM_ERROR"` | Khối `--warning` tại chỗ gọi: `detail.message` của backend và nút "Thử lại". Không tự thử lại |

Các trạng thái chung (component dùng chung trong `src/components/common/`):
- **Đang tải:** khung xương tĩnh (không shimmer khi giảm chuyển động) kèm chữ "Đang tải…" cho trình đọc màn hình (`role="status"`).
- **Trống:** một câu nói vì sao trống và một hành động kế tiếp, ví dụ "Chưa có cặp khái niệm nào đã xác nhận trong chương này. Mở Vault để bôi định nghĩa."
- **Lỗi:** `role="alert"`, thông điệp của backend (`detail`), và một hành động theo mã: 409 lệch version thì "Tải lại bản mới nhất"; 409 `CapabilityError` thì `message` và không có nút thử lại; 503 (kho bận) thì "Thử lại"; mất kết nối thì "Không kết nối được backend ở {apiBase}".
- **Khả năng không khả dụng:** thông báo ở đúng màn hình dùng khả năng đó kèm `detail_vi` (§5.12). Với `ocr`, trang nhập tài liệu báo trước rằng trang scan sẽ `failed`.

### 5.7 Phím tắt

| Phím | Ngữ cảnh | Hành động |
|---|---|---|
| `1`–`4` hoặc `A`–`D` | câu MCQ4 chưa nộp | chọn phương án tương ứng |
| `Enter` | câu MCQ4 đã chọn | nộp; khi đã nộp thì sang câu kế tiếp |
| `Ctrl+Enter` | ô trả lời tự luận | nộp câu trả lời |
| `N` | đã nộp | câu kế tiếp |
| `C` | đã nộp | mở và đưa focus tới trích dẫn của câu |
| `Ctrl+K` | mọi nơi | hộp lệnh: đi tới màn hình, tìm trong Vault của môn đang chọn |
| `Esc` | hộp thoại, hộp lệnh, ô đang sửa | đóng hoặc huỷ sửa |
| `?` | mọi nơi trừ ô nhập | bảng phím tắt |
| `Enter` / `Esc` | ô bảng ở TableAuditor, dòng ở màn xem lại trang | lưu / huỷ (giữ hành vi hiện có của TableAuditor) |

Phím một ký tự (`1`–`4`, `A`–`D`, `N`, `C`, `?`) không có tác dụng khi focus nằm trong `input`, `textarea`, `select` hoặc phần tử `contenteditable`, để gõ tự luận không chọn nhầm phương án. Ánh xạ phím là hàm thuần ở `src/lib/shortcuts.ts`, có test.

## 6. Kế hoạch chia nhánh F1 → F5

Mỗi giai đoạn một nhánh tách từ nhánh trước (xếp chồng); nhánh đầu tách từ `develop` sau khi b3 và b6 đã merge, hoặc dùng mock cho phần chưa merge. Theo quy trình của người dùng, tối đa 3 nhánh chờ duyệt cùng lúc, nên F4 chỉ bắt đầu khi F1 đã merge. Mỗi nhánh cập nhật `CHANGELOG.md` mục `[Unreleased]` và chạy `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`, cùng một lần build export chạy tay (mục 4.3).

### F1 `feat/frontend-f1-shell`: khung app, cấu hình, thanh trạng thái

| File | Việc |
|---|---|
| `src/lib/config.ts`, `src/lib/config.test.ts` | `apiBase()`, `authHeaders()`, `USE_MOCKS`; test thứ tự ưu tiên, từ chối object chèn sai dạng, chỉ gắn `X-Holocron-Token` khi có token |
| `src/types/runtime-global.d.ts` | khai báo `window.__HOLOCRON_RUNTIME__` |
| `src/lib/api.ts` (sửa) | dùng `config.ts`; `interface HolocronApi`; `export const api`; sửa `describeError` cho `CapabilityError`, `LLM_UPSTREAM_ERROR` |
| `src/lib/errors.ts`, `src/lib/errors.test.ts` | phân loại lỗi → hành động UI (tải lại, thử lại, khả năng thiếu, mất kết nối) |
| `src/lib/runtime.ts`, `src/lib/runtime.test.ts` | `capabilityOf(runtime, name)`; trạng thái LLM (mục 5.6); backend cũ không có `capabilities` thì trả "không rõ" và không chặn tính năng offline |
| `src/lib/shortcuts.ts`, `src/lib/shortcuts.test.ts` | ánh xạ phím, bỏ qua khi đang ở ô nhập |
| `src/types/contract-pending.ts` | `Capability`, `CapabilityName`, `CapabilityReason`, `CapabilityError`, `RuntimeInfoV2` (M1, M2) |
| `src/types/holocron.ts` (sửa) | `ApiErrorBody` thêm hai dạng `detail` đã duyệt ở §6.0 |
| `src/mocks/index.ts`, `src/mocks/store.ts`, `src/mocks/fixtures/runtime.ts`, `src/mocks/fixtures/errors.ts`, `src/mocks/fixtures.test.ts` | khung mock (mục 3.2) |
| `src/app/globals.css` (sửa) | token mục 5 |
| `src/app/layout.tsx` (sửa) | khung app, mô tả trang không còn nói "môn Hệ điều hành" |
| `src/components/shell/AppShell.tsx`, `Sidebar.tsx`, `StatusBar.tsx`, `OfflineBanner.tsx`, `CommandPalette.tsx`, `ShortcutHelp.tsx` (+ `.module.css`) | khung, thanh trạng thái, băng offline, `Ctrl+K`, `?` |
| `src/components/common/VerbatimQuote.tsx`, `LoadState.tsx`, `ErrorNotice.tsx`, `CapabilityNotice.tsx` (+ css) | thành phần dùng chung (mục 5.5, 5.6) |
| `src/app/ingestion/page.tsx`, `src/app/twins/page.tsx` (thay hai trang động) | đọc ID từ query string trong `<Suspense>`; xoá `src/app/ingestion/[itemId]/`, `src/app/blueprints/[blueprintId]/` |

### F2 `feat/frontend-f2-documents-vault`: môn, nhập tài liệu, xem lại trang, Vault

| File | Việc |
|---|---|
| `src/lib/documents.ts`, `src/lib/documents.test.ts` | nhãn tiếng Việt cho `ReviewReason`, `FailureReason`, `LineFlag` (bảng `Record<…, string>` để TypeScript bắt thiếu mã); `canConfirmPage` (ok hoặc needs_review, chưa xác nhận, có ít nhất một dòng có văn bản; khớp điều kiện 409 của §6.5); `canConfirmDocument` (không còn trang `pending`); `buildLineEdits` (chỉ gửi dòng đã đổi); `pollDelay` (1,5 s khi `processing`, dừng khi hết `pending`, giãn khi lỗi liên tiếp); `mismatchesByLine` |
| `src/lib/vault.ts`, `src/lib/vault.test.ts` | dựng query cho chunks/search; phân trang bằng `offset`/`limit` của backend |
| `src/types/contract-pending.ts` (thêm) | `Subject`, `ExamFormat`, `PageLinesPatch`, `PageConfirmRequest`, `DocumentConfirmRequest`; `DocumentImport.chapter_code`, `LOW_TEXT_SCORE` cho tới khi backend có (M3–M5) |
| `src/mocks/fixtures/subjects.ts`, `documents.ts`, `vault.ts` | M3–M5 |
| `src/components/documents/ImportForm.tsx`, `DocumentList.tsx`, `PageStrip.tsx`, `PageReview.tsx`, `LineEditor.tsx`, `ReviewReasons.tsx`, `TokenMismatchList.tsx`, `OcrMissingHelp.tsx` (+ css) | form có `chapter_code` bắt buộc; danh sách trang có trạng thái bằng chữ; dòng cờ thấp tô `--warning`; hai giá trị khi token lệch; hướng dẫn cài Tesseract khi `ocr` không khả dụng hoặc trang `failed` vì `OCR_ENGINE_MISSING`; trang `failed` có nút "Xoá tài liệu để nhập lại" |
| `src/components/vault/ChunkList.tsx`, `ChunkView.tsx`, `ChunkLabelsForm.tsx`, `VaultSearch.tsx` (+ css) | xem chunk, sửa nhãn, tìm |
| `src/app/subjects/page.tsx`, `src/app/documents/page.tsx`, `src/app/documents/review/page.tsx`, `src/app/vault/page.tsx` | trang |

Màn xem lại trang chỉ hiện văn bản theo dòng, chưa có ảnh trang cho tới khi M12 được quyết. Số liệu như `ocr_score`, `coverage_ratio` hiện nguyên giá trị backend trả, không quy đổi, không so ngưỡng ở frontend (§5.7).

### F3 `feat/frontend-f3-concept-pairs`: cặp khái niệm

| File | Việc |
|---|---|
| `src/lib/pairs.ts`, `src/lib/pairs.test.ts` | `selectionToQuote(rawText, start, end)` cắt đúng lát của `raw_text` theo vùng bôi, chỉ bỏ khoảng trắng hai đầu (backend vẫn kiểm I3); `termSegments(quote, term_spans)` tách đoạn để tô `term` theo vị trí backend trả; `canPropose(runtime)` |
| `src/types/contract-pending.ts` (thêm) | `ConceptPairProposeRequest`, `ConceptPairProposeResponse` nếu b6 chưa merge (M6) |
| `src/components/pairs/PairList.tsx`, `PairCard.tsx`, `PairEditor.tsx`, `ChunkHighlighter.tsx`, `ProposeButton.tsx` (+ css) | danh sách theo chương và trạng thái; sửa `term`, bôi lại định nghĩa; nút đề xuất bằng LLM theo ba trạng thái ở mục 5.6; báo `rejected_count` là số đề xuất bị loại vì không khớp nguyên văn |
| `src/app/pairs/page.tsx` | trang |

### F4 `feat/frontend-f4-practice`: luyện tập và kết quả

| File | Việc |
|---|---|
| `src/lib/practice.ts`, `src/lib/practice.test.ts` | `answerOutcome(selectedKey, twin)` chỉ so khoá đã chọn với `correct_key`; `revealPolicy(twin, answered)`: trước khi nộp, ẩn `correct_key`, badge misconception, `citation`, `solution_explanation_vi`, và với ESSAY ẩn cả `essay.points` (§6.9); `defaultSlotFormat(subject)` chỉ để điền sẵn ô chọn hình thức, luôn gửi lựa chọn tường minh của người dùng; `optionKeyFromShortcut` |
| `src/lib/essay.ts`, `src/lib/essay.test.ts` | `essayResultView(result)` dựng câu "Khớp {matched_count}/{total_points} ý", danh sách ý còn thiếu (nguyên văn), trích dẫn và câu giới hạn bắt buộc của §5.14. **Test bắt buộc:** với mọi fixture và một loạt kết quả dựng sẵn, mọi chuỗi hiển thị không chứa `%`, không chứa "điểm", không chứa thang dạng "/10" hay "/100", không chứa giá trị nào của `ngram_coverage`; hai số duy nhất xuất hiện là `matched_count` và `total_points` do backend trả; câu giới hạn luôn có mặt |
| `src/lib/artifacts.ts`, `src/lib/artifacts.test.ts` | chọn component theo `artifacts[].kind`; twin cũ chưa có `artifacts` thì dùng `gantt` (không rẽ nhánh theo topic) |
| `src/types/contract-pending.ts` (thêm) | `BlueprintCreate`, `TheorySlotSpec`, `ExamBlueprint`, `BlueprintSlot`, `TwinQuestionV2`, `TheoryTrace`, `ComputationalTrace`, `SolutionArtifact`, `EssaySpec`, `EssayPoint`, `EssayGradeRequest`, `EssayGradeResult` và các model con, `TwinBatchV2` (M7–M9) |
| `src/mocks/fixtures/blueprints.ts`, `twins.ts`, `essay.ts` | M7–M9 |
| `src/components/practice/BlueprintBuilder.tsx`, `PracticeSession.tsx`, `McqQuestion.tsx`, `EssayQuestion.tsx`, `AnswerReveal.tsx`, `MisconceptionBadge.tsx`, `EssayResult.tsx`, `ReviewTicketNotice.tsx` (+ css) | chọn chương và số slot, hình thức; ẩn lựa chọn ESSAY khi `essay_grading_offline` không khả dụng và không tự đổi sang MCQ4 (§1.5); một câu mỗi lần; badge cho phương án sai sau khi nộp, kèm trích dẫn của phương án nhiễu lý thuyết; `review_tickets` hiện lý do và liên kết sang màn cặp khái niệm |
| `src/components/twins/TimelineArtifact.tsx`, `TableArtifact.tsx` | vẽ artifact theo `kind`; `GanttChart` giữ cho twin cũ |
| `src/app/practice/page.tsx` | trang |

Màn kết quả MCQ hiện từng câu đúng/sai. Có hiện tổng số câu đúng của phiên hay không thì chờ người dùng quyết (mục 9, câu 3); tới khi đó không hiện con số tổng do frontend đếm.

### F5 `feat/frontend-f5-history-settings`: lịch sử và cài đặt

| File | Việc |
|---|---|
| `src/lib/history.ts`, `src/lib/history.test.ts` | định dạng thời gian bằng `Intl.DateTimeFormat("vi-VN")`; giữ thứ tự backend trả (mới nhất trước) |
| `src/lib/settings.ts`, `src/lib/settings.test.ts` | nhóm khả năng theo màn hình bị ảnh hưởng (bảng §11.4) để hiển thị; kiểm `PATCH /v1/subjects` có đủ `expected_version` |
| `src/types/contract-pending.ts` (thêm) | `BlueprintSummary`, `TwinBatchSummary` (M10) |
| `src/mocks/fixtures/history.ts` | M10 |
| `src/components/history/BlueprintHistory.tsx`, `BatchList.tsx` (+ css) | danh sách bộ đề và lượt sinh; mở lại một lượt ở màn luyện tập |
| `src/components/settings/CapabilityTable.tsx`, `DataEgressNotice.tsx`, `SubjectSettings.tsx`, `TesseractHelp.tsx` (+ css) | bảng khả năng kèm `detail_vi`; thông báo dữ liệu rời máy của §13.1; đổi `exam_format`; hướng dẫn cài Tesseract và `vie.traineddata` |
| `src/app/history/page.tsx`, `src/app/settings/page.tsx` | trang |

Lịch sử lượt làm bài không làm trong F5 vì chưa có contract (M11).

### Logic thuần và test vitest: tổng hợp

| Module `src/lib/` | Nội dung test chính |
|---|---|
| `config.ts` | thứ tự nguồn base URL, type guard cho object Tauri chèn, header token |
| `errors.ts` | mọi dạng `detail` của §6.0 (chuỗi, danh sách 422, `{message, issues}`, `CapabilityError`, `LLM_UPSTREAM_ERROR`), 409 lệch version, 503, mất kết nối |
| `runtime.ts` | trạng thái LLM, khả năng thiếu, backend cũ không có `capabilities` |
| `shortcuts.ts` | ánh xạ phím theo ngữ cảnh, bỏ qua khi đang ở ô nhập |
| `documents.ts` | điều kiện xác nhận khớp §6.5; chỉ gửi dòng đã đổi; lịch hỏi trạng thái |
| `vault.ts`, `pairs.ts` | dựng query; cắt vùng bôi đúng lát; tô `term` theo `term_spans` |
| `practice.ts` | so khoá; ẩn đáp án, trích dẫn, ý tự luận trước khi nộp |
| `essay.ts` | **ESSAY không bao giờ hiện điểm hay phần trăm** (bắt buộc) |
| `artifacts.ts`, `history.ts`, `settings.ts` | chọn component theo `kind`; định dạng; nhóm khả năng |
| `src/mocks/fixtures.test.ts` | fixture tuân thủ I2, I3 và validator của contract (mục 3.3) |

`gate.ts`, `gate.test.ts` (I5, `canApprove`) và `twin.ts`, `twin.test.ts` giữ nguyên và phải xanh.

## 7. Ràng buộc chung khi viết code

- Frontend không tính con số nào của nội dung câu hỏi, đáp án, lời giải hay kết quả chấm (I1). Đúng/sai của MCQ là phép so khoá. Các số đếm như số trang đã xác nhận lấy từ `DocumentSummary`, không đếm lại ở client.
- Không component, CSS hay bảng hằng nào gắn với một môn, chương hay topic (CLAUDE.md §1). Bảng nhãn tiếng Việt chỉ theo mã enum của contract (`ReviewReason`, `CapabilityName`…).
- Chuỗi giao diện bằng tiếng Việt; tên định danh bằng tiếng Anh (CLAUDE.md §4.3).
- Không `any`, `@ts-ignore`, `@ts-expect-error`, `as unknown as`, `biome-ignore`; `as T` chỉ ở chỗ parse JSON trong `lib/api.ts` (CLAUDE.md §4.2).
- `editor_id` dùng hằng `LOCAL_EDITOR_ID` đã có.

## 8. Đề xuất dependency (chưa cài; chuyển sang `proposed-diffs.md`)

| Gói | Giấy phép | Lý do | Khi nào cần | Nếu không duyệt |
|---|---|---|---|---|
| `pdfjs-dist` | Apache-2.0 | Render ảnh trang để đặt dòng theo `bbox` ở màn xem lại (§5.7) và mở trích dẫn đúng trang, tô `verbatim_quote` (§12.2). Worker phải đóng gói cục bộ, không tải từ CDN | Sau khi có cách lấy file gốc (M12) | Màn xem lại chỉ có văn bản theo dòng; trích dẫn chỉ hiện quote và số trang |
| `@testing-library/react` + `jsdom` (hoặc `happy-dom`) | MIT | Test component cho phím tắt, ẩn đáp án trước khi nộp, nút bị vô hiệu theo khả năng. Vitest phải nhận thêm `*.test.tsx` | Từ F1 nếu muốn test ở mức component | Logic đã tách ra `src/lib/` và có test; phần nối vào component kiểm bằng tay |
| `katex` | MIT | Render LaTeX trong `stem` của câu tính toán (Xác suất thống kê, ARM) | Luồng A, khi có twin tính toán ngoài fixture | Hiện LaTeX dạng chữ như hiện nay; câu lý thuyết của lát cắt dọc không cần |
| `@tauri-apps/api` | MIT/Apache-2.0 | Chỉ khi chọn phương án C (IPC) ở mục 4.2 | Sau spike | Phương án B và A dùng initialization script, không cần gói này |

Mỗi gói ghim version chính xác lúc cài và kiểm lại giấy phép trên registry ngày cài (CLAUDE.md §2.2).

## 9. Câu hỏi cần người dùng quyết

1. **Nối WebView với backend dưới Tauri:** thử phương án B (backend phục vụ bản export, cùng origin) trước, A (CORS) làm dự phòng? Cả hai đều cần duyệt: B đổi kiến trúc backend, A đổi §6.0 và cần ADR.
2. **Kiểu của contract chưa có trong backend:** đặt ở `src/types/contract-pending.ts` rồi chuyển sang `holocron.ts` khi backend có, hay thêm thẳng vào `holocron.ts` kèm chú thích "chưa có ở backend" (trái nguyên tắc khớp 1:1)?
3. **Tổng kết phiên luyện tập MCQ:** frontend có được đếm số câu đúng trong phiên để hiện "Đúng x/y câu" không? Đây là số về câu trả lời của người học, không phải nội dung câu hỏi, nhưng §5.14 thêm `matched_count` riêng "để frontend không tự đếm". Mặc định của kế hoạch: không hiện tổng.
4. **Lịch sử lượt làm bài:** spec ghi là việc sau MVP. Ở F5 chỉ làm lịch sử bộ đề và lượt sinh (§6.7), hay cần một contract mới cho lượt làm bài (backend lưu) hoặc cho phép lưu ở `localStorage` của WebView?
5. **File gốc cho ảnh trang và trích dẫn một chạm:** duyệt thêm endpoint (ví dụ `GET /v1/documents/{id}/file`) cùng `pdfjs-dist`, hay để sau MVP và chỉ xem lại theo văn bản?
6. **`RuntimeInfo` thiếu hai thông tin UI cần:** chế độ lưu trữ (in-memory thì mất Vault khi tắt backend, người dùng đang ôn thật) và tên bên nhận dữ liệu LLM mà §13.1 yêu cầu UI nêu (nhưng `/v1/runtime` không được lộ endpoint). Thêm field (đổi contract §5.12), hay ghi cố định trên UI?
7. **Câu chữ băng thông báo:** dùng "Đang chạy offline, không có LLM" theo đề xuất D10(a) thay cho "chế độ demo"?
8. **Cờ mock:** đồng ý tên `NEXT_PUBLIC_HOLOCRON_MOCKS` và nguyên tắc chỉ mock endpoint chưa có, endpoint đã có luôn gọi backend thật kể cả khi bật cờ?
9. **Danh sách môn khi chưa có `/v1/subjects`:** tạm lấy `subject_code` từ `GET /v1/documents`, và tạo môn chỉ bằng cách nhập tài liệu đầu tiên?
10. **Ý nghĩa phím `C` và `Ctrl+K`:** `C` mở trích dẫn của câu vừa nộp; `Ctrl+K` là hộp lệnh gồm điều hướng và tìm trong Vault. Có cần thêm phím cho màn xem lại trang (dòng trước, dòng sau, xác nhận trang) không?
11. **Đổi URL của trang có sẵn:** đồng ý chuyển `/ingestion/[itemId]` thành `/ingestion?item=…` và `/blueprints/[blueprintId]` thành `/twins?blueprint=…` ngay ở F1 để giữ được static export?
12. **Thứ tự merge backend:** F2 và F3 cần b3 và b6 vào `develop` trước (b3 có bộ nhập và Vault, b6 có `propose` và kiểu `Capability`). Nếu chưa merge, F2 và F3 dùng mock cho các endpoint đó; chấp nhận được không?
