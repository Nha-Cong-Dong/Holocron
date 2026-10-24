# Changelog

Theo [Keep a Changelog 1.1.0](https://keepachangelog.com/vi/1.1.0/) và [SemVer 2.0.0](https://semver.org/lang/vi/).
Năm nhóm cố định: 🚀 Tính năng mới · 🛠️ Cải tiến & Tối ưu · 🐛 Sửa lỗi · 🔒 Bảo mật Sandbox · ⚠️ Thay đổi không tương thích.

## [Unreleased]

### 🚀 Tính năng mới
- `infrastructure/llm_config.py`: cấu hình LLM/VLM từ biến môi trường `HOLOCRON_LLM_*`/`HOLOCRON_VLM_*` (khoá API bọc `SecretStr`, không lộ ở `repr`/serialize/log; thiếu VLM ⇒ chế độ demo) và `GET /v1/runtime` trả cờ `demo_mode`, `llm_configured`, `vlm_configured` cho UI; `types/holocron.ts` bổ sung `RuntimeInfo`.
- Frontend: trang `/blueprints/[blueprintId]` với Twin Viewer (twin, badge misconception từ `distractor_to_misconception_map`, trích dẫn, lời giải, `review_tickets`) và `GanttChart` (văn bản thay thế liệt kê từng đoạn, bảng màu có dark mode); logic thuần ở `lib/twin.ts` có test vitest; `lib/api.ts` thêm `createTwinBatch` (có `Idempotency-Key`). Frontend chỉ hiển thị số do backend trả.
- `POST /v1/blueprints/{id}/twins`: sinh `TwinBatch` (`count` 1–10, `base_seed` mặc định từ sha256 của `blueprint_id`, `Idempotency-Key` 24 h, 404/409/422; slot không sinh được vào `review_tickets`, không trả 5xx); `services/twin_batch.py`, kho blueprint trong `IngestionStore`, kiểm lại I5 theo item gắn với blueprint; `types/holocron.ts` bổ sung `TwinBatch`, `TwinBatchRequest`, `ReviewTicket`, `AttemptLog`. `POST /v1/blueprints` chưa triển khai (xem spec §6.3).
- `GanttSegment` và `TwinQuestion.gantt` (validator: không rỗng với topic CPU Scheduling, sắp theo `start`, không chồng lấn); `solve()` của template RR trả `gantt` đúng dạng `{"process", "start", "end"}` của spec §8.1; sandbox kiểm kiểu `gantt`; `types/holocron.ts` bổ sung `GanttSegment`.
- Template SJF không ưu tiên `os_sjf_avg_waiting` (topic `os.cpu_sched.sjf.avg_waiting_time`) với 3 misconception `WAIT_CONFUSED_WITH_TURNAROUND`, `SJF_IGNORES_ARRIVAL`, `SJF_COMPUTED_AS_SRTF`, đăng ký trong `template_library` và registry; kiểm bằng bộ giải tham chiếu độc lập, property test 200 seed (tỉ lệ nhận seed 16 %), tất định và sandbox không thoát bất thường. Chưa đăng ký ở Ingestion Gate (`TABLE_SCHEMAS`, `TOPIC_ALGORITHMS`).

### 🛠️ Cải tiến & Tối ưu
- Spec v0.4: viết lại §1 theo hướng đa môn (bài toán, người dùng mục tiêu là sinh viên tự ôn luyện, nền tảng desktop Tauri, tech stack kèm lý do, hai nhóm câu hỏi, ba dạng câu lý thuyết, bộ nhập PDF theo tầng, ba môn demo kèm yêu cầu có PDF scan và PDF có bảng lấy từ tài liệu thật, chấm tự luận để sau MVP, tầm nhìn sau MVP); mở rộng câu chữ I2 và I4 ở §0 cho câu lý thuyết (ADR-007); thêm §13.1 mô hình bảo mật của app local; cập nhật lộ trình và rủi ro R7–R9. Contract §5–§12 chưa đổi.
- ADR-005 (pipeline câu hỏi không phụ thuộc topic, kế hoạch trả nợ ARCH-001), ADR-006 (Knowledge Vault đa môn, tách từ tiếng Việt cho BM25), ADR-007 (câu lý thuyết sinh tất định từ Vault, cách tính `pool_sha256`; chấm tự luận sau MVP) và ADR-008 (bộ nhập PDF theo tầng, khảo sát giấy phép thư viện, phép đo CER chọn engine OCR), cả bốn ở trạng thái *Chấp nhận*. ADR-004 thêm nợ PKG-001 (FFmpeg LGPL trong wheel OpenCV); bảng test cưỡng chế ở CLAUDE.md §9 thêm test I2/I4 cho câu lý thuyết.
- Frontend: trả nợ FE-001, `TableAuditor` hết 7 cảnh báo Biome. Key React dựng từ helper thuần `rowIndices`, `tableColumns`, `keyedIssues` ở `lib/gate.ts` (có test vitest) thay cho chỉ số mảng; panel sửa ô dùng `<fieldset>` thay `role="group"`; focus vào ô nhập bằng callback ref khi người dùng mở ô, thay `autoFocus`. `biome.jsonc` nâng `noArrayIndexKey`, `noAutofocus`, `useSemanticElements` lên `error` và đổi trường `recommended` đã deprecated sang `"preset": "recommended"`.
- Claude Code: thêm 5 subagent trong `.claude/agents/` (`pdf-researcher`, `spec-writer`, `backend-impl`, `frontend-impl`, `reviewer`), mỗi agent có phạm vi file và bộ công cụ riêng; `.claude/settings.json` cho phép push nhánh và tạo PR nháp, chặn force push, push thẳng `develop`/`main`, xoá nhánh, merge PR, `merge`, `rebase`, `reset --hard`; `.gitignore` mở ngoại lệ cho hai đường dẫn này.
- README: thêm bước cài hook `pre-commit`.
- ADR-008, kết quả đo và engine OCR (2026-10-24): đo trên scan giả lập 10 trang (phép đo trên scan thật chưa làm). Từ điển ký tự của mọi model RapidOCR thiếu chữ có dấu thanh, quy tắc dừng đã kích hoạt. Engine chính của tầng 2 là Tesseract `vie` (tessdata_best), gọi binary do người dùng tự cài qua subprocess, không thêm gói Python; RapidOCR chỉ đối chiếu token số và mã. Thêm cách chuyển tầng theo cờ trang, độ phủ dòng, dấu hiệu chữ viết tay, đối chiếu chéo hai lượt đọc, biến cấu hình `HOLOCRON_PDF_OCR_*`/`HOLOCRON_TESSERACT_CMD`/`HOLOCRON_TESSDATA_DIR`, mô tả `PageExtraction` và sáu test bắt buộc; ghi đánh đổi Docling (không dùng cho MVP).
- ADR-004: ARCH-001 bổ sung các chỗ còn gắn cứng CPU Scheduling (`MAX_PROCESSES`, `to_template_params`, `GanttSegment`, `table_to_params`, `create_item`, `template_library`, `holocron.ts`, `layout.tsx`, `TableAuditor.tsx`, `UploadForm.tsx`) và ghi chú test I5 `test_input_limits.py`; PKG-001 thêm rủi ro giấy phép của wheel `tesserocr` và cách tránh.
- Tài liệu: báo cáo review tiến độ tuần 19–23/10 (`docs/progress-review-2026-10-23.md`) với danh sách thiếu sót F1–F18 và kết quả cổng kiểm tra chạy lại trên `903b954`.

### 🐛 Sửa lỗi

### 🔒 Bảo mật Sandbox

### ⚠️ Thay đổi không tương thích

## [0.1.0-alpha] - 2026-10-20

Khởi tạo khung Core, Ingestion Gate, frontend Ingestion và bộ công cụ kỷ luật kỹ thuật/phát hành (từ 2026-10-17): Tuần 1, Tuần 2 Ngày 1–2, hai commit dọn dẹp và nhánh `chore/engineering-discipline`.

### 🚀 Tính năng mới
- Domain schemas Pydantic v2 (`Citation`, `KnowledgeChunk`, `ExamBlueprint`, `QuestionTemplate`, `TwinQuestion`) với validator cưỡng chế I2 và I3 (1e4d5bc).
- Sandbox: ASTGuard, tiến trình con, giới hạn OS (RLIMIT trên Unix, Job Object trên Windows) (1e4d5bc, 6bb625c).
- Template Round Robin `os_rr_avg_waiting` và `template_library` pre-computed (1e4d5bc).
- Ingestion Gate 5 tín hiệu, self-solve, vá ô có `PatchAudit`, kiểm LaTeX (1e4d5bc).
- Pipeline retry budget (3 lượt LLM, 40 seed) với fallback và `ReviewTicket` (1e4d5bc).
- API item: tạo, lấy, sửa bảng (optimistic locking), sửa stem, đánh dấu non-cloneable, approve (1e4d5bc).
- Walkthrough và `examples/*.json` tất định (e4308b4); test bảo mật sandbox, schemas và gate, template RR (86eebd2).
- Tài liệu và cấu hình: README, SPECIFICATION, CLAUDE.md, `.gitignore`, LICENSE MIT, ADR placeholder (520f1cb, de8ca56, 7083af1, 1dfab60, 3a6a4a0).
- `CPUSchedulingProblem` và chuẩn hoá bảng đã qua Gate thành đầu vào duy nhất của `solve()` (613f096).
- `POST /v1/ingestion/upload` multipart: nhận diện magic bytes, `document_id` theo sha256, giới hạn 10 MiB, dual-pass bằng `MockDualPassExtractor` (607f685).
- Frontend Next.js 15: `UploadForm`, `TableAuditor` (ô BLOCKING viền đỏ, chọn nhanh từ `alternatives`, nút approve theo `canApprove`/I5), `lib/api.ts`, `types/holocron.ts` (10b01ea).
- Đặc tả v0.3: chốt Data/API contracts cho Tuần 2 (92ef6d7).
- Template Round Robin trả thêm khoá `gantt` từ `solve()` trong sandbox; `template_sha256` và `examples/*.json` được sinh lại (c7294cc).
- Công cụ phát hành: `scripts/check_commit_msg.py`, `check_versions.py`, `extract_release_notes.py`, `release_bench.py`.

### 🛠️ Cải tiến & Tối ưu
- Strict typing toàn `holocron_core`, annotation thật cho template RR, `ruff format` (4299a7c, 7ae8989, c739c02).
- Walkthrough đóng băng id, thời gian, `sandbox_duration_ms` và in UTF-8 (964c0f5, d74864a, 49182c4).
- Viết lại `.claude/CLAUDE.md`; README bổ sung phần frontend (856fc0b).
- `.gitignore` bỏ qua thư mục output của app desktop Claude (ad23c4f), cache công cụ và báo cáo coverage.
- Backend: coverage có nhánh (`pytest-cov`, `fail_under = 94`); `run_walkthrough` ghi file với `\n` để I4 giống từng byte trên Linux và Windows.
- Frontend: Biome 2.5.15 (`npm run lint`) và vitest (`npm test`); áp định dạng thuần cho 3 file.
- Version thống nhất `0.1.0-alpha` (npm) / `0.1.0a0` (Python, FastAPI).
- Tài liệu: điền nội dung ADR 001–003, thêm ADR 004 (kỷ luật kỹ thuật) và `0000-template`; thêm `docs/releases/` (quy trình, checklist 10 mục, mẫu release notes); `.claude/CLAUDE.md` bổ sung cổng tự động, phát hành, giao thức đổi bất biến và sổ nợ.

### 🐛 Sửa lỗi
- Sandbox chạy được trên Windows (Job Object) (6bb625c).
- Pipeline không crash khi template trả `misconception_code` chưa khai báo; retry kèm feedback (48b9a48).
- API: `quantum = null` bị từ chối bằng 422; chặn xung đột approve và non-cloneable (b633926).
- I5: `POST /v1/ingestion/items` với topic chưa hỗ trợ trả 422; item không có solver không thể AUTO_PASS.
- I5: `self_solve.status == "SKIPPED"` do sandbox lỗi/timeout giờ sinh issue BLOCKING `SELF_SOLVE_MISMATCH`, approve trả 409.
- Upload với tên file dài hơn 255 ký tự trả 422 thay vì 500.
- Bảng nhiều hơn 10 tiến trình trả `OUT_OF_RANGE` (BLOCKING) thay vì gắn nhầm `NOT_A_NUMBER`.
- `build_twin` không còn ném `TypeError` khi template trả `value` sai kiểu.

### 🔒 Bảo mật Sandbox
- ASTGuard whitelist import và builtins; proxy module không lộ module con; RLIMIT, Job Object, timeout wall-clock 2 s (1e4d5bc, 6bb625c).
- Ngoại lệ đã duyệt: một `# noqa: S102` tại dòng `exec` trong `_sandbox_child.py` (099269d, 1583482).
- Upload: từ chối định dạng sai (415), file rỗng (400), quá lớn (413), `Content-Type` không khớp nội dung thực (415) (607f685).
- Hợp đồng đầu ra sandbox kiểm cả KIỂU (`value` là số không phải bool; `display`/`explanation_vi`/`rationale_vi`/`misconception_code` là `str`), sai thì `contract_error`.
- Thêm `tests/test_architecture.py` cưỡng chế phân tầng package bằng `ast`.

### ⚠️ Thay đổi không tương thích
- Tái cấu trúc thành các package `domain/`, `services/`, `sandbox/`, `infrastructure/`, `routers/`: đường dẫn import `holocron_core.*` thay đổi (e9a2de7).
