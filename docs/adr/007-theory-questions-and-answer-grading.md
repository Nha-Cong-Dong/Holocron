# ADR 007: Câu lý thuyết sinh tất định từ Vault; chấm tự luận offline (MVP nếu còn thời gian); AnswerJudge sau MVP

- Trạng thái: Chấp nhận (2026-10-23), gồm câu chữ I2 và I4 ở mục 4. Sửa ngày 2026-10-25 theo quyết định của người dùng: tiêu đề, nhà cung cấp LLM ở mục 1 và 3, khoá sắp xếp `pool_sha256` ở mục 2, mục 5 tách thành 5a (chấm offline) và 5b (`AnswerJudge` sau MVP), phần Hệ quả.
- Ngày: 2026-10-23; cập nhật 2026-10-25

## Bối cảnh

`SPECIFICATION.md` §1.5 (v0.4) chia câu hỏi thành hai nhóm. Câu tính toán đã có cơ chế (template, sandbox, I1–I5). Câu lý thuyết chưa có thiết kế, mà yêu cầu thì đã rõ: đáp án bắt buộc có trích dẫn từ Vault (I3), và không đủ căn cứ thì vào `review_tickets`, không được bịa. Riêng môn demo Triết học Mác–Lênin chỉ có câu lý thuyết, nên luồng G1 của môn đó phụ thuộc hoàn toàn vào quyết định này.

CLAUDE.md §2.1 đã xếp "bộ sinh câu hỏi tất định (điền khuyết, ghép khái niệm)" vào Deterministic Core, còn "bộ soạn câu hỏi lý thuyết" và `AnswerJudge` vào LLM Gate. Spec v0.3 lại loại "tự luận, chấm tự luận" khỏi MVP. ADR này chốt phần nào thuộc MVP.

## Quyết định

### 1. Câu lý thuyết trong MVP sinh tất định từ Vault, không do LLM soạn

Nguyên liệu là các **cặp khái niệm – định nghĩa** (`ConceptPair`). Mỗi cặp gồm `term` (tên khái niệm), `definition_quote` (trích dẫn định nghĩa), `chunk_id`, `subject_code`, `chapter_code` và trạng thái xác nhận.

**Nguồn của cặp, theo đúng thứ tự:**
1. **LLM đề xuất** cặp từ một chunk đã nằm trong Vault (ADR-006), tức từ văn bản người dùng đã xác nhận ở màn hình xem lại của bộ nhập. LLM là tuỳ chọn; nhà cung cấp do cấu hình quyết định, không có nhà cung cấp mặc định (hiện là gateway kiểu OpenAI của ban tổ chức, model `claude-haiku-5-5`). Contract hiện chỉ có adapter kiểu OpenAI Chat Completions sau `Protocol` `ChatCompletionClient` (tên đề xuất, spec §11.5); DeepSeek hay server local tương thích OpenAI dùng được qua adapter này; Anthropic gốc (API không theo giao thức OpenAI) cần một adapter riêng cài cùng `Protocol`, chưa có. Không có LLM thì người dùng tự tạo cặp bằng cách bôi đoạn văn trong chunk (duyệt 2026-10-25); cặp đó vẫn qua bước 2.
2. **Code kiểm nguyên văn:** `definition_quote` PHẢI là chuỗi con (đã chuẩn hoá NFC và khoảng trắng, ADR-006 mục 9, duyệt 2026-10-24) của văn bản chunk đó (I3). Không khớp thì cặp bị loại ngay, không sửa hộ và không hiện cho người dùng.
3. **Người dùng xác nhận** cặp ở màn hình xem lại; có thể sửa `term`, hoặc chọn lại trích dẫn bằng cách bôi đúng đoạn văn trong chunk (đoạn được chọn vẫn qua bước 2).

**Chỉ cặp đã được người dùng xác nhận mới được dùng để sinh câu.** Cặp chưa xác nhận hay bị từ chối không vào nhóm ứng viên và không tính vào `pool_sha256` (mục 2). Nhóm ứng viên của một slot chỉ gồm các cặp cùng `subject_code` với slot. Nếu `term` xuất hiện trong chính trích dẫn của nó, vị trí xuất hiện được ghi lại để che khi sinh câu.

Ba dạng câu MCQ4, đều do code sinh; LLM không soạn thân câu, phương án hay giải thích:

| Dạng | Thân câu | Đáp án đúng | Phương án nhiễu |
|---|---|---|---|
| Ghép khái niệm | "Khái niệm nào ứng với đoạn trích sau?" kèm `definition_quote`; tên khái niệm trong trích dẫn được thay bằng `____` | `term` của cặp | `term` của 3 cặp khác, ưu tiên cùng chương rồi mới tới cùng môn |
| Điền khuyết | `definition_quote` với một thuật ngữ khác trong trích dẫn được thay bằng `____` (chỉ dùng khi thuật ngữ đó cũng là `term` của một cặp đã xác nhận) | Thuật ngữ bị che | `term` của 3 cặp khác, ưu tiên cùng chương rồi mới tới cùng môn |
| Chọn định nghĩa đúng | "Phát biểu nào là định nghĩa của «`term`»?" | `definition_quote` của cặp | `definition_quote` của 3 khái niệm **khác cùng chương**; không đủ 3 khái niệm cùng chương thì không sinh dạng này |

- Ở mọi dạng, tên khái niệm xuất hiện bên trong một phương án được che bằng `____`, để phương án không tự lộ đáp án.
- Bốn phương án phải khác nhau sau khi chuẩn hoá (giống luật MCQ4 hiện có).
- `citation` của câu là trích dẫn của cặp đúng. Giải thích cho từng phương án nhiễu là định nghĩa nguyên văn của khái niệm ứng với phương án đó, kèm trích dẫn riêng.
- Không có đủ 4 khái niệm đã xác nhận (với dạng thứ ba: 4 khái niệm cùng chương), hoặc không chọn được 3 phương án nhiễu khác nhau, thì slot vào `ReviewTicket` với lý do "không đủ căn cứ trong Vault".

### 2. Tái lập và misconception

- **Tái lập:** chọn cặp và phương án bằng `random.Random(f"{generator_version}:{pool_sha256}:{seed}")`, trong đó `generator_version` là phiên bản của bộ sinh, tăng khi đổi luật chọn. Cùng ba giá trị đó thì cùng câu hỏi, cùng đáp án và cùng thứ tự phương án. Đây là cách mở rộng I4 sang câu không có template.
- **Phạm vi của `pool_sha256`:** chỉ gồm các cặp **đã xác nhận** có **cùng `subject_code` với slot**, không phải toàn Vault. Thêm, sửa hay xoá dữ liệu của môn khác không làm đổi câu hỏi của môn này.
- **Cách tính `pool_sha256`:**
  1. Mỗi cặp lấy đúng năm trường `subject_code`, `chapter_code`, `term`, `definition_quote`, `chunk_id`; mỗi trường chuỗi được chuẩn hoá Unicode NFC.
  2. Sắp danh sách cặp theo `(chapter_code, term, chunk_id, definition_quote)` sau khi chuẩn hoá; khoá cuối dùng để phân xử khi hoà, nhờ đó hash không phụ thuộc thứ tự đầu vào (duyệt 2026-10-25; làm I4 chặt hơn, không nới).
  3. Serialize danh sách bằng `json.dumps(..., sort_keys=True, ensure_ascii=False, separators=(",", ":"))`, mã hoá UTF-8.
  4. `pool_sha256` là chuỗi hex của sha256 trên dãy byte đó.
- **Misconception:** bộ sinh khai báo cố định hai mã, `THEORY_SAME_CHAPTER_CONCEPT` và `THEORY_SAME_SUBJECT_CONCEPT` (danh sách `THEORY_MISCONCEPTIONS`). Mỗi phương án nhiễu nhận đúng một mã tuỳ theo nguồn chọn; dạng "chọn định nghĩa đúng" chỉ dùng `THEORY_SAME_CHAPTER_CONCEPT`. Như vậy luật "map phủ đúng 3 phương án sai" của `TwinQuestion` vẫn giữ.

### 3. LLM chỉ đề xuất cặp khái niệm

LLM (tuỳ chọn; nhà cung cấp do cấu hình quyết định, hiện qua gateway của ban tổ chức; duyệt 2026-10-25) chỉ đề xuất cặp `(term, definition_quote)` theo đúng thứ tự ở mục 1: đề xuất, code kiểm nguyên văn, người dùng xác nhận. LLM không soạn thân câu, không soạn phương án, không viết giải thích.

### 4. Câu chữ bất biến I2 và I4 (đã duyệt 2026-10-23)

Chỉ sửa câu chữ và cơ chế cưỡng chế trong bảng §0 của spec; nội dung với câu tính toán giữ nguyên.

| | Trước (spec v0.4 §0) | Sau (đã áp dụng vào spec §0) |
|---|---|---|
| **I2** bất biến | Mỗi phương án sai của MCQ4 ánh xạ đúng một `misconception_code` đã khai báo trong template. | Mỗi phương án sai của MCQ4 ánh xạ đúng một `misconception_code` đã khai báo trước: trong template với câu tính toán, hoặc trong danh sách mã cố định `THEORY_MISCONCEPTIONS` của bộ sinh với câu lý thuyết (ADR-007). |
| **I2** cơ chế | Validator `TwinQuestion._format_rules`; `build_twin` ném `ValueError` khi gặp mã lạ → retry (§10). | Validator `TwinQuestion._format_rules`; `build_twin` ném `ValueError` khi gặp mã lạ → retry (§10); bộ sinh câu lý thuyết chỉ gán mã thuộc `THEORY_MISCONCEPTIONS`, có test cưỡng chế. |
| **I4** bất biến | `(template_sha256, seed)` ⇒ cùng đề, cùng đáp án, cùng thứ tự phương án, cùng Gantt. | Câu tính toán: `(template_sha256, seed)` ⇒ cùng đề, cùng đáp án, cùng thứ tự phương án, cùng Gantt. Câu lý thuyết: `(generator_version, pool_sha256, seed)` ⇒ cùng đề, cùng đáp án, cùng thứ tự phương án, trong đó `pool_sha256` là sha256 của các cặp khái niệm đã xác nhận **cùng `subject_code` với slot** (cách tính ở ADR-007 mục 2); dữ liệu của môn khác không làm đổi câu hỏi của môn này. |
| **I4** cơ chế | `random.Random(seed)` trong template; trộn phương án bằng `random.Random(f"{sha256}:{seed}")`; walkthrough sinh `examples/*.json` giống từng byte. | Như cũ, cộng thêm: bộ sinh câu lý thuyết chỉ dùng `random.Random(f"{generator_version}:{pool_sha256}:{seed}")`; test cho thấy cùng nội dung nhập ở dạng NFC hay NFD cho cùng `pool_sha256`, và thêm cặp ở môn khác không làm đổi `pool_sha256` của môn này; walkthrough sinh thêm ví dụ câu lý thuyết, cũng phải giống từng byte. |

Test cưỡng chế được ghi vào bảng CLAUDE.md §9 và thêm cùng bộ sinh câu lý thuyết ở Giai đoạn 4.

Ghi chú 2026-10-25: câu chữ §0 được sửa tiếp theo quyết định của người dùng ("Gantt" thành "artifact lời giải theo `kind`" ở I1/I4, ADR-005 bước 3; I4 thêm tái lập của chấm tự luận offline; cơ chế I3 thêm các ý của câu ESSAY; I1 thêm ngoại lệ cho bộ tính điểm chấm câu trả lời của người học). Bảng trên giữ nguyên làm lịch sử của lần duyệt 2026-10-23; câu chữ hiện hành ở spec §0.

### 5. Chấm câu trả lời tự luận

#### 5a. Chấm offline bằng n-gram: MVP nếu còn thời gian (duyệt 2026-10-25)

Chỉ dạng "Trình bày khái niệm X", đáp án tham chiếu là `definition_quote` của một cặp đã xác nhận (mục 1). Contract ở spec §5.14 và §6.9.

- Code chấm offline, không cần LLM, thuộc Deterministic Core (bộ tính điểm, CLAUDE.md §2.1): độ phủ n-gram âm tiết (bigram, trigram) sau chuẩn hoá NFC, theo từng ý tách nguyên văn từ `definition_quote`. Các ý là chuỗi con của `definition_quote` theo đúng quy tắc so khớp của I3 (spec §0): đưa cả hai chuỗi về NFC rồi gộp khoảng trắng, sau đó mới kiểm chuỗi con.
- Kết quả chỉ báo "khớp x/y ý" kèm danh sách ý còn thiếu; KHÔNG có điểm số tuyệt đối. UI ghi rõ giới hạn: người học diễn đạt khác tài liệu thì có thể bị chấm sót ý.
- Cùng `grader_version`, ngưỡng, các ý và câu trả lời (NFC) thì cùng kết quả (I4, spec §0).
- Tham số số học (tách ý theo dấu câu, tối thiểu 4 âm tiết mỗi ý; trọng số bigram/trigram 0.5/0.5; ngưỡng khớp ý 0.5): đề xuất, **chưa hiệu chỉnh**, chờ duyệt; hiệu chỉnh bằng script đánh giá opt-in (CLAUDE.md §2.2). Phần đã duyệt 2026-10-25 là hướng chấm ở gạch đầu dòng thứ nhất và thứ hai của mục này (n-gram âm tiết bigram và trigram, "khớp x/y ý", không điểm tuyệt đối).
- Chỉ làm nếu lát cắt dọc của spec §16 chạy được trước hết ngày 29/10; không thì chuyển sang sau MVP.

#### 5b. `AnswerJudge`: sau MVP (đã duyệt 2026-10-23)

`AnswerJudge` là nâng cấp tuỳ chọn của 5a, chỉ chạy khi có cấu hình LLM. Thiết kế giữ theo CLAUDE.md §2.1, chưa triển khai:

- Rubric gồm các ý, mỗi ý neo vào một trích dẫn nguyên văn. Đáp án mẫu là trích dẫn, không phải văn bản LLM diễn đạt lại.
- LLM chỉ trả verdict cho từng ý (`đạt` / `thiếu` / `sai`) kèm bằng chứng. Bằng chứng phải là chuỗi con (đã chuẩn hoá NFC và khoảng trắng, ADR-006 mục 9) của câu trả lời người học hoặc của tài liệu nguồn; verdict không có bằng chứng hợp lệ bị loại và ý đó chuyển sang "cần người xem".
- Điểm và kết luận đúng/sai do code tính từ verdict. LLM không trả điểm.

Lý do để `AnswerJudge` sau MVP: chấm tự luận bằng LLM cần một bộ câu trả lời mẫu có nhãn để đo độ đúng (script đánh giá opt-in, CLAUDE.md §2.2), và việc này không làm kịp trong thời hạn hackathon. MVP có câu có đáp án xác định (MCQ4, câu trả lời số) và câu ESSAY chấm offline theo 5a (nếu kịp mốc 29/10; duyệt 2026-10-25).

## Hệ quả

- Môn lý thuyết thuần chạy được luồng G1 mà không cần sandbox hay template.
- Câu lý thuyết MVP chỉ kiểm tra **nhận biết khái niệm** (mức `remember`/`understand`), chưa kiểm tra lập luận. Đây là giới hạn có chủ đích.
- Chất lượng câu hỏi phụ thuộc chất lượng cặp khái niệm, nên màn hình xem lại của Vault phải cho sửa và xoá cặp.
- **Chạm bất biến I2 và I4:** câu chữ cũ của I2 ("mã đã khai báo trong template") và I4 ("`(template_sha256, seed)`") chỉ nói về câu có template. Người làm dự án đã duyệt câu chữ mới ngày 2026-10-23 (mục 4); spec §0 và bảng CLAUDE.md §9 đã cập nhật theo (CLAUDE.md §9).
- MVP có câu có đáp án xác định và câu ESSAY chấm offline (nếu kịp mốc 29/10; mục 5a, duyệt 2026-10-25).
- Contract (`question_kind`, model `ConceptPair`, trace cho câu không có template) được viết ở spec §5.8, §5.9, §5.11 và §6.6 (Đợt 2b, 2026-10-24). Đã duyệt 2026-10-25: `TheoryTrace`, `TwinQuestion.template_id` và `topic_key` nullable, `MisconceptionRef.citation` (spec §5.11); các field nullable của `BlueprintSlot` vẫn là đề xuất, chờ duyệt (spec §17 D8).
- Kiểm chứng: test cho thấy cặp có trích dẫn không nguyên văn bị loại; cặp chưa xác nhận không bao giờ được dùng và không làm đổi `pool_sha256`; Vault ít hơn 4 khái niệm đã xác nhận thì vào `review_tickets`; dạng "chọn định nghĩa đúng" chỉ lấy phương án nhiễu cùng chương; cùng `(generator_version, pool_sha256, seed)` thì câu hỏi giống từng byte; cùng nội dung nhập ở dạng NFC hay NFD cho cùng `pool_sha256`; thêm cặp ở môn khác không làm đổi `pool_sha256` của môn này; mọi phương án nhiễu đều có mã thuộc `THEORY_MISCONCEPTIONS`.

## Phương án đã loại

- **Để LLM soạn câu lý thuyết và phương án nhiễu.** Phương án nhiễu do LLM viết là câu sai không có nguồn, còn thân câu là văn bản diễn đạt lại; cả hai trái I3. Muốn kiểm phải dựng thêm một tầng kiểm chứng mà MVP chưa có thời gian làm.
- **Câu đúng/sai sinh bằng cách LLM sửa sai một chi tiết của trích dẫn.** Câu sai do LLM tạo có thể vô tình đúng ở chỗ khác của tài liệu; không có cách tất định để kiểm.
- **Đưa `AnswerJudge` vào MVP.** Không đo được độ đúng trước hạn nộp; điểm chấm sai còn tệ hơn không chấm.
