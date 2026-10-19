# ADR 003: Dual-pass + Ingestion Gate; trích dẫn bằng `verbatim_quote`

- Trạng thái: Chấp nhận
- Ngày: 2026-10-19

## Bối cảnh

Dữ liệu đề (bảng tiến trình, stem, phương án) đến từ VLM đọc ảnh nên không tin cậy. Một chữ số đọc sai (ví dụ burst 9 thành 8) làm cả đáp án sinh ra sai mà không có dấu hiệu nào ở đầu ra. Cần một cơ chế bắt lỗi trước khi dữ liệu vào sandbox (I5). Về trích dẫn, người học cần kiểm chứng lời giải với slide gốc nhanh, và trích dẫn phải đúng nguyên văn (I3).

## Quyết định

1. **Dual-pass:** VLM đọc hai lượt độc lập trên cùng lưới `(row, col)`; mỗi ô có `confidence`. Hiện dùng `MockDualPassExtractor` (tất định, không gọi mạng); adapter VLM thật cắm vào qua `DocumentExtractor` (ADR 001).
2. **Ingestion Gate với 5 tín hiệu** (`services/ingestion_gate.py`, SPECIFICATION §7.2):
   - S1 `DUAL_PASS_MISMATCH` (BLOCKING): hai lượt khác nhau.
   - S2 `LOW_CONFIDENCE` (WARNING): `confidence < 0.85`.
   - S3 schema: `HEADER_MISMATCH`, `NOT_A_NUMBER`, `OUT_OF_RANGE`, `DUPLICATE_LABEL`, `MISSING_CELL`.
   - S4 `NOT_IN_TEXT_LAYER` (WARNING): số không có trong text layer của PDF.
   - S5 `SELF_SOLVE_MISMATCH` (BLOCKING): chạy `solve()` trong sandbox trên bảng hiện tại, kết quả không khớp đúng một phương án; cũng áp dụng khi self-solve không chạy được (`SKIPPED`) hoặc topic chưa có solver, để không có đường nào dẫn tới AUTO_PASS.
3. **Con người xác nhận:** ô bị cờ được tô trên `TableAuditor`; người dùng sửa qua `PATCH …/table` (ô sửa có `verified_by_human=true`, mỗi lần sửa có `PatchAudit`). `POST …/approve` trả 409 khi còn BLOCKING; frontend vô hiệu nút bằng `canApprove`.
4. **Trích dẫn bằng `verbatim_quote`, không bằng bounding box:** `Citation` mang `doc_id`, `page_number` và `verbatim_quote`; validator của `KnowledgeChunk` bắt `verbatim_quote` là chuỗi con (đã chuẩn hoá khoảng trắng) của `raw_text`. Client dùng tìm kiếm văn bản (PDF.js `find`) trên đúng trang để cuộn tới chỗ trích.
5. **Không thêm mã issue mới khi đủ dùng mã sẵn có**, để không đổi contract với frontend (`types/holocron.ts`) khi chưa cần.

## Hệ quả

- Lỗi đọc ảnh bị bắt bằng ba cơ chế độc lập (dual-pass, self-solve, người dùng), thay vì dựa vào độ tin cậy của một lượt đọc.
- Trích dẫn không phụ thuộc hình học trang, nên không vỡ khi đổi kích thước hay độ phân giải; bù lại cần text layer, và ảnh scan không có text layer thì chưa trích dẫn 1-chạm được (nằm ở lộ trình Vault).
- Chi phí: hai lượt VLM mỗi ảnh, và người dùng phải xử lý các ô bị cờ. Đây là đánh đổi chủ ý để giữ I5.
- Giới hạn đã biết: self-solve chỉ bắt được lỗi làm đáp án lệch khỏi mọi phương án; lỗi ở ô không ảnh hưởng đáp án chỉ bị bắt bởi dual-pass và độ tin cậy.

## Phương án đã loại

- **Một lượt đọc và tin độ tin cậy của VLM:** không bắt được lỗi tự tin sai.
- **Bounding box cho trích dẫn:** gắn chặt với hình học trang, dễ lệch giữa các lần dựng PDF, và đòi hỏi OCR có toạ độ chính xác.
- **Tự động sửa ô lệch bằng cách chọn lượt có độ tin cậy cao hơn:** thay con người đưa ra quyết định cuối, trái với I5.
