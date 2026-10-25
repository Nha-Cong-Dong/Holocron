# ADR 004: Kỷ luật kỹ thuật và quản trị phát hành

- Trạng thái: Chấp nhận
- Ngày: 2026-10-19

## Bối cảnh

Dự án có năm bất biến cần giữ qua nhiều tuần phát triển và nhiều người đóng góp, kể cả công cụ AI. Trước quyết định này, các cổng chất lượng chỉ là lệnh thủ công trong `.claude/CLAUDE.md`, version ba nơi lệch nhau (`pyproject` 0.2.0, `package.json` 0.1.0, FastAPI 0.3.0), và không có quy trình phát hành. Đợt rà soát phát hiện thêm hai lỗ hổng I5 (topic lạ và self-solve `SKIPPED` đều có thể AUTO_PASS) và hai lỗi biên (tên file dài trả 500; bảng > 10 tiến trình bị gắn nhầm mã), đều đã sửa kèm test.

## Quyết định

1. **Một bộ cổng, chạy hai nơi.** Cùng các kiểm tra chạy ở pre-commit (cục bộ) và CI (`.github/workflows/`): `ruff check`, `ruff format --check`, `mypy --strict holocron_core`, `pytest` (Linux và Windows, Python 3.12; Linux thêm 3.13), `biome ci`, `tsc`, `next build`, kiểm Conventional Commits, kiểm version, và kiểm tái lập I4 (`run_walkthrough` rồi `git diff --exit-code` trên `examples/`). Không được `--no-verify`, `SKIP=`, `continue-on-error: true`, hạ `fail_under` hay hạ ngưỡng ở `scripts/release_bench.py`.
2. **Coverage có ngưỡng.** `pytest-cov` đo cả nhánh với `fail_under = 94` (đo được 99,6 %). `sandbox/_sandbox_child.py` và `infrastructure/templates/*` được loại khỏi phép đo vì chỉ chạy trong tiến trình con (ADR 002); hành vi của chúng được kiểm bằng test hành vi.
3. **Kiến trúc được cưỡng chế bằng test** (`tests/test_architecture.py`), không chỉ bằng tài liệu (ADR 001).
4. **Frontend dùng Biome 2.5.15** (ghim chính xác) với preset `recommended` (khai báo `"preset": "recommended"`, thay trường `recommended` đã deprecated) cộng các quy tắc nghiêm hơn ở `holocron/frontend/biome.jsonc`; `vitest` cho logic thuần ở `src/lib/`. Mọi quy tắc khai báo riêng đều là `error`, gồm `noExplicitAny`, `noTsIgnore`, `noNonNullAssertion`, `noArrayIndexKey`, `noAutofocus`, `useSemanticElements` (nợ FE-001 đã trả, xem dưới); không hạ quy tắc nào xuống `warn` nếu chưa có ADR.
5. **Version một nguồn sự thật dạng SemVer**, quy đổi sang PEP 440 cho Python; `scripts/check_versions.py` bắt lệch. Pre-release tag trên `develop`, bản ổn định chỉ tag trên tổ tiên của `main`. Chi tiết ở `docs/releases/README.md`.
6. **CHANGELOG** theo Keep a Changelog với đúng 5 nhóm cố định; release notes sinh từ CHANGELOG, không viết tay.
7. **Cấm lối tắt (Zero-Shortcut)** giữ nguyên: không `# type: ignore`, `# noqa`, `biome-ignore`, `skip/xfail`; không hạ cấu hình kiểm tra; không sửa/xoá test sẵn có. Ngoại lệ duy nhất đã duyệt: một `# noqa: S102` tại `exec` trong sandbox.
8. **Ruleset ruff mở rộng hoãn lại.** Thử bộ `E,F,W,I,B,UP,S,RUF,C4,SIM,PT` (bỏ `S101`): đo ngày 2026-10-19 có 27 vi phạm ở `holocron_core` + `scripts` và 45 nếu tính cả `tests/` (nhiều nhất `PT018` 17 và `UP042` 12). Quá nhiều để gộp vào PR này mà không trộn lẫn thay đổi hành vi; sẽ xử lý ở PR riêng (nợ BE-002), không dùng `# noqa` hay `ignore`.

## Hệ quả

- Lỗi bị chặn sớm ở máy người đóng góp và ở CI cho cả hai hệ điều hành; commit message và version luôn nhất quán.
- Chi phí: pre-commit và CI chậm hơn (nhất là `tests` trên Windows vì spawn tiến trình), và người đóng góp phải cài `pre-commit`.
- Hook `pytest` và `next build` chạy ở giai đoạn `pre-push`, không ở `pre-commit`, để commit không bị chậm.
- Sổ nợ kỹ thuật được ghi công khai ngay trong ADR này thay vì giấu trong cấu hình:

| Mã | Nợ | Ghi chú |
|---|---|---|
| FE-001 | 7 cảnh báo Biome ở `TableAuditor.tsx` (`noAutofocus`, `useSemanticElements`, `noArrayIndexKey`) | **Đã trả 2026-10-23** (`5f3b2b4`, `1b9b5c4`, gộp qua PR #29): key React ổn định từ `lib/gate.ts` (có test), `fieldset` thay `role="group"`, focus bằng callback ref khi mở ô; ba quy tắc nâng lên `error` |
| BE-001 | `tests/` và `scripts/` chưa gõ kiểu: 29 lỗi `mypy --strict` | Không nằm trong cổng mypy hiện tại |
| BE-002 | Ruleset ruff mở rộng: 27 (core + scripts) / 45 (cả tests) vi phạm | PR riêng |
| BE-003 | Optimistic locking nguyên tử | Tuần 4, TODO đã duyệt trong `routers/ingestion.py` (trước khi tách router: `routers/api.py`; cập nhật đường dẫn, duyệt 2026-10-25) |
| BE-004 | Spawn tiến trình cho mỗi lần chạy sandbox | Pool tiến trình dựng sẵn và chạy song song seed |
| ARCH-001 | Gắn cứng CPU Scheduling ngoài `infrastructure/templates/` và test (CLAUDE.md §1, ADR-005 *Chấp nhận*) | Danh sách file ở dưới; gỡ theo ADR-005, sau đó thêm kiểm tra vào `tests/test_architecture.py` |
| PKG-001 | Wheel `opencv-python-headless` (phụ thuộc bắc cầu của `rapidocr`, ADR-008) đóng kèm FFmpeg theo LGPL-2.1 (`cv2/opencv_videoio_ffmpeg*.dll` trên Windows). Vẫn còn sau quyết định engine OCR ngày 2026-10-24, vì RapidOCR vẫn làm lượt đọc thứ hai. Thêm 2026-10-24: wheel `tesserocr` kèm libiconv (LGPL-2.1) trên Windows và libjbig (GPL-2.0+) trên manylinux | Khi đóng gói Tauri: bỏ file plugin `opencv_videoio_ffmpeg*.dll` khỏi gói, chạy lại test OCR để xác nhận OCR không dùng tới; kiểm wheel Linux và macOS xem có thư viện FFmpeg hay Qt đi kèm không; cập nhật `THIRD_PARTY_NOTICES` theo kết quả. `tesserocr`: cách tránh đã chọn (ADR-008 mục 8) là không dùng wheel này, gọi binary `tesseract` do người dùng tự cài qua subprocess với ảnh PNG do pypdfium2 render. Khi đóng gói Tesseract cùng app (sau MVP) phải kiểm giấy phép của bản build |

Chi tiết ARCH-001, lập ngày 2026-10-23 bằng `grep -rinE 'cpu_?sched|CPUScheduling|round.?robin|SJF|RR|quantum|burst|arrival|gantt|FCFS|SRTF'` trên `holocron_core/`, `holocron/backend/scripts/` và `holocron/frontend/src/`, bỏ `infrastructure/templates/` và file test, rồi lọc tay các kết quả chỉ nằm trong docstring:

- `holocron_core/domain/schemas.py`: hằng `CPU_SCHEDULING_TOPIC_PREFIX` và validator của `TwinQuestion` rẽ nhánh `topic_key.startswith(...)` để bắt buộc `gantt`; model riêng `SchedulingAlgorithm`, `ProcessItem`, `CPUSchedulingProblem`; `SolverInputs.quantum`.
- `holocron_core/services/ingestion_gate.py`: `TABLE_SCHEMAS` và `TOPIC_ALGORITHMS` khoá theo `os.cpu_sched.rr.avg_waiting_time` (cột tiến trình/arrival/burst cố định); `table_to_problem` trả `CPUSchedulingProblem`.
- `holocron_core/services/extraction.py`: `MockDualPassExtractor` chỉ hỗ trợ topic RR, header và đề RR mẫu; `DualPassExtraction.quantum`.
- `holocron_core/domain/ingestion.py:144`: `IngestionItem.problem: CPUSchedulingProblem`; `holocron_core/routers/ingestion.py`: form field `quantum` của upload (trước khi tách router cả hai ở `routers/api.py`; cập nhật đường dẫn, duyệt 2026-10-25).
- `holocron_core/infrastructure/template_library.py`: hằng `RR_TOPIC`, `SJF_TOPIC` và metadata hai template (biến, assert, misconception) viết trong code thay vì dữ liệu.
- `holocron_core/sandbox/_sandbox_child.py`: `_gantt_ok` kiểm khoá `gantt` riêng của dạng bài lập lịch trong kết quả `solve()`.
- `holocron_core/services/pipeline.py`: ánh xạ khoá `gantt` của `solve()` sang `TwinQuestion.gantt` (field riêng cho một dạng bài thay vì artifact đa hình).
- `holocron/backend/scripts/run_walkthrough.py`: walkthrough I4 dựng slot, chunk và blueprint RR; chấp nhận được như fixture nhưng là ví dụ end-to-end duy nhất.
- `holocron/frontend/src/types/holocron.ts`: `SchedulingAlgorithm`, `ProcessItem`, `CPUSchedulingProblem`, `SolverInputs.quantum`, `GanttSegment`, `TwinQuestion.gantt`.
- `holocron/frontend/src/components/ingestion/UploadForm.tsx`: danh sách dạng bài ghi cứng (Round Robin) và ô nhập quantum; `holocron/frontend/src/lib/api.ts`: tham số `quantum` của `uploadDocument`.
- `holocron/frontend/src/components/twins/GanttChart.tsx`, `GanttChart.module.css`, `TwinViewer.tsx`, `holocron/frontend/src/lib/twin.ts` (`ganttLayout`, `ganttDescription`) và biến màu `--gantt-*` trong `app/globals.css`: component vẽ riêng sơ đồ Gantt.

Bổ sung ngày 2026-10-24 (số dòng theo `777c521`), các chỗ còn thiếu trong danh sách trên:

- `holocron_core/domain/schemas.py:240` và `holocron_core/services/ingestion_gate.py:400` (trước khi tách router: `routers/api.py:220`; cập nhật đường dẫn, duyệt 2026-10-25): hằng `MAX_PROCESSES`; thông điệp "tiến trình" khi bảng quá số dòng ở `holocron_core/services/ingestion_gate.py:218-226`.
- `holocron_core/domain/schemas.py:269-278`: `to_template_params` với các khoá `names`, `arrivals`, `bursts` riêng của dạng bài lập lịch.
- `holocron_core/domain/schemas.py:495` (`GanttSegment`) và `:527` (`TwinQuestion.gantt`).
- `holocron_core/services/ingestion_gate.py:393-397`: `table_to_params` gắn cứng khoá `"quantum"`.
- `holocron_core/routers/ingestion.py:113` (trước khi tách router: `routers/api.py:328`; cập nhật đường dẫn, duyệt 2026-10-25): `create_item` kiểm `TOPIC_ALGORITHMS`.
- `holocron_core/infrastructure/template_library.py:113`: `theory_chunk_ids=["chk_os05_rr_definition"]`.
- `holocron/frontend/src/types/holocron.ts:226`: `IngestionItem.problem: CPUSchedulingProblem | null`.
- `holocron/frontend/src/app/layout.tsx:8`: mô tả trang ghi "môn Hệ điều hành".
- `holocron/frontend/src/components/ingestion/TableAuditor.tsx:168`: tiêu đề "bảng tiến trình".
- `holocron/frontend/src/components/ingestion/UploadForm.tsx:14`: topic Round Robin ghi cứng trong `TOPICS`.

Ghi chú: `tests/test_input_limits.py:36-53` khoá hành vi "bảng > 10 tiến trình → `OUT_OF_RANGE` (BLOCKING)". Đây là test I5 (CLAUDE.md §9), nên bước 2 của ADR-005 phải giữ hành vi này mà không sửa test. Đã duyệt 2026-10-24: khi đó giới hạn số dòng đến từ `input_schema` của template (`max_rows`, spec §5.10) thay cho `MAX_PROCESSES`; template RR khai báo `max_rows = 10` để giữ hành vi trên. ADR-005 mục 2 đã ghi thêm `max_rows`.

## Phương án đã loại

- **Chỉ dựa vào CI, không pre-commit:** phản hồi chậm và dễ đẩy commit sai chuẩn lên nhánh chung.
- **Chỉ dựa vào pre-commit:** có thể bị bỏ qua cục bộ; CI là cổng bắt buộc cuối.
- **ESLint + Prettier cho frontend:** hai công cụ và nhiều cấu hình hơn; Biome gộp lint và format trong một công cụ nhanh, đủ cho phạm vi hiện tại.
- **Tag pre-release trên `main`:** làm `main` lẫn bản chưa ổn định; `main` chỉ chứa bản đã chốt milestone.
