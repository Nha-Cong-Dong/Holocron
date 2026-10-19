# Changelog

Theo [Keep a Changelog 1.1.0](https://keepachangelog.com/vi/1.1.0/) và [SemVer 2.0.0](https://semver.org/lang/vi/).
Năm nhóm cố định: 🚀 Tính năng mới · 🛠️ Cải tiến & Tối ưu · 🐛 Sửa lỗi · 🔒 Bảo mật Sandbox · ⚠️ Thay đổi không tương thích.

## [Unreleased]

### 🚀 Tính năng mới
- `GanttSegment` và `TwinQuestion.gantt` (validator: không rỗng với topic CPU Scheduling, sắp theo `start`, không chồng lấn); `solve()` của template RR trả `gantt` đúng dạng `{"process", "start", "end"}` của spec §8.1; sandbox kiểm kiểu `gantt`; `types/holocron.ts` bổ sung `GanttSegment`.
- Template SJF không ưu tiên `os_sjf_avg_waiting` (topic `os.cpu_sched.sjf.avg_waiting_time`) với 3 misconception `WAIT_CONFUSED_WITH_TURNAROUND`, `SJF_IGNORES_ARRIVAL`, `SJF_COMPUTED_AS_SRTF`, đăng ký trong `template_library` và registry; kiểm bằng bộ giải tham chiếu độc lập, property test 200 seed (tỉ lệ nhận seed 16 %), tất định và sandbox không thoát bất thường. Chưa đăng ký ở Ingestion Gate (`TABLE_SCHEMAS`, `TOPIC_ALGORITHMS`).

### 🛠️ Cải tiến & Tối ưu

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
