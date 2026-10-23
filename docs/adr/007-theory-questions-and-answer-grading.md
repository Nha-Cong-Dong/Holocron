# ADR 007: Câu lý thuyết sinh tất định từ Vault; chấm tự luận sau MVP

- Trạng thái: Đề xuất
- Ngày: 2026-10-24

## Bối cảnh

`SPECIFICATION.md` §1.5 (v0.4) chia câu hỏi thành hai nhóm. Câu tính toán đã có cơ chế (template, sandbox, I1–I5). Câu lý thuyết chưa có thiết kế, mà yêu cầu thì đã rõ: đáp án bắt buộc có trích dẫn từ Vault (I3), và không đủ căn cứ thì vào `review_tickets`, không được bịa. Riêng môn demo Triết học Mác–Lênin chỉ có câu lý thuyết, nên luồng G1 của môn đó phụ thuộc hoàn toàn vào quyết định này.

CLAUDE.md §2.1 đã xếp "bộ sinh câu hỏi tất định (điền khuyết, ghép khái niệm)" vào Deterministic Core, còn "bộ soạn câu hỏi lý thuyết" và `AnswerJudge` vào LLM Gate. Spec v0.3 lại loại "tự luận, chấm tự luận" khỏi MVP. ADR này chốt phần nào thuộc MVP.

## Quyết định

### 1. Câu lý thuyết trong MVP sinh tất định từ Vault, không do LLM soạn

Nguyên liệu là các **cặp khái niệm – định nghĩa** (`ConceptPair`) mà Vault lưu khi nhập tài liệu (ADR-006). Mỗi cặp gồm `term` (tên khái niệm), `definition_quote` (trích dẫn nguyên văn từ chunk), `chunk_id`, `subject_code`, `chapter_code`. Cặp chỉ được lưu khi `definition_quote` là chuỗi con nguyên văn của văn bản chunk (I3). Nếu `term` xuất hiện trong trích dẫn, vị trí xuất hiện được ghi lại.

Hai dạng câu MCQ4, đều do code sinh:

| Dạng | Thân câu | Đáp án đúng | Phương án nhiễu |
|---|---|---|---|
| Ghép khái niệm | "Khái niệm nào ứng với đoạn trích sau?" kèm `definition_quote`; tên khái niệm trong trích dẫn được thay bằng `____` | `term` của cặp | `term` của 3 cặp khác |
| Điền khuyết | `definition_quote` với một thuật ngữ khác trong trích dẫn được thay bằng `____` (chỉ dùng khi thuật ngữ đó cũng là `term` của một cặp trong Vault) | Thuật ngữ bị che | `term` của 3 cặp khác |

- Phương án nhiễu ưu tiên khái niệm cùng chương, rồi mới tới khái niệm cùng môn. Bốn phương án phải khác nhau sau khi chuẩn hoá (giống luật MCQ4 hiện có).
- `citation` của câu là trích dẫn của cặp đúng. Giải thích cho từng phương án nhiễu là định nghĩa nguyên văn của khái niệm đó, kèm trích dẫn riêng.
- Môn có ít hơn 4 khái niệm hợp lệ, hoặc không chọn được 3 phương án nhiễu khác nhau, thì slot vào `ReviewTicket` với lý do "không đủ căn cứ trong Vault".

### 2. Tái lập và misconception

- **Tái lập:** chọn cặp và phương án bằng `random.Random(f"{pool_sha256}:{seed}")`, trong đó `pool_sha256` là sha256 của danh sách cặp ứng viên đã sắp xếp. Cùng Vault và cùng seed thì cùng câu hỏi và cùng thứ tự phương án. Đây là cách mở rộng I4 sang câu không có template.
- **Misconception:** bộ sinh khai báo cố định hai mã, `THEORY_SAME_CHAPTER_CONCEPT` và `THEORY_SAME_SUBJECT_CONCEPT`. Mỗi phương án nhiễu nhận đúng một mã tuỳ theo nguồn chọn, nên luật "map phủ đúng 3 phương án sai" của `TwinQuestion` vẫn giữ.

### 3. LLM chỉ đề xuất cặp khái niệm

DeepSeek đọc chunk và đề xuất cặp `(term, definition_quote)`. Code kiểm nguyên văn; cặp sai bị loại. Người dùng có thể sửa hoặc xoá cặp ở màn hình xem lại. LLM không soạn thân câu, không soạn phương án, không viết giải thích.

### 4. Chấm câu trả lời tự luận và `AnswerJudge`: sau MVP

Thiết kế giữ theo CLAUDE.md §2.1, chưa triển khai:

- Rubric gồm các ý, mỗi ý neo vào một trích dẫn nguyên văn. Đáp án mẫu là trích dẫn, không phải văn bản LLM diễn đạt lại.
- LLM chỉ trả verdict cho từng ý (`đạt` / `thiếu` / `sai`) kèm bằng chứng. Bằng chứng phải là chuỗi con (đã chuẩn hoá khoảng trắng) của câu trả lời người học hoặc của tài liệu nguồn; verdict không có bằng chứng hợp lệ bị loại và ý đó chuyển sang "cần người xem".
- Điểm và kết luận đúng/sai do code tính từ verdict. LLM không trả điểm.

Lý do để sau MVP: chấm tự luận cần một bộ câu trả lời mẫu có nhãn để đo độ đúng (script đánh giá opt-in, CLAUDE.md §2.2), và việc này không làm kịp trong thời hạn hackathon. MVP chỉ có câu có đáp án xác định (MCQ4, câu trả lời số).

## Hệ quả

- Môn lý thuyết thuần chạy được luồng G1 mà không cần sandbox hay template.
- Câu lý thuyết MVP chỉ kiểm tra **nhận biết khái niệm** (mức `remember`/`understand`), chưa kiểm tra lập luận. Đây là giới hạn có chủ đích.
- Chất lượng câu hỏi phụ thuộc chất lượng cặp khái niệm, nên màn hình xem lại của Vault phải cho sửa và xoá cặp.
- **Chạm bất biến I2 và I4:** câu chữ hiện tại của I2 ("mã đã khai báo trong template") và I4 ("`(template_sha256, seed)`") chỉ nói về câu có template. Mục 2 mở rộng cách hiểu hai bất biến cho câu lý thuyết. Theo CLAUDE.md §9, việc đổi câu chữ bất biến cần người dùng đồng ý; khi được duyệt sẽ thêm test cưỡng chế tương ứng.
- Contract (`question_kind`, model `ConceptPair`, trace cho câu không có template) được chốt ở Giai đoạn 3, spec §5/§6.
- Kiểm chứng: test cho thấy cặp có trích dẫn không nguyên văn bị loại; Vault ít hơn 4 khái niệm thì vào `review_tickets`; cùng Vault và cùng seed thì câu hỏi giống từng byte; mọi phương án nhiễu đều có mã misconception.

## Phương án đã loại

- **Để LLM soạn câu lý thuyết và phương án nhiễu.** Phương án nhiễu do LLM viết là câu sai không có nguồn, còn thân câu là văn bản diễn đạt lại; cả hai trái I3. Muốn kiểm phải dựng thêm một tầng kiểm chứng mà MVP chưa có thời gian làm.
- **Câu đúng/sai sinh bằng cách LLM sửa sai một chi tiết của trích dẫn.** Câu sai do LLM tạo có thể vô tình đúng ở chỗ khác của tài liệu; không có cách tất định để kiểm.
- **Đưa `AnswerJudge` vào MVP.** Không đo được độ đúng trước hạn nộp; điểm chấm sai còn tệ hơn không chấm.
