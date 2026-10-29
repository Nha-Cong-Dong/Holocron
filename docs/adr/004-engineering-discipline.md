# ADR 004: Kỷ luật kỹ thuật và quản trị phát hành

- Trạng thái: Chấp nhận
- Ngày: 2026-10-19; sổ nợ bổ sung BE-005, BE-006, SEC-001, PKG-002 ngày 2026-10-26 (chiều) theo quyết định của người dùng; PKG-003 đề xuất 2026-10-28, chờ người dùng duyệt. Trạng thái không đổi.

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
| PKG-001 | Wheel `opencv-python-headless` (phụ thuộc bắc cầu của `rapidocr`, ADR-008) đóng kèm FFmpeg theo LGPL-2.1 (`cv2/opencv_videoio_ffmpeg*.dll` trên Windows). Vẫn còn sau quyết định engine OCR ngày 2026-10-24, vì RapidOCR vẫn làm lượt đọc thứ hai. Cập nhật 2026-10-27: build script của nhánh P4 (`scripts/build_portable.py`, chưa merge) bỏ `opencv_videoio_ffmpeg*.dll` khỏi zip portable và dừng nếu còn sót; kiểm wheel Linux/macOS vẫn còn. Thêm 2026-10-24: wheel `tesserocr` kèm libiconv (LGPL-2.1) trên Windows và libjbig (GPL-2.0+) trên manylinux | Khi đóng gói Tauri: bỏ file plugin `opencv_videoio_ffmpeg*.dll` khỏi gói, chạy lại test OCR để xác nhận OCR không dùng tới; kiểm wheel Linux và macOS xem có thư viện FFmpeg hay Qt đi kèm không; cập nhật `THIRD_PARTY_NOTICES` theo kết quả. `tesserocr`: cách tránh đã chọn (ADR-008 mục 8) là không dùng wheel này, gọi binary `tesseract` do người dùng tự cài qua subprocess với ảnh PNG do pypdfium2 render. Khi đóng gói Tesseract cùng app (sau MVP) phải kiểm giấy phép của bản build |
| BE-005 | `REGISTRY = InMemoryTemplateRegistry()` là đối tượng cấp module trong `holocron_core/routers/deps.py` (re-export ở `routers/api.py`), không cấp qua `Depends`; test không thay được bằng `app.dependency_overrides` như các phụ thuộc khác (CLAUDE.md §2.2) | Thêm 2026-10-26 chiều (duyệt 2026-10-26 chiều), làm sau MVP: thêm `get_registry()` và cấp qua `Depends(get_registry)`, giữ tên `REGISTRY` re-export cho tới khi mọi chỗ import đã chuyển. Không thêm đối tượng cấp module mới theo kiểu này |
| BE-006 | Tầng 2 của bộ nhập PDF (ADR-008), ba điểm: (1) regex token của đối chiếu chéo `0x[0-9A-F]+\|#?-?\d+` (`TOKEN_PATTERN`, `infrastructure/document_readers/cross_check.py`) tách `#0x1F` thành `#0` và `1`, nên lệch ở chữ số hex sau `x` không được so đúng; (2) lý do `HIGH_PAGE_CER` đã khai báo trong `ReviewReason` (spec §5.7) nhưng chưa có code tính; (3) hạn tổng cho cả tài liệu `document_timeout_s = 600` s (`infrastructure/document_readers/config.py`) chỉ đủ khoảng 90 trang scan (2–7 s mỗi trang theo ADR-008, "Kết quả đo"); trang còn lại thành `failed` (`PAGE_TIMEOUT`) | Thêm 2026-10-26 chiều (duyệt 2026-10-26 chiều), làm sau MVP. (1) đổi regex sao cho `#0x1F` là một token, kèm test; (2) tính CER văn bản giữa hai lượt đọc với ngưỡng 0.15 (chưa xác minh, ADR-008 mục 11); (3) hiệu chỉnh hạn tổng theo số trang khi đo thời gian bộ nhập (spec §16, mốc "Tới 7/11"). Không hạ giới hạn hiện có để làm xanh test |
| SEC-001 | Kiểm `Host` (spec §13.1) chưa xét header `Forwarded` (RFC 7239) hay `X-Forwarded-Host` | Thêm 2026-10-26 chiều (duyệt 2026-10-26 chiều, ghi cùng nhóm ghi chú tầng 2), làm sau MVP. Backend chỉ nghe `127.0.0.1` và không đứng sau proxy nào trong MVP, nên chưa cần; khi có proxy (ví dụ phương án A của `docs/tauri-spike-plan.md`) phải quyết định bỏ qua hay kiểm các header này, kèm test trong `tests/test_local_security.py` |
| PKG-002 | `THIRD_PARTY_NOTICES` thiếu các phụ thuộc bắc cầu của `rapidocr` (ADR-008) | Thêm 2026-10-26 chiều (duyệt 2026-10-26 chiều). PHẢI xong trước release (`v1.0.0-rc`): liệt kê đủ phụ thuộc bắc cầu của `rapidocr` cùng giấy phép của từng gói (kiểm trên PyPI và file LICENSE trong wheel đã ghim), gộp kết quả kiểm wheel của PKG-001. Thêm 2026-10-27 (Q4, duyệt 2026-10-27): `shapely==2.2.0` (BSD-3-Clause) và GEOS 3.14.1 (LGPL-2.1, liên kết động, `shapely.libs/geos*.dll`) chỉ ở bản zip portable đủ có RapidOCR; điều kiện tuân thủ: kèm toàn văn LGPL-2.1 và mã nguồn GEOS (`licenses/geos-3.14.1.tar.bz2`), THIRD_PARTY_NOTICES của zip ghi DLL bị delvewheel sửa đổi, build dừng nếu GEOS ngoài `shapely.libs/` hoặc thiếu ánh xạ phiên bản (cưỡng chế ở `scripts/build_portable.py`, nhánh P4, chưa merge) |
| PKG-003 | Log file của bản portable (spec §14, nhánh P6, chưa merge) có thể chứa văn bản tài liệu trong traceback (lệch quy tắc "không log văn bản trang"), và hai phiên bản chạy với `--data-dir` khác nhau ghi chung một file `holocron.log`. Rủi ro thêm: trên Windows, hai tiến trình khác `--data-dir` cùng mở log thì lúc xoay vòng (rename) có thể lỗi `PermissionError`, `logging` in lỗi ra stderr và mất dòng log | Đề xuất 2026-10-28, chờ người dùng duyệt. Đề xuất xong trước `v1.0.0-rc`: lọc traceback (bỏ `input_value` của lỗi Pydantic, cắt văn bản dài) và đặt tên file log theo phiên bản/data-dir; kèm test, gồm test cho trường hợp hai tiến trình cùng mở log trên Windows |
| ARCH-002 | Câu lý thuyết vào `TwinBatch` bằng union `TwinQuestion \| TheoryTwin` theo `question_kind` (`BatchTwin`, `domain/twins.py`), thay vì `TwinQuestion` đa hình có `question_kind`, `template_id`/`topic_key` nullable và `trace` union như spec §5.11 | Thêm 2026-10-28 (duyệt 2026-10-28, quyết định 14(b)); nhánh B5a. Lý do: tránh đổi model, pipeline và `examples/` trước 29/10 (~3h so với 5–6h). Hệ quả: frontend phải rẽ nhánh theo `question_kind`; `TwinBatch.twins` không còn kiểu đồng nhất. Trả bằng ARCH-008 |
| ARCH-003 | Không có `TheoryTrace`: bộ ba I4 `(generator_version, pool_sha256, seed)` nằm trong `TheoryQuestion`, `TheoryTwin` không có `trace` (spec §5.11 yêu cầu `GenerationTrace` union có `TheoryTrace`) | Thêm 2026-10-28 (duyệt 2026-10-28). Test I4 cho câu lý thuyết (`tests/test_theory_generator.py`) đọc field của `question`, không đọc `trace`. Trả bằng ARCH-008 |
| ARCH-004 | `TheorySlot` là model riêng (`ExamBlueprint.slots` là union `BlueprintSlot \| TheorySlot`), `BlueprintSlot` vẫn có `is_computational` và không có `question_kind` (spec §5.9: một `BlueprintSlot` có `question_kind` và field nullable `source_page_number`, `topic_key`, `self_solve`) | Thêm 2026-10-28 (duyệt 2026-10-28). Lý do: không đổi JSON `examples/exam_blueprint_sample.json`. `TheorySlot` không có `source_page_number`, `self_solve`, `distractor_archetypes`. Trả bằng ARCH-008 |
| ARCH-005 | Dấu vân tay chống câu lý thuyết trùng (R6) là sha256 của `generator_version`, `form`, bốn phương án A–D và `citation.chunk_id`, thay cho "danh sách `pair_id` theo thứ tự phương án" của spec §6.3 | Thêm 2026-10-28 (duyệt 2026-10-28). Bộ sinh không lộ `pair_id`. Hạn chế: hai cặp khác nhau cùng chunk, cùng văn bản phương án bị coi là một. Trả bằng ARCH-008: bộ sinh trả `pair_id` của các phương án, đổi vân tay theo spec, test lại I4 |
| ARCH-006 | `TheorySlot.source_chunk_id` (chunk gợi ý của người dùng) được lưu trên slot, ngoài shape §5.9 mục tiêu (slot lý thuyết không lưu chunk gợi ý); `POST /v1/blueprints` buộc chunk cùng `chapter_code` với slot (spec §6.7 chỉ nói "không tồn tại hoặc khác môn") | Thêm 2026-10-28 (field duyệt 2026-10-28; luật cùng `chapter_code` duyệt 2026-10-28, câu 25). Khi chuyển (a): quyết định giữ field trong §5.9 hay bỏ |
| ARCH-007 | Không có cổng kiểm riêng cho tính năng sinh câu lý thuyết: `TheoryTwin.review_status` luôn `AUTO_PASS`; không có gate `theory_generation` (cờ cấu hình hay kiểm Vault tối thiểu trước khi cho `POST …/twins` sinh câu lý thuyết) | Thêm 2026-10-28 (`review_status = AUTO_PASS` duyệt 2026-10-28, câu 24). Câu lý thuyết vẫn qua mọi kiểm của bộ sinh (I2, I3, I4, luật "đúng một đáp án", thiếu căn cứ ⇒ `ReviewTicket`); chỗ thiếu là bước người duyệt và cổng bật/tắt tính năng. Quyết định sau 29/10 |
| ARCH-008 | Kế hoạch chuyển sang shape (a) sau 29/10: `TwinQuestion` đa hình theo `question_kind` với `template_id`/`topic_key` nullable và `trace` union (`ComputationalTrace \| TheoryTrace`); `BlueprintSlot` có `question_kind` và field nullable; bỏ `TheoryTwin`, `TheorySlot`, `BatchTwin` | Thêm 2026-10-28 (kế hoạch, ~5–6h). Việc: đổi model và `types/holocron.ts`, pipeline, `services/twin_batch.py`; `examples/` đổi (`trace.kind`, `question_kind`) và duyệt qua `git diff examples/`; chuyển dữ liệu đã lưu (cột JSON của blueprint và batch) hoặc cho phép đọc cả hai dạng; đổi test sẵn có đích danh (cần người dùng đồng ý, CLAUDE.md §7). Xong thì cập nhật spec §5.9, §5.11, §6.3, §6.7 và gỡ nhãn "mục tiêu sau 29/10" |
| TEST-001 | Test chập chờn `tests/test_api_sqlite.py::test_upload_patch_approve_twins_and_survive_a_restart`: thỉnh thoảng đỏ, chưa rõ nguyên nhân | Thêm 2026-10-28 (người dùng yêu cầu 2026-10-28, PR #83). KHÔNG `skip`/`xfail`, không nới assert (CLAUDE.md §7). Điều tra 29/10: chạy lặp có ghi log để tìm nguyên nhân; sửa code hoặc test theo nguyên nhân tìm được, báo người dùng nếu phải đổi assert |

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

Bổ sung ngày 2026-10-24, các chỗ còn thiếu trong danh sách trên. Số dòng theo `777c521`, trừ hai vị trí mới sau khi tách router ở gạch đầu dòng thứ nhất (`services/ingestion_gate.py:400`) và thứ năm (`routers/ingestion.py:113`), theo `99a3d2e`; số dòng cũ ghi trong ngoặc ở hai gạch đầu dòng đó (`routers/api.py:220`, `routers/api.py:328`) vẫn theo `777c521`:

- `holocron_core/domain/schemas.py:240` và `holocron_core/services/ingestion_gate.py:400` (trước khi tách router: `routers/api.py:220`; cập nhật đường dẫn, duyệt 2026-10-25): hằng `MAX_PROCESSES`; thông điệp "tiến trình" khi bảng quá số dòng ở `holocron_core/services/ingestion_gate.py:168-174` (số dòng theo `f59f417`; số dòng cũ `218-226` theo `777c521`, không còn khớp).
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

### Khả năng chờ backend (UI mới)

Trạng thái: đề xuất, chờ duyệt (2026-10-29; người dùng yêu cầu ghi như vậy ở `.claude/state/decisions-answers-2026-10-29.md`, câu 16). Phương án A lấy từ `.claude/state/decisions-2026-10-28.md`, mục "Đổi kế hoạch lần cuối"; nguồn yêu cầu `design-handoff/README.md` mục 9. Phương án A: UI không đặt TODO trong code; mỗi chỗ chờ backend kiểm một khả năng có tên trong `GET /v1/runtime` (spec §5.12, §11.4), mặc định `available = false`, `reason = "not_implemented"`. Chỗ nào chưa có khả năng thì UI dùng chức năng hiện có kèm một câu giải thích ngắn, không hiển thị dữ liệu giả. "Nhập nhiều file" không cần khả năng mới (UI gửi lần lượt qua `POST /v1/documents`).

| Mã | Tên khả năng | Lý do | Điều kiện xoá |
|---|---|---|---|
| CAP-001 | `concept_auto_proposal` | UI mới cần đề xuất khái niệm tự động không cần LLM (thuật ngữ + trích dẫn nguyên văn + số trang); backend chưa có | Tên vào `IMPLEMENTED_CAPABILITIES` và có test endpoint |
| CAP-002 | `line_review_queue` | UI mới cần hàng đợi duyệt theo dòng nghi ngờ; backend chưa có; khi làm thật cần ADR I5 riêng | Tên vào `IMPLEMENTED_CAPABILITIES` và có test endpoint |
| CAP-003 | `page_progress_eta` | UI mới cần tiến độ theo trang và thời gian còn lại cho từng tài liệu đang đọc; backend chưa có | Tên vào `IMPLEMENTED_CAPABILITIES` và có test endpoint |

### Nợ: worker OCR chưa kiểm unicharset lúc khởi động (ghi 2026-10-29)

ADR-008 mục 14 quy định kiểm unicharset lúc khởi động. Bộ kiểm khả năng khởi động có kiểm, nhưng worker (`build_ocr`, `_pdf_worker.py`) thì chưa. Hệ quả: có thể báo OCR không khả dụng (`ocr_language_missing`) dù worker vẫn OCR được. Điều kiện xoá: worker gọi cùng hàm kiểm với bộ kiểm khởi động.

## Phương án đã loại

- **Chỉ dựa vào CI, không pre-commit:** phản hồi chậm và dễ đẩy commit sai chuẩn lên nhánh chung.
- **Chỉ dựa vào pre-commit:** có thể bị bỏ qua cục bộ; CI là cổng bắt buộc cuối.
- **ESLint + Prettier cho frontend:** hai công cụ và nhiều cấu hình hơn; Biome gộp lint và format trong một công cụ nhanh, đủ cho phạm vi hiện tại.
- **Tag pre-release trên `main`:** làm `main` lẫn bản chưa ổn định; `main` chỉ chứa bản đã chốt milestone.
