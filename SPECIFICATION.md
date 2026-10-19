# Holocron — Technical Specification

| Trường | Giá trị |
|---|---|
| Trạng thái | DRAFT v0.2 — Tuần 2 / Ngày 1 |
| Phạm vi | Deterministic Core (Golden Path: Hệ Điều Hành — CPU Scheduling) + Ingestion Gate |
| Tài liệu liên quan | `backend/holocron_core/schemas.py`, `sandbox.py`, `pipeline.py`, `ingestion_gate.py`, `docs/adr/` |
| Quy ước | Từ khoá PHẢI / NÊN / CÓ THỂ theo nghĩa RFC 2119 (MUST / SHOULD / MAY) |

## Mục lục

0. [Tóm tắt điều hành](#0-tóm-tắt-điều-hành)
1. [Mục tiêu, phi mục tiêu & ràng buộc](#1-mục-tiêu-phi-mục-tiêu--ràng-buộc)
2. [Thuật ngữ](#2-thuật-ngữ)
3. [Kiến trúc tổng thể](#3-kiến-trúc-tổng-thể)
4. [Mô hình dữ liệu](#4-mô-hình-dữ-liệu)
5. [Knowledge Vault & Deep Checkpoint](#5-knowledge-vault--deep-checkpoint)
6. [Ingestion Pipeline & Ingestion Gate](#6-ingestion-pipeline--ingestion-gate)
7. [Blueprint Extraction & AI Self-Solve](#7-blueprint-extraction--ai-self-solve)
8. [Executable Template Contract](#8-executable-template-contract)
9. [Sandbox Runtime](#9-sandbox-runtime)
10. [Retry Budget & Fallback](#10-retry-budget--fallback)
11. [Twin Question Assembly](#11-twin-question-assembly)
12. [API Surface](#12-api-surface)
13. [Bảo mật & Threat Model](#13-bảo-mật--threat-model)
14. [Quan sát hệ thống (Observability)](#14-quan-sát-hệ-thống-observability)
15. [Chiến lược kiểm thử & Định nghĩa "Done"](#15-chiến-lược-kiểm-thử--định-nghĩa-done)
16. [Lộ trình Tuần 2 → Tuần 4](#16-lộ-trình-tuần-2--tuần-4)
17. [Rủi ro & câu hỏi mở](#17-rủi-ro--câu-hỏi-mở)
18. [Phụ lục](#18-phụ-lục)

---

## 0. Tóm tắt điều hành

Holocron tách hai tầng: **Deterministic Core** (mọi con số đều do code chạy trong sandbox tính ra, LLM không bao giờ tính nhẩm) và **Knowledge Vault UI** (quản trị tài liệu đa môn, trích dẫn 1-click). Tài liệu này đặc tả Core cho Golden Path của MVP Tuần 3.

Bất biến cốt lõi (PHẢI luôn đúng):

- **I1 — Không số nào do LLM tự viết.** Đáp án và mọi phương án nhiễu của câu tính toán đến từ `solve()` / `make_distractors()` chạy trong Sandbox.
- **I2 — Mỗi phương án sai ánh xạ đúng một misconception.** `TwinQuestion` không hợp lệ nếu thiếu `distractor_to_misconception_map` cho đủ 3 phương án sai.
- **I3 — Mọi câu hỏi có trích dẫn kiểm chứng được.** `verbatim_quote` là chuỗi con của `raw_text` chunk gốc.
- **I4 — Tái lập hoàn toàn.** `(template_sha256, seed)` ⇒ cùng đề, cùng đáp án, cùng thứ tự phương án.
- **I5 — Không đề nào vượt Ingestion Gate khi còn lỗi BLOCKING.**

## 1. Mục tiêu, phi mục tiêu & ràng buộc

### 1.1 Mục tiêu (Tuần 3 MVP)
- Nhận ảnh/PDF đề cũ không đáp án → Blueprint → sinh đề song sinh MCQ4 / NUMERICAL cho topic `os.cpu_sched.rr.*`, `os.cpu_sched.sjf.*`, `os.paging.{fifo,lru}.*`, `os.deadlock.banker.*`.
- Thời gian sinh một câu tính toán p95 ≤ 3 s (không tính thời gian LLM viết template).

### 1.2 Phi mục tiêu
- Tự luận dài, chấm điểm tự luận.
- Tham số hoá câu hỏi phụ thuộc hình vẽ (gắn `non_cloneable`).
- Bounding-box pixel cho trích dẫn (thay bằng `page_number` + `verbatim_quote`).

### 1.3 Ràng buộc
- Backend: FastAPI + Pydantic v2, Python 3.12. Frontend: Next.js + PDF.js.
- Lưu trữ: SQLite (dev) → PostgreSQL (Tuần 4). Không phụ thuộc dịch vụ sandbox trả phí.

## 2. Thuật ngữ

| Thuật ngữ | Định nghĩa |
|---|---|
| Knowledge Chunk | Đoạn tài liệu đã bóc tách, gắn `THEORY` / `PRACTICE` và con trỏ liên kết |
| Soft-Tagging | Gắn nhãn thay vì tách DB, giữ liên kết định lý ↔ ví dụ |
| Exam Blueprint | Ma trận đề trích từ đề cũ: slot, dạng câu, chương, archetype nhiễu |
| Executable Template | Script Python do LLM sinh, tuân thủ hợp đồng §8 |
| Twin Question | Câu hỏi đẳng cấu sinh từ template + seed |
| Misconception | Lỗi nhận thức có mã (`RR_TREATED_AS_FCFS`, …) |
| Ingestion Gate | Cổng kiểm duyệt người dùng trước khi dữ liệu đề vào pipeline |

## 3. Kiến trúc tổng thể

### 3.1 Sơ đồ thành phần
> Chèn sơ đồ Mermaid: Upload → VLM Extract (dual-pass) → Ingestion Gate → Self-Solve → Blueprint → Template Author (LLM) → AST Guard → Sandbox → Twin Assembler → Exam Store → Vault UI.

### 3.2 Ranh giới tin cậy
- **Không tin cậy:** output VLM, output LLM (kể cả template), nội dung PDF người dùng tải lên.
- **Tin cậy:** schemas, AST Guard, sandbox runner, template pre-computed đã kiểm chứng.

### 3.3 Luồng dữ liệu chính (sequence)
> Chèn sequence diagram cho: (a) ingest đề cũ, (b) sinh 1 twin, (c) người dùng sửa ô bảng.

## 4. Mô hình dữ liệu

Nguồn chân lý: `backend/holocron_core/schemas.py`. Phần này chỉ mô tả ngữ nghĩa và bất biến.

### 4.1 `KnowledgeChunk`
- Bất biến: `category=THEORY` ⇒ có `#theory`, không có `#practice_example` (và ngược lại); `verbatim_quote ⊂ raw_text` (chuẩn hoá khoảng trắng); không tự liên kết.

### 4.2 `ExamBlueprint` / `BlueprintSlot`
- `len(slots) == total_questions`, `slot_index` liên tục 1..N.
- `non_cloneable ⇔ non_cloneable_reason != null`.
- `self_solve.status ∈ {NO_MATCH, AMBIGUOUS}` ⇒ không được `AUTO_PASS`.
- Trường dẫn xuất: `chapter_distribution`, `format_distribution`, `cloneable_ratio`, `mean_mapping_confidence`.

### 4.3 `QuestionTemplate`
- `generator_source` PHẢI parse được và định nghĩa đủ 5 entrypoint top-level, có ≥ 1 `assert`.
- Đúng 3 `MisconceptionSpec`, mã không trùng. `source_sha256` là khoá tái lập.

### 4.4 `TwinQuestion`
- MCQ4: đủ A–D, 4 nội dung khác nhau, `correct_key ∉ distractor_to_misconception_map`, map phủ đúng 3 phương án còn lại.
- NUMERICAL: không có options; bắt buộc `numerical_answer` với `tolerance_abs`.

### 4.5 Lược đồ lưu trữ (Tuần 4)
> Bảng: `documents`, `chunks`, `chunk_links`, `blueprints`, `blueprint_slots`, `templates`, `twins`, `ingestion_items`, `patch_audits`. Chỉ mục: `(topic_key)`, `(template_sha256, seed)` UNIQUE.

## 5. Knowledge Vault & Deep Checkpoint

### 5.1 Bóc tách & phân tầng chunk
> Quy tắc cắt chunk theo heading, ngưỡng token, heuristic phân loại THEORY/PRACTICE, cách sinh `linked_chunk_ids` (ví dụ → định lý được trích dẫn).

### 5.2 Trích dẫn 1-click
- Client gọi PDF.js `PDFFindController` với `query = verbatim_quote`, `pageNumber` cố định; highlight trên Split-View.
- Fallback khi không tìm thấy (ligature, gạch nối cuối dòng): tìm theo 8 từ đầu của quote.

### 5.3 Multi-subject Vault (Tuần 4)
> CRUD metadata môn học / tài liệu; không nằm trên Golden Path.

## 6. Ingestion Pipeline & Ingestion Gate

### 6.1 Trích xuất VLM dual-pass
Hai lượt đọc độc lập (khác prompt hoặc khác nhiệt độ) cho mỗi bảng; kết quả có `confidence` từng ô.

### 6.2 Năm tín hiệu phát hiện đọc sai

| Mã | Tín hiệu | Mức |
|---|---|---|
| `DUAL_PASS_MISMATCH` | Hai lượt đọc khác nhau | BLOCKING |
| `LOW_CONFIDENCE` | confidence < 0.85 | WARNING |
| `HEADER_MISMATCH` / `NOT_A_NUMBER` / `OUT_OF_RANGE` / `DUPLICATE_LABEL` / `MISSING_CELL` | Vi phạm schema bảng của topic | BLOCKING (header: WARNING) |
| `NOT_IN_TEXT_LAYER` | Số không có trong text layer (chỉ khi PDF có text) | WARNING |
| `SELF_SOLVE_MISMATCH` | Tự giải không khớp đúng một phương án | BLOCKING |
| `LATEX_UNBALANCED` | `$` lẻ hoặc `{}` lệch | BLOCKING |

### 6.3 Sửa trực tiếp trên UI
- UI tô màu ô theo `(row, col)` của issue; ô `DUAL_PASS_MISMATCH` hiện dropdown `alternatives`.
- `PATCH /v1/ingestion/items/{id}/table` với `expected_version` (optimistic concurrency, 409 khi lệch).
- Ô đã sửa: `verified_by_human=true`, bỏ qua kiểm tra dual-pass/confidence; mọi thay đổi ghi `PatchAudit`.
- Sau mỗi patch: chạy lại Self-Solve bằng template đã kiểm chứng ở chế độ `params_override`, tính lại `GateReport`.
- `POST …/non-cloneable` để gắn cờ câu phụ thuộc hình vẽ; `POST …/approve` trả 409 nếu còn BLOCKING.

## 7. Blueprint Extraction & AI Self-Solve

### 7.1 Thuật toán
1. Với mỗi câu đã qua Gate: phân loại topic_key (LLM + retrieval từ `#theory`).
2. Câu tính toán: Self-Solve = chạy `solve()` của template đã kiểm chứng trên dữ liệu đề thật (Sandbox, `params_override`). Câu lý thuyết: LLM giải với bắt buộc trích dẫn chunk.
3. So khớp đáp án với các phương án (dung sai `10^-decimals / 2`): `MATCHED` / `NO_MATCH` / `AMBIGUOUS`.
4. Với từng phương án sai: thử tái tạo bằng `make_distractors()`; khớp ⇒ gán `misconception_code` với `reproduction_confidence`.
5. `mapping_confidence` = trung bình có trọng số (topic classifier, self-solve, reproduction).

### 7.2 Chính sách độ tin cậy
> Ngưỡng `mapping_confidence` để AUTO_PASS (đề xuất ≥ 0.85), dưới ngưỡng ⇒ NEEDS_REVIEW.

## 8. Executable Template Contract

### 8.1 Entry points (bắt buộc, top-level)
```python
def generate_params(seed: int) -> dict          # dùng random.Random(seed), không dùng random toàn cục
def validate(params: dict) -> None              # chỉ chứa assert có nhãn ASSERT_ID
def solve(params: dict) -> dict                 # {"value", "display", "explanation_vi", ...}
def make_distractors(params: dict, solution: dict) -> list[dict]   # đúng 3 phần tử
def render_stem(params: dict) -> str            # Markdown + LaTeX
```

### 8.2 Quy tắc assert
- Mỗi `assert` PHẢI có nhãn thuộc một trong 4 nhóm: `VALIDITY_*`, `TIE_*`, `NONDEG_*`, `DISTINCT_*`.
- Nhóm `TIE_*` loại bỏ mọi trường hợp đáp án phụ thuộc quy ước (ví dụ RR: tiến trình đến đúng lúc tiến trình khác hết quantum).
- Nhóm `DISTINCT_*` đảm bảo 4 giá trị sau làm tròn đôi một cách nhau ≥ `min_option_gap`.

### 8.3 Số học
- Dùng `fractions.Fraction` cho mọi phép chia; chỉ làm tròn ở bước hiển thị.

### 8.4 Ví dụ chuẩn
- `backend/holocron_core/templates/os_rr_avg_waiting.py` (Round Robin, 3 misconception: `RR_TREATED_AS_FCFS`, `WAIT_EQUALS_FIRST_RESPONSE`, `WAIT_CONFUSED_WITH_TURNAROUND`).

### 8.5 Prompt hợp đồng cho LLM
> Chèn system prompt sinh template + few-shot (chính là file ví dụ ở 8.4) + định dạng feedback lỗi ở §10.3.

## 9. Sandbox Runtime

### 9.1 Phòng thủ nhiều lớp
| Lớp | Cơ chế | Chặn |
|---|---|---|
| L1 AST Guard | Whitelist import (`random, math, fractions, itertools, functools, collections, statistics`); cấm builtins nguy hiểm, thuộc tính `_*`, `gi_/f_/tb_/co_*`, `.format`; cấm `class/try/with/global/async`; giới hạn số mũ, kích thước literal, số node | `os`, `sys`, `subprocess`, socket, file I/O, introspection escape |
| L2 Runtime | Tiến trình con riêng; builtins rút gọn; `__import__` chỉ trả module proxy (không lộ module con như `fractions.sys`) | Escape qua module graph |
| L3 OS limits | `RLIMIT_AS` 256 MB, `RLIMIT_CPU` 2 s, `RLIMIT_FSIZE` 0, wall-clock ≤ 2 s, env rỗng, cwd tạm | Vòng lặp vô hạn, memory bomb, ghi file |
| L4 (production) | nsjail/gVisor, `--network=none`, seccomp | Phần còn lại |

### 9.2 Kết quả & phân loại lỗi
`ok | ast_rejected | assertion_failed | runtime_error | contract_error | timeout | memory_exceeded | crashed`.

### 9.3 Hiệu năng
> Đo: ~40–60 ms/lần spawn. Kế hoạch tối ưu: pool tiến trình pre-fork (Tuần 3), chạy song song nhiều seed.

## 10. Retry Budget & Fallback

### 10.1 Ngân sách
| Ngân sách | Giá trị | Trừ khi |
|---|---|---|
| Seed re-roll / template | 40 | `assertion_failed` (lỗi bộ số, không gọi LLM) |
| LLM attempt / slot | 3 | lỗi code, hoặc 0/40 seed hợp lệ (`ill_posed`) |

### 10.2 Cây quyết định
1. Slot `non_cloneable` ⇒ ReviewTicket ngay.
2. Lặp ≤ 3 lượt LLM; mỗi lượt re-roll ≤ 40 seed theo dãy tất định `base_seed + k·7919`.
3. Hết 3 lượt ⇒ template `precomputed_verified` cùng `topic_key` ⇒ twin `AUTO_FALLBACK`.
4. Không có fallback ⇒ `ReviewTicket`, slot `NEEDS_REVIEW`.

### 10.3 Định dạng feedback cho LLM
- `AST_REJECTED: dòng N: …`, `RUNTIME_ERROR (stage=…, line=…): …`, `ILL_POSED: 0/40 seed … Assert hay vi phạm nhất: …` — LLM được yêu cầu nới miền sinh biến, **không** xoá assert.

## 11. Twin Question Assembly
- Trộn phương án bằng `random.Random(f"{template_sha256}:{seed}")` ⇒ tái lập (I4).
- Gắn `citation` từ chunk `#theory` của template; `trace` lưu thời gian sandbox, số lượt LLM, số lần re-roll, provenance.
- Mẫu payload: `backend/examples/twin_question_rr_mcq.json`, `twin_question_rr_numerical.json`.

## 12. API Surface

| Method | Path | Mô tả |
|---|---|---|
| POST | `/v1/ingestion/items` | Tạo item từ kết quả VLM, trả `GateReport` |
| GET | `/v1/ingestion/items/{id}` | Lấy item |
| PATCH | `/v1/ingestion/items/{id}/table` | Sửa ô bảng (`expected_version`) |
| PATCH | `/v1/ingestion/items/{id}/stem` | Sửa LaTeX |
| POST | `/v1/ingestion/items/{id}/non-cloneable` | Gắn cờ không nhân bản |
| POST | `/v1/ingestion/items/{id}/approve` | Duyệt (409 nếu còn BLOCKING) |
| POST | `/v1/blueprints` | *(Tuần 2 – Ngày 3)* Tạo blueprint từ các item đã duyệt |
| POST | `/v1/blueprints/{id}/twins` | *(Tuần 2 – Ngày 4)* Sinh bộ đề song sinh |

> Bổ sung: mã lỗi chuẩn, idempotency key cho POST sinh đề, phân trang.

## 13. Bảo mật & Threat Model
> STRIDE cho: template độc hại do prompt injection trong PDF, VLM bị lừa bởi text ẩn trong ảnh, rò rỉ đề giữa người dùng, DoS qua sandbox. Ghi rõ các biện pháp ở §9 và giới hạn tốc độ.

## 14. Quan sát hệ thống (Observability)
- Log có cấu trúc cho mỗi lần sandbox: `template_sha256, seed, status, duration_ms, stage`.
- Metrics: tỉ lệ chấp nhận seed theo template, phân bố `assertion_histogram`, tỉ lệ `AUTO_FALLBACK`, tỉ lệ Gate BLOCKED theo mã issue.

## 15. Chiến lược kiểm thử & Định nghĩa "Done"

| Loại | Nội dung | File |
|---|---|---|
| Security | 13 payload escape bị AST Guard chặn; proxy module; timeout; memory bomb | `tests/test_sandbox_security.py` |
| Correctness | So `solve()` với cài đặt tham chiếu độc lập trên 120 seed; tính tất định | `tests/test_rr_template.py` |
| Gate E2E | Đọc sai ô → BLOCKED → sửa → AUTO_PASS → approve; xung đột version 409 | `tests/test_schemas_and_gate.py` |

Done cho mỗi topic mới: template + reference solver độc lập + ≥ 100 seed property test + tỉ lệ chấp nhận seed ≥ 10 %.

## 16. Lộ trình Tuần 2 → Tuần 4

| Ngày | Hạng mục |
|---|---|
| T2-N1 | Spec này, schemas, sandbox, template RR, Gate API (đã xong) |
| T2-N2 | Template SJF (non-preemptive + SRTF), FIFO/LRU page replacement |
| T2-N3 | Blueprint extraction + Self-Solve end-to-end trên 5 đề thật |
| T2-N4 | Twin batch API, UI Ingestion Gate (bảng sửa ô) |
| T2-N5 | Banker's algorithm, process pool cho sandbox |
| Tuần 3 | MVP Golden Path demo; đo p95 |
| Tuần 4 | Multi-subject Vault, PostgreSQL, Kiến trúc Vi xử lý (Cache) |

## 17. Rủi ro & câu hỏi mở

| ID | Rủi ro / câu hỏi | Giảm thiểu / chủ sở hữu |
|---|---|---|
| R1 | Tỉ lệ chấp nhận seed thấp (RR ≈ 20 %) làm chậm sinh đề | Pool + song song; LLM được feedback histogram |
| R2 | Quy ước RR khác nhau giữa giảng viên | Assert `TIE_ARRIVAL_AT_PREEMPTION`; cấu hình quy ước theo môn (mở) |
| R3 | VLM đọc sai chữ số viết tay | Dual-pass + Self-Solve mismatch |
| Q1 | Có cho phép người dùng chỉnh template không? | Chưa quyết |

## 18. Phụ lục
- A. Danh mục misconception code theo topic.
- B. JSON Schema xuất từ Pydantic (`model_json_schema()`).
- C. ADR liên quan: ADR-001 Soft-Tagging, ADR-002 Citation không dùng bounding box, ADR-003 Sandbox subprocess thay vì WASM.
