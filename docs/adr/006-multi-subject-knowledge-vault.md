# ADR 006: Knowledge Vault đa môn

- Trạng thái: Chấp nhận (2026-10-23). Bổ sung mục 9 (so khớp I3 sau chuẩn hoá NFC), người dùng đồng ý ngày 2026-10-24.
- Ngày: 2026-10-23; cập nhật 2026-10-24

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
   - Các cặp **khái niệm – trích dẫn định nghĩa** dùng cho câu lý thuyết. Chỉ cặp đã được người dùng xác nhận mới được dùng để sinh câu (ADR-007 mục 1).

   Mọi nhãn chỉ là gợi ý, hiện trên màn hình xem lại kèm `mapping_confidence`. Trích dẫn do LLM chọn PHẢI là chuỗi con nguyên văn (đã chuẩn hoá NFC và khoảng trắng, mục 9) của văn bản chunk; không khớp thì bị loại, không sửa hộ (I3).
3. **Cắt chunk tất định bằng code** theo spec §12.1 (heading, 450 token, chồng lấn 1 câu). LLM không cắt chunk. Cùng văn bản đầu vào thì cùng tập chunk.
4. **Tìm chunk bằng từ khoá, không dùng embedding trong MVP.** Truy vấn lọc theo `subject_code` và `topic_keys`, xếp hạng bằng BM25: SQLite FTS5 khi chuyển sang SQLite, còn bản in-memory dùng BM25 tự cài trên cùng bộ token. Kết quả xếp hạng tất định: hoà điểm thì xếp theo `chunk_id`. Embedding chạy offline thuộc tầm nhìn sau MVP (spec §1.6), vì cần thêm model và làm nặng bộ cài.
5. **Tách từ tiếng Việt cho BM25: theo âm tiết, chuẩn hoá dấu, không dùng thư viện tách từ.** Một hàm thuần chỉ dùng thư viện chuẩn (`unicodedata`, `re`), dùng chung cho văn bản chunk lúc lập chỉ mục và cho truy vấn:
   1. Chuẩn hoá Unicode NFC, vì PDF hay trả chữ có dấu ở dạng tổ hợp (NFD) và hai dạng phải so khớp được.
   2. Chuẩn hoá vị trí dấu thanh về một quy ước: dấu đặt trên nguyên âm chính theo kiểu mới (`hòa`, `thủy`, `khỏe`), nên `hoà` và `hòa`, `thuỷ` và `thủy` cho cùng token. Bảng ánh xạ cho các vần `oa`, `oe`, `uy` (có và không có phụ âm cuối) nằm cùng hàm, có test cho từng cặp.
   3. `casefold()`; giữ `đ` là chữ riêng, không đổi thành `d`.
   4. Tách token theo âm tiết: chuỗi ký tự chữ hoặc số liền nhau (`\w+` của Unicode), bỏ dấu câu. Tiếng Việt viết cách giữa các âm tiết nên mỗi token là một âm tiết. Không ghép từ nhiều âm tiết: BM25 trên âm tiết vẫn bắt được cụm từ nhờ tần suất đồng xuất hiện, và tránh thêm một model tách từ có thể sai.
   5. Không bỏ dấu khi lập chỉ mục chính, vì `ma`, `mà`, `má`, `mã` là những từ khác nhau. SQLite FTS5 dùng tokenizer `unicode61 remove_diacritics 0` trên văn bản đã qua bước 1–3. Một chỉ mục phụ không dấu chỉ dùng khi chỉ mục chính không trả kết quả nào, và điểm của nó luôn xếp sau.

   Thư viện tách từ đã cân nhắc (giấy phép kiểm trên PyPI và file LICENSE ngày 2026-10-23): **VnCoreNLP** là **GPL-3.0** nên bị loại; `py_vncorenlp` (MIT) chỉ là lớp bọc gọi VnCoreNLP. `underthesea` 9.5.0 là Apache-2.0 nhưng kéo theo `huggingface-hub` và tải model khi chạy. `pyvi` 0.1.1 là MIT nhưng từ 2021-06 không ra bản mới. Tách theo âm tiết không cần `underthesea` hay `pyvi`, nên MVP không thêm dependency nào.
6. **`VaultCitationProvider` thay `NoCitationProvider`.**
   - Slot tính toán: chọn chunk `THEORY` cùng `subject_code` có `topic_keys` chứa `topic_key` của slot; hoà điểm thì chọn trang nhỏ nhất rồi `chunk_id` nhỏ nhất. Không có chunk nào thì giữ hành vi hiện tại: `ReviewTicket` với lý do thiếu nguồn trích dẫn.
   - Slot lý thuyết: trích dẫn là `definition_quote` của cặp khái niệm đúng, đã xác nhận (ADR-007); không cần tìm thêm.
   - `QuestionTemplate.theory_chunk_ids` không còn dùng để tìm trích dẫn.
7. **Trích dẫn là bản chụp giá trị.** `TwinQuestion.citation` chép `doc_id`, `page_number`, `verbatim_quote` tại thời điểm sinh. Xoá tài liệu thì xoá chunk của nó, nhưng twin đã sinh vẫn giữ trích dẫn và UI báo "tài liệu gốc đã bị xoá" khi không mở được trang.
8. **File gốc được giữ cục bộ** trong thư mục dữ liệu ứng dụng, đặt tên theo `sha256`, để PDF.js mở đúng trang khi bấm trích dẫn (spec §12.2). Cùng nội dung thì cùng file, tương tự `document_id` của upload hiện tại.
9. **So khớp trích dẫn nguyên văn sau chuẩn hoá NFC (người dùng đồng ý ngày 2026-10-24).** PDF hay trả chữ có dấu ở dạng tổ hợp (NFD), trong khi văn bản đã xác nhận và trích dẫn có thể ở dạng NFC; so chuỗi con trực tiếp sẽ loại nhầm trích dẫn đúng.
   - Cả trích dẫn lẫn văn bản nguồn được đưa về Unicode NFC rồi gộp khoảng trắng, sau đó mới kiểm chuỗi con.
   - Chỉ hai chuỗi tương đương chính tắc (canonical equivalence) mới được coi là khớp. Không dùng NFKC, không bỏ dấu, không `casefold`, nên quy tắc không nới lỏng I3.
   - Áp dụng cho mọi chỗ kiểm trích dẫn nguyên văn: `KnowledgeChunk` (spec §0 I3, §5.8), cặp khái niệm (ADR-007 mục 1) và bằng chứng verdict của `AnswerJudge` sau MVP (ADR-007 mục 5, CLAUDE.md §2.1).
   - Test mới: `test_chunk_accepts_quote_in_nfd_against_nfc_text` trong `tests/test_schemas_and_gate.py`. `test_chunk_rejects_quote_not_in_text` vẫn phải xanh mà không sửa. Cả hai nằm ở dòng I3 của bảng CLAUDE.md §9.

## Hệ quả

- Twin có trích dẫn ngay khi người dùng đã nhập tài liệu của môn; G1 không còn bị chặn ở `NoCitationProvider`.
- Chất lượng trích dẫn phụ thuộc chất lượng nhãn `topic_keys`. Màn hình xem lại là nơi sửa nhãn sai, và tỉ lệ slot vào `review_tickets` vì thiếu nguồn là chỉ số cần theo dõi (spec §14).
- Tìm bằng từ khoá kém khi tài liệu dùng từ đồng nghĩa với cách đặt tên topic. Đây là rủi ro được chấp nhận cho MVP.
- Dữ liệu rời máy: văn bản chunk được gửi tới DeepSeek để gắn nhãn (spec §13.1).
- Contract cụ thể (endpoint nhập, liệt kê, xoá tài liệu; bảng `documents`, `chunks`, `concept_pairs`) được viết ở spec §5.8, §5.13, §6.5 và §6.6 (Đợt 2b, 2026-10-24); tên endpoint và phần vượt ADR ghi "đề xuất" chờ duyệt.
- Kiểm chứng: test I3 sẵn có (`test_chunk_rejects_quote_not_in_text`) và test NFC ở mục 9; thêm test cho thấy trích dẫn LLM đề xuất mà không khớp nguyên văn thì bị loại, slot không có chunk phù hợp thì vào `review_tickets`, và bộ tách từ cho cùng token với chữ NFC và NFD, với `hoà`/`hòa`, `thuỷ`/`thủy`, nhưng khác token với `ma`/`mà`.

## Phương án đã loại

- **Để LLM cắt chunk và viết lại nội dung cho gọn.** Văn bản đã viết lại không còn là nguyên văn, trái I3.
- **Tìm bằng embedding ngay trong MVP.** Cần model embedding chạy offline (nặng bộ cài) hoặc gọi API (thêm dữ liệu rời máy, thêm key), trong khi lợi ích chưa được đo.
- **Giữ `theory_chunk_ids` ghi cứng trong template.** Không dùng được với tài liệu người dùng tự nhập, và gắn template với một bộ tài liệu cụ thể.
