# ADR 006: Knowledge Vault đa môn

- Trạng thái: Đề xuất
- Ngày: 2026-10-24

## Bối cảnh

Mục tiêu G1 (`SPECIFICATION.md` §1.5) yêu cầu mọi twin đều có trích dẫn, và câu lý thuyết lấy đáp án từ tài liệu học (I3). Hiện tại:

- Đã có model `DocumentMeta`, `KnowledgeChunk` và `Citation` (`domain/schemas.py`); validator của `KnowledgeChunk` cưỡng chế `verbatim_quote` là chuỗi con của `raw_text` (I3).
- Chưa có luồng nhập tài liệu, chưa có kho chunk, chưa có endpoint nào. `CitationProvider` mặc định là `NoCitationProvider` (`services/twin_batch.py`), nên mọi slot đều rơi vào `review_tickets`.
- Spec §12 mới mô tả cách cắt chunk và trích dẫn 1-chạm, chưa nói chunk lấy từ đâu và tìm bằng cách nào.
- `QuestionTemplate.theory_chunk_ids` ghi cứng id chunk (ví dụ `chk_os05_rr_definition` trong `template_library.py`). Cách này không dùng được khi tài liệu do người dùng tự nhập.

## Quyết định

1. **Nguồn vào duy nhất là bộ nhập PDF đã qua xem lại** (ADR-008). Văn bản của chunk là văn bản người dùng đã xác nhận ở màn hình xem lại, không phải đầu ra thô của OCR hay VLM. Có thêm đường nhập văn bản thuần (dán hoặc file `.txt`) dùng cho test và tài liệu mẫu.
2. **Nhãn do DeepSeek đề xuất, người dùng xác nhận.** Trên văn bản đã tách, DeepSeek đề xuất:
   - `subject_code`: mã học phần (khớp mẫu `^[A-Z]{2,6}[0-9]{2,4}$` sẵn có, ví dụ `IT3070`). Nếu tài liệu không ghi mã, người dùng nhập.
   - `chapter_code` theo heading (`ch3`, `ch3.2`).
   - `category` `THEORY`/`PRACTICE`, `topic_keys`.
   - Các cặp **khái niệm – trích dẫn định nghĩa** dùng cho câu lý thuyết (ADR-007).

   Mọi nhãn chỉ là gợi ý, hiện trên màn hình xem lại kèm `mapping_confidence`. Trích dẫn do LLM chọn PHẢI là chuỗi con nguyên văn (đã chuẩn hoá khoảng trắng) của văn bản chunk; không khớp thì bị loại, không sửa hộ (I3).
3. **Cắt chunk tất định bằng code** theo spec §12.1 (heading, 450 token, chồng lấn 1 câu). LLM không cắt chunk. Cùng văn bản đầu vào thì cùng tập chunk.
4. **Tìm chunk bằng từ khoá, không dùng embedding trong MVP.** Truy vấn lọc theo `subject_code` và `topic_keys`, xếp hạng bằng BM25: SQLite FTS5 khi chuyển sang SQLite, còn bản in-memory dùng điểm trùng từ đơn giản. Kết quả xếp hạng tất định. Embedding chạy offline thuộc tầm nhìn sau MVP (§1.6), vì cần thêm model và làm nặng bộ cài.
5. **`VaultCitationProvider` thay `NoCitationProvider`.**
   - Slot tính toán: chọn chunk `THEORY` cùng `subject_code` có `topic_keys` chứa `topic_key` của slot; hoà điểm thì chọn trang nhỏ nhất rồi `chunk_id` nhỏ nhất. Không có chunk nào thì giữ hành vi hiện tại: `ReviewTicket` với lý do thiếu nguồn trích dẫn.
   - `QuestionTemplate.theory_chunk_ids` không còn dùng để tìm trích dẫn.
6. **Trích dẫn là bản chụp giá trị.** `TwinQuestion.citation` chép `doc_id`, `page_number`, `verbatim_quote` tại thời điểm sinh. Xoá tài liệu thì xoá chunk của nó, nhưng twin đã sinh vẫn giữ trích dẫn và UI báo "tài liệu gốc đã bị xoá" khi không mở được trang.
7. **File gốc được giữ cục bộ** trong thư mục dữ liệu ứng dụng, đặt tên theo `sha256`, để PDF.js mở đúng trang khi bấm trích dẫn (spec §12.2). Cùng nội dung thì cùng file, tương tự `document_id` của upload hiện tại.

## Hệ quả

- Twin có trích dẫn ngay khi người dùng đã nhập tài liệu của môn; G1 không còn bị chặn ở `NoCitationProvider`.
- Chất lượng trích dẫn phụ thuộc chất lượng nhãn `topic_keys`. Màn hình xem lại là nơi sửa nhãn sai, và tỉ lệ slot vào `review_tickets` vì thiếu nguồn là chỉ số cần theo dõi (spec §14).
- Tìm bằng từ khoá kém khi tài liệu dùng từ đồng nghĩa với cách đặt tên topic. Đây là rủi ro được chấp nhận cho MVP.
- Dữ liệu rời máy: văn bản chunk được gửi tới DeepSeek để gắn nhãn (spec §13.1).
- Contract cụ thể (endpoint nhập, liệt kê, xoá tài liệu; bảng `documents`, `chunks`, `concept_pairs`) được chốt ở Giai đoạn 3 trong spec §5/§6.
- Kiểm chứng: test I3 sẵn có (`test_chunk_rejects_quote_not_in_text`); thêm test cho thấy trích dẫn LLM đề xuất mà không khớp nguyên văn thì bị loại, và slot không có chunk phù hợp thì vào `review_tickets`.

## Phương án đã loại

- **Để LLM cắt chunk và viết lại nội dung cho gọn.** Văn bản đã viết lại không còn là nguyên văn, trái I3.
- **Tìm bằng embedding ngay trong MVP.** Cần model embedding chạy offline (nặng bộ cài) hoặc gọi API (thêm dữ liệu rời máy, thêm key), trong khi lợi ích chưa được đo.
- **Giữ `theory_chunk_ids` ghi cứng trong template.** Không dùng được với tài liệu người dùng tự nhập, và gắn template với một bộ tài liệu cụ thể.
