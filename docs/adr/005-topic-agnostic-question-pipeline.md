# ADR 005: Pipeline câu hỏi không phụ thuộc topic

- Trạng thái: Chấp nhận (2026-10-23). F16 (SJF ở Ingestion Gate) làm theo bước 1 của thứ tự trả nợ bên dưới, không thêm vào `TABLE_SCHEMAS`. Việc đổi câu chữ I1 và I4 ("Gantt" thành "artifact lời giải theo `kind`") ở bước 3 được người làm dự án đồng ý ngày 2026-10-25, có điều kiện: khi đổi code ở bước 3, `tests/test_sandbox_security.py` phải xanh nguyên vẹn (CLAUDE.md §9; spec §0 đã sửa, duyệt 2026-10-25).
- Ngày: 2026-10-23

## Bối cảnh

Theo `SPECIFICATION.md` §1.5 (v0.4, cập nhật 2026-10-24), MVP là đa môn. CPU Scheduling (RR/SJF) giờ chỉ là fixture kiểm thử và walkthrough, không phải môn demo. Môn demo Kỹ thuật vi xử lý có câu tính toán cần artifact dạng bảng (thanh ghi, cờ CPSR, ô nhớ). CLAUDE.md §1 cấm code trong `domain/`, `services/`, `routers/` và `frontend/` rẽ nhánh theo môn, chương hay topic. Tuy vậy, khung hiện tại được dựng quanh CPU Scheduling. Nợ ARCH-001 trong ADR-004 liệt kê các chỗ gắn cứng, gồm năm nhóm:

1. **Dữ liệu đề:** `SchedulingAlgorithm`, `ProcessItem`, `CPUSchedulingProblem`, `SolverInputs.quantum` (`domain/schemas.py`), `IngestionItem.problem: CPUSchedulingProblem` (`routers/api.py`).
2. **Lược đồ bảng của Gate:** `TABLE_SCHEMAS` và `TOPIC_ALGORITHMS` khoá theo `os.cpu_sched.rr.avg_waiting_time`, cùng `table_to_problem` (`services/ingestion_gate.py`).
3. **Artifact lời giải:** `TwinQuestion.gantt` và validator rẽ nhánh `topic_key.startswith(CPU_SCHEDULING_TOPIC_PREFIX)`; `_gantt_ok` trong `sandbox/_sandbox_child.py`; ánh xạ `gantt` trong `services/pipeline.py`.
4. **Metadata template viết trong code:** `RR_TOPIC`, `SJF_TOPIC` và toàn bộ biến, assert, misconception của hai template nằm trong `infrastructure/template_library.py`.
5. **Frontend:** danh sách dạng bài và ô `quantum` ghi cứng trong `UploadForm.tsx`; `GanttChart` vẽ riêng một dạng bài; kiểu `CPUSchedulingProblem`, `GanttSegment` trong `types/holocron.ts`.

Hệ quả: thêm một dạng bài tính toán mới (ví dụ bài xác suất của môn demo thứ hai) hiện đòi sửa code ở cả năm nhóm, đúng điều CLAUDE.md §1 cấm. Việc bổ sung SJF vào `TABLE_SCHEMAS`/`TOPIC_ALGORITHMS` (F16) theo cách hiện tại cũng sẽ thêm một chỗ gắn cứng mới.

## Quyết định

Tri thức riêng của từng dạng bài chuyển hết vào **template và dữ liệu đi kèm template**. Code chỉ làm việc với các khái niệm chung.

1. **Trường phân biệt nhóm câu hỏi.** `BlueprintSlot` và `TwinQuestion` có thêm `question_kind: Literal["computational", "theory"]`. Hiện `BlueprintSlot.is_computational` đã mang thông tin này; field mới thay thế nó. Câu `theory` đi theo ADR-007 và không dùng sandbox.
2. **Template tự khai báo đầu vào.** `QuestionTemplate` có thêm `input_schema`: danh sách bảng (mỗi cột có `name`, `kind` `label|int|float`, miền giá trị, `header_aliases`; mỗi bảng có số dòng tối đa `max_rows`, thay `MAX_PROCESSES`, bổ sung và duyệt ngày 2026-10-24) và danh sách tham số vô hướng (ví dụ `quantum`), kèm miền giá trị. Contract chi tiết ở spec §5.10. Gate đọc `input_schema` của template đã kiểm chứng ứng với `topic_key` thay cho `TABLE_SCHEMAS`. Việc chuyển bảng thành `params` trở thành một hàm chung, ánh xạ cột theo `name`.
3. **Luật riêng của dạng bài nằm trong `validate()`.** Các luật như "RR bắt buộc có quantum", "SJF không có quantum" hay "tên tiến trình không trùng" chuyển thành `assert` có nhãn `VALIDITY_*` trong template (§8.2), chạy trong sandbox. Gate ánh xạ `assertion_failed` ở bước tự giải thành issue BLOCKING kèm nhãn assert. `CPUSchedulingProblem`, `ProcessItem`, `SchedulingAlgorithm` và `TOPIC_ALGORITHMS` bị xoá; `IngestionItem.problem` thay bằng `params: dict[str, Any]` đã qua `input_schema`. Đây là ngoại lệ `params` đã được CLAUDE.md §4.1 cho phép.
4. **Artifact lời giải đa hình.** `TwinQuestion.gantt` thay bằng `artifacts: list[SolutionArtifact]`, với `SolutionArtifact` là discriminated union theo trường `kind`:
   - `timeline`: các đoạn `{label, start, end}` sắp theo `start`, không chồng lấn. Sơ đồ Gantt là một timeline.
   - `table`: `headers` và `rows` là chuỗi hiển thị.

   Template khai báo `required_artifacts` trong metadata; `build_twin` kiểm template có trả đủ artifact đã khai báo hay không, thay cho validator rẽ nhánh theo `topic_key`. Sandbox kiểm kiểu artifact theo `kind`, không theo dạng bài.
5. **Metadata template là dữ liệu.** Mỗi template trong `infrastructure/templates/` có một file `.json` đi kèm (biến, assert, misconception, `answer`, `input_schema`, `required_artifacts`). Registry quét thư mục và dựng `QuestionTemplate` từ cặp `.py` + `.json`. Các hằng `RR_TOPIC`, `SJF_TOPIC` bị xoá.
6. **Frontend render theo dữ liệu.** Danh sách dạng bài lấy từ backend (endpoint liệt kê topic có template đã kiểm chứng; contract ở Giai đoạn 3). Form tham số vô hướng sinh từ `input_schema`. `GanttChart` đổi thành `TimelineArtifact`, thêm `TableArtifact`; Twin Viewer chọn component theo `kind`.
7. **Cưỡng chế bằng test.** Sau khi gỡ xong, `tests/test_architecture.py` kiểm không còn chuỗi topic/môn cố định trong `holocron_core` ngoài `infrastructure/templates/` và fixture test (CLAUDE.md §9).

**Thứ tự trả nợ** (mỗi bước là một PR, cổng xanh sau từng bước):

| Bước | Nội dung | Đóng |
|---|---|---|
| 1 | Metadata template sang `.json`, thêm `input_schema`; Gate đọc `input_schema` thay `TABLE_SCHEMAS`. SJF nhập được qua Gate mà không thêm hằng mới | F16, nhóm 2, nhóm 4 |
| 2 | Luật dạng bài vào `validate()`; xoá `CPUSchedulingProblem` và các model liên quan; `IngestionItem.params` | Nhóm 1 |
| 3 | `artifacts` đa hình thay `gantt` (backend, sandbox, TypeScript, frontend) | Nhóm 3, nhóm 5 |
| 4 | Danh sách topic từ backend, form sinh từ `input_schema`; thêm kiểm tra vào `test_architecture.py` | Nhóm 5, đóng ARCH-001 |

## Hệ quả

- Thêm dạng bài tính toán mới chỉ cần thêm cặp `.py` + `.json` (viết tay, hoặc LLM viết rồi được kiểm chứng và người duyệt), không sửa code.
- **Thay đổi không tương thích (⚠️):** `TwinQuestion.gantt`, `IngestionItem.problem`, field `quantum` của upload và các kiểu TypeScript tương ứng. Bước 3 làm đổi `examples/*.json` (`gantt` thành `artifacts`), là thay đổi có chủ đích và phải được duyệt khi xem `git diff examples/`.
- **Chạm bất biến I1:** câu chữ của I1 (và I4) trong spec §0 nhắc "sơ đồ Gantt". Bản chất không đổi (mọi artifact vẫn do sandbox sinh), nhưng câu chữ cần đổi thành "artifact lời giải". Theo CLAUDE.md §9, việc này cần người dùng đồng ý. **Đã đồng ý ngày 2026-10-25** (duyệt có điều kiện, xem phần Trạng thái); spec §0 nay ghi "artifact lời giải theo `kind`".
- **Chạm sandbox:** bước 3 thay `_gantt_ok` bằng kiểm tra artifact chung trong `_sandbox_child.py`. Đây là kiểm tra đầu ra, không nới whitelist hay giới hạn, nhưng vẫn thuộc nhóm DỪNG VÀ HỎI ở CLAUDE.md §3 và cần thêm test trong `tests/test_sandbox_security.py`. Điều kiện duyệt ngày 2026-10-25: các test sẵn có của `tests/test_sandbox_security.py` phải xanh nguyên vẹn (không sửa, không xoá); chỉ được thêm test mới.
- `format` (hình thức trả lời) độc lập với `question_kind`; thêm `ESSAY` (spec §5.14, duyệt 2026-10-25). `question_kind` giữ hai giá trị `computational`, `theory`.
- Lỗi miền giá trị (ví dụ RR thiếu quantum) hiện bị bắt ở Pydantic với thông điệp riêng. Sau bước 2 lỗi này đến từ nhãn assert, nên thông điệp tiếng Việt phải lấy từ `description` của assert trong metadata.
- Kiểm chứng: `test_architecture.py` (bước 4); test hiện có của RR/SJF giữ nguyên hành vi; property test ≥ 100 seed vẫn chạy cho mọi template.

## Phương án đã loại

- **Giữ `CPUSchedulingProblem`, thêm một model riêng cho mỗi môn.** Mỗi dạng bài mới phải sửa code ở `domain/`, `services/` và `frontend/`, trái CLAUDE.md §1.
- **Để LLM tự suy ra lược đồ bảng mỗi lần nhập đề.** Lược đồ sẽ không ổn định giữa các lần chạy, và Gate mất một nguồn kiểm tra tin cậy. Lược đồ nên là dữ liệu của template đã kiểm chứng.
- **Bổ sung SJF thẳng vào `TABLE_SCHEMAS`/`TOPIC_ALGORITHMS` (F16) trước, gỡ nợ sau.** Nhanh hơn, nhưng thêm chỗ gắn cứng mà CLAUDE.md §1 cấm. Bước 1 ở trên đóng F16 với chi phí tương đương.
