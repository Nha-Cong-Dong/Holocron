# ADR 001: Kiến trúc hai tầng và phân tầng package

- Trạng thái: Chấp nhận
- Ngày: 2026-10-19

## Bối cảnh

Holocron sinh đề song sinh từ ảnh đề thi cũ. Hai nguồn đầu vào không đáng tin: VLM đọc ảnh (có thể đọc sai chữ số, bị lừa bởi chữ ẩn) và LLM viết mã template (có thể viết mã độc hoặc sai). Trong khi đó sản phẩm cam kết năm bất biến (SPECIFICATION §0): không con số nào do LLM tự viết (I1), mỗi phương án sai ánh xạ đúng một misconception (I2), trích dẫn nguyên văn (I3), tái lập theo `(template_sha256, seed)` (I4), và không đề nào vào tính toán khi Gate còn issue BLOCKING (I5).

## Quyết định

1. **Tách hai tầng theo mức tin cậy** (SPECIFICATION §3.3):
   - **LLM Gate (không tin cậy):** `DocumentExtractor` (VLM) và `TemplateAuthor` (LLM viết template). Chỉ được đọc ảnh thành bảng/stem và viết mã template.
   - **Deterministic Core (tin cậy):** `ingestion_gate`, `pipeline`, `sandbox`, `template_library`, `domain`. Mọi phép tính, Gantt, đáp án, phương án nhiễu và trộn phương án nằm ở đây.
   - Mọi output không tin cậy phải đi qua một cổng kiểm: bảng → Ingestion Gate; mã template → ASTGuard + sandbox + validator `TwinQuestion`.
2. **Phân tầng package một chiều** (`holocron/backend/holocron_core/`): `domain` ← `sandbox`, `infrastructure` ← `services` ← `routers`. `domain` chỉ dùng thư viện chuẩn và `pydantic`; `sandbox` không import `services`, `routers` hay mạng; `services` không import `routers` hay `fastapi`; `infrastructure` không import `services` hay `routers`.
3. **Phụ thuộc ngoài đi qua `Protocol`** (`DocumentExtractor`, `TemplateAuthor`, `TemplateRegistry`) và được cấp bằng `Depends` hoặc tham số, để test thay bằng stub và không gọi mạng. Không thêm global state mutable; kho dữ liệu hiện tại là `IngestionStore` cấp qua `Depends(get_store)`.
4. **Cưỡng chế bằng test, không bằng thói quen:** `tests/test_architecture.py` phân tích `import` bằng `ast` và fail khi một tầng import thứ bị cấm.
5. **Frontend chỉ hiển thị số do backend trả**, không tự tính lại; `canApprove` phía frontend phản chiếu I5 nhưng backend vẫn là nơi cưỡng chế (`POST …/approve` trả 409).

## Hệ quả

- Dễ kiểm chứng: mỗi bất biến có điểm cưỡng chế cụ thể và test tương ứng (xem `.claude/CLAUDE.md` §9).
- Thay VLM hoặc LLM thật cho mock chỉ cần cấp adapter mới qua `Depends`, không đổi router.
- Tăng chi phí: dữ liệu qua ranh giới phải là model Pydantic, và thêm tính năng cần chọn đúng tầng. Vi phạm bị `test_architecture.py` chặn ở CI.
- Frontend trùng logic `canApprove` với backend; sai lệch giữa hai nơi chỉ làm nút bị khoá nhầm, không phá I5, vì backend vẫn chặn.

## Phương án đã loại

- **Cho LLM trả thẳng đáp án và phương án nhiễu:** vi phạm I1; không kiểm chứng được và không tái lập được (I4).
- **Tin kết quả VLM nếu độ tin cậy cao:** vi phạm I5; chữ số đọc sai có độ tin cậy cao vẫn xảy ra, nên bắt buộc dual-pass, self-solve và xác nhận của con người (ADR 003).
- **Kiến trúc phẳng không phân tầng:** dễ import vòng và dễ để router hay service tính hộ một con số, phá I1.
