# ADR 009: Khung VLM đọc trang

- Trạng thái: Chấp nhận (2026-10-29, người dùng duyệt). Nháp "Đề xuất" viết cùng ngày; người dùng trả lời bốn nhóm câu hỏi của nháp rồi chuyển ADR sang Chấp nhận (nguyên văn ở `.claude/state/decisions-answers-2026-10-29.md`, mục "Khung VLM — trả lời 4 nhóm câu hỏi ADR-009"). Một số chi tiết người dùng chưa trả lời trực tiếp được ghi "chi tiết suy ra, chờ xác nhận" tại chỗ; chúng chưa được coi là đã duyệt.
- Ngày: 2026-10-29
- Liên quan: ADR-008 (bộ nhập PDF theo tầng; ADR này là tầng 4 và thay các câu định tuyến VLM ở ADR-008 mục 6, 8, 9), ADR-002 (cô lập tiến trình), ADR-006 (Knowledge Vault). Contract ở spec §5.7, §5.12, §6.5, §11.2, §11.4, §11.5, §13.1.
- Phụ thuộc: nhánh `fix/pdf-tier-text-layer` (luật chọn tầng mới; hàm `needs_ocr_page` / `ocr_reason` trong `infrastructure/document_readers/text_layer.py`) phải merge trước. Lúc viết ADR, hàm này chưa có trên `develop`.
- Nguồn kế hoạch: quyết định của người dùng ngày 2026-10-29 ("Khung VLM 2 ngày", hai khối; "Việc A — trả lời 3 câu", ý 2; prompt v1; "Khung VLM — trả lời 4 nhóm câu hỏi ADR-009").

## Bối cảnh

- ADR-008 để tầng 4 (VLM) ở trạng thái park từ 2026-10-25 (R4). Về VLM, nó chỉ có các câu: "Nếu đã cấu hình VLM, các trang cần xem kỹ được gửi VLM" (mục 6), "thiếu engine OCR thì trang scan được gửi VLM nếu có key" (mục 8). ADR-008 chưa có contract cho kết quả VLM, đường đi của ảnh, việc đồng ý gửi ảnh, cache hay cách kiểm đầu ra.
- Đo trên slide thật (2026-10-29), sau khi sửa luật chọn tầng (Việc A), các trang còn phải OCR cả trang là:
  - trang có lớp chữ rác (TCVN3/VNI);
  - trang rỗng chữ;
  - trang ít chữ mà ảnh phủ phần lớn (bảng hoặc sơ đồ là ảnh).

  Trên các trang này Tesseract đọc kém về bố cục: mất hàng và cột của bảng, không tách khối, lẫn chữ của đồ hoạ khung slide.
- Trang có lớp chữ sạch nhưng có bảng, công thức hoặc hình: chữ đã đúng nhưng thiếu cấu trúc. Tầng 3 (pdfplumber) chưa làm, và `find_tables` nhận nhầm khung slide là bảng (spec §5.7, ghi chú tầng 3).
- Ràng buộc giữ nguyên:
  - **G5, G6** (spec §1.5): không có VLM vẫn chạy đủ luồng; không cấu hình VLM thì không dữ liệu nào rời máy.
  - **LLM Gate** (CLAUDE.md §2.1): đầu ra VLM là dữ liệu không tin cậy và luôn đi vào một cổng kiểm.
  - **I5**: dữ liệu đọc từ ảnh phải được con người xác nhận trước khi dùng như kết quả.
  - **I3**: văn bản vào Vault là văn bản người dùng đã xác nhận; trích dẫn phải nguyên văn.
  - **File PDF không tin cậy** (CLAUDE.md §2.2, ADR-008 mục 3): việc render trang chạy trong worker có giới hạn OS.
  - **Không đặt `temperature`/`top_p`** (spec §11.5).
  - **Không rẽ nhánh theo môn** (CLAUDE.md §1).

## Quyết định

### 1. Định tuyến theo trang

VLM chỉ được gọi cho một trang khi đủ cả ba điều kiện:

1. Khả năng `vlm` khả dụng (mục 6; spec §5.12).
2. Tài liệu có đồng ý gửi ảnh (`send_page_images = true`, mục 7).
3. Trang thuộc một trong hai loại:

| Loại trang | Điều kiện (code) | VLM cho gì | `lines` của trang lấy từ |
|---|---|---|---|
| **A. Trang cần OCR** | `needs_ocr_page(raw, config)` đúng, tức `ocr_reason` ∈ {`empty_text_layer`, `garbled_text_layer`, `sparse_text_on_image`} | Chữ và cấu trúc | VLM, sau khi đối chiếu với OCR (mục 3) |
| **B. Trang có lớp chữ kèm bảng, công thức hoặc hình** | `needs_ocr_page` sai VÀ (`has_table` hoặc `has_formula` hoặc `has_figure`), theo cờ trang của ADR-008 mục 6, 9 | Chỉ cấu trúc (loại khối, thứ tự, hàng và cột) | Lớp chữ, không đổi (lớp chữ thắng chữ VLM, mục 3) |

- Trang không thuộc A hay B thì không gọi VLM và xử lý như ADR-008.
- Thiếu điều kiện 1 hoặc 2 thì không gọi VLM và không tra cache (mục 8). Trang được xử lý đúng như khi không có VLM.
- Trang A vẫn chạy tầng 2 như hiện nay (Tesseract nếu có, RapidOCR nếu bật). Kết quả OCR dùng để đối chiếu (mục 3) và làm phương án dự phòng khi VLM hỏng (mục 9).
- Trang A đọc thành công bằng VLM có `tier_used = "vlm"`.
- Trang B giữ `tier_used = "text_layer"` (người dùng chốt Q3). Nguồn cấu trúc ghi ở `Block.structure_source` (mục 2).
- Định tuyến chỉ dựa vào tín hiệu của trang, không xét tên môn, chương hay topic.

### 2. Contract `Block`, `VlmPageResult`, mở rộng `PageExtraction`

Contract chi tiết ở spec §5.7. Tóm tắt kèm nguồn dữ liệu:

**`Block`**: một khối nội dung của trang. Model này dùng chung với bộ nhập theo vùng sau này (việc B).

| Field | Kiểu | Nguồn |
|---|---|---|
| `type` | `header` \| `title` \| `paragraph` \| `list` \| `table` \| `code` \| `figure` \| `caption` \| `page_number` | VLM (khoá `type` của phần tử mảng, prompt v1); sau này: bộ nhập theo vùng |
| `text` | chuỗi NFC | Trang A: VLM. Trang B: chữ của các dòng lớp chữ đã căn với khối (mục 3). Với `list`: mỗi ý một dòng bắt đầu `- `, ý con thụt 2 dấu cách (prompt v1) |
| `rows` | `list[list[str]] \| null` | VLM (khoá `rows`), chỉ có khi `type = table`, ngược lại `null`. Trang B: chỉ giữ khi mọi ô khớp lớp chữ (mục 3) |
| `order` | số nguyên ≥ 0 | Code: vị trí 0-based của phần tử trong mảng VLM trả về |
| `bbox` | `(x0, y0, x1, y1)` ∈ [0, 1] \| `null` | `null` với mọi khối từ VLM; bộ nhập theo vùng điền sau |
| `structure_source` | `"vlm"` \| `"rules"` | Code: `"vlm"` khi loại khối và thứ tự đến từ VLM; `"rules"` dành cho bộ nhập theo vùng dựa trên luật (việc B). Màn xem lại và `eval_vlm.py` dùng field này để phân biệt (người dùng chốt Q3) |

**`VlmPageResult`**: kết quả đã kiểm của một lần đọc trang bằng VLM. Model này trao đổi giữa các bước trong `services/` và được lưu trong cache (mục 8). Nó KHÔNG xuất hiện trong response API.

| Field | Nguồn |
|---|---|
| `image_sha256` | Code: sha256 của đúng các byte PNG mà tiến trình cha đã kiểm và gửi đi (mục 5) |
| `prompt_version` | Hằng `PROMPT_VERSION` (hiện `"v1"`) |
| `status` | Code: `ok`; `invalid_output` (JSON hỏng sau lần gọi lại); `upstream_error` (lỗi HTTP/mạng sau khi hết lượt thử của adapter); `timeout` |
| `blocks` | VLM, đã parse và validate (mục 9); rỗng khi `status ≠ ok` |
| `attempts` | Code: số lần gọi ở mức việc (1 hoặc 2) |
| `from_cache` | Code: `true` khi lấy từ cache, không gọi mạng |

`VlmPageResult` không chứa tên model, endpoint hay khoá. Tên model chỉ nằm trong khoá cache (mục 8).

**`PageExtraction`** (spec §5.7) thêm:

- `blocks: list[Block] = []`.
- `tier_used = "vlm"` (giá trị đã có trong `TierUsed`): văn bản trang A lấy từ VLM.
- Dòng của trang A: `engine = "vlm"`, `bbox = null`, `conf` = `trace_ratio` (mục 3). Giá trị này thay đề xuất cũ "vlm: 0.0" ở spec §5.7.
- `LineFlag` thêm `VLM_UNTRACED`.
- `ReviewReason` thêm `VLM_READ`, `VLM_UNTRACED_TEXT`, `VLM_TOKEN_DROPPED`, `VLM_FALLBACK`, `VLM_STRUCTURE_UNALIGNED`.
- `DocumentImport` thêm `send_page_images: bool` (mục 7).

**Từ khối sang dòng (trang A):** `lines` vẫn là nguồn chân lý cho việc xác nhận và cho Vault. Code dựng `lines` từ `blocks` theo `order`:

- Mỗi dòng của `text` (tách theo `\n`) thành một `ExtractedLine`.
- `table`: mỗi hàng thành một dòng, các ô nối bằng ` | `.
- `figure`: chỉ phần sau `Nhãn:` vào `lines`. Phần mô tả trước `Nhãn:` do VLM tự viết, không phải chữ của tài liệu. Phần đó KHÔNG vào `lines`, Vault hay trích dẫn (I3) và chỉ còn trong `blocks[].text` (người dùng chấp nhận Q5).
- Dòng dài hơn 2000 ký tự được cắt tại khoảng trắng gần nhất.
- `blocks` không có field nối với dòng. `blocks` không sửa được qua `PATCH …/lines`. Sau khi người dùng sửa dòng, `blocks` vẫn giữ bản đọc ban đầu (người dùng chấp nhận Q4).

### 3. Đầu ra VLM không tin cậy: các cổng kiểm

Bộ đọc trang bằng VLM là một thành phần mới của **LLM Gate** (CLAUDE.md §2.1).

1. **Lớp chữ thắng chữ VLM (trang B).**
   - `lines` của trang B lấy nguyên từ lớp chữ.
   - Căn khối: chuẩn hoá NFC, gộp khoảng trắng, rồi tìm `text` của khối như một dãy dòng liên tiếp của lớp chữ.
   - Khối căn được: `text` được thay bằng chữ của đúng các dòng đó. Khối `table` chỉ được giữ khi mọi ô khác rỗng là chuỗi con (sau chuẩn hoá) của lớp chữ của trang.
   - Khối không căn được bị bỏ (các khối còn lại giữ `order`, nên `order` tăng ngặt nhưng có thể có khoảng trống). Trang nhận `VLM_STRUCTURE_UNALIGNED`.
   - VLM không bao giờ thêm chữ vào trang B.
2. **Số và mã đối chiếu RapidOCR (trang A).**
   - Token lấy theo regex của ADR-008 mục 11 (`0x[0-9A-F]+|#?-?\d+`, NFC) từ `lines` của VLM và từ lượt RapidOCR của cùng trang.
   - So theo **tập đếm (multiset) của cả trang**, vì dòng VLM không có `bbox` (người dùng chấp nhận Q14).
     - Token VLM không còn trong tập RapidOCR ⇒ tạo một `TokenMismatch` (`primary_value` là token VLM, `secondary_value = null`); dòng có cờ `TOKEN_MISMATCH`, trang có `TOKEN_MISMATCH`, `cross_check.status = "mismatch"`.
     - Token RapidOCR không còn trong tập VLM (VLM bỏ sót số) ⇒ trang có `VLM_TOKEN_DROPPED`.
     - Không có lượt RapidOCR ⇒ `cross_check.status = "unavailable"`, trang có `CROSS_CHECK_UNAVAILABLE`.
   - **Giới hạn đã biết (ghi theo yêu cầu của người dùng):**
     - So theo tập đếm cả trang KHÔNG bắt được số bị đổi chỗ giữa các dòng, ví dụ hai ô bảng tráo giá trị cho nhau, vì tập đếm không đổi.
     - Đối chiếu theo vị trí (ghép dòng theo hộp như ADR-008 mục 11) làm sau, khi kết quả có `bbox` (bộ nhập theo vùng).
   - **Chặn theo I5 (người dùng chốt 2026-10-29):**
     - `POST …/pages/{n}/confirm` trả 409 khi trang còn chỗ số/mã lệch chưa giải quyết, tức `cross_check.mismatches` khác rỗng.
     - "Giải quyết" một chỗ lệch là một trong hai cách: sửa dòng chứa nó, hoặc bấm "Giữ giá trị này" cho chỗ lệch đó (VLM có thể đúng, OCR sai).
     - "Giữ giá trị này" đi qua `PATCH …/lines` (field `kept_mismatches`, spec §6.5), tăng `version` và được ghi vào `PageEditAudit` như một lần sửa.
     - Test phủ cả hai đường.
     - Chi tiết suy ra, chờ xác nhận: luật 409 áp cho trang `tier_used = "vlm"`, đúng phạm vi câu hỏi Q6 của nháp. Mở rộng cho trang OCR (Tesseract với RapidOCR) cần người dùng quyết riêng. `VLM_TOKEN_DROPPED` chỉ làm trang `needs_review`, không gây 409.
3. **Chữ VLM không có dấu vết trong OCR ⇒ cờ "có thể bịa" (trang A).**
   - `trace_ratio` của một dòng VLM là tỉ lệ từ của dòng có mặt trong tập đếm từ của lượt OCR chính (Tesseract). Từ ở đây là cụm không trắng, chuẩn hoá NFC, chữ thường, bỏ dấu câu đầu/cuối.
   - Không có Tesseract thì so với RapidOCR, sau khi bỏ dấu thanh ở cả hai phía (từ điển RapidOCR thiếu chữ có dấu thanh, ADR-008 "Kết quả đo").
   - `conf` của dòng = `trace_ratio`. Không có lượt OCR nào thì `conf = 0.0`.
   - `trace_ratio < VLM_TRACE_MIN` (field cấu hình, mặc định 0.5) ⇒ dòng có cờ `VLM_UNTRACED`, trang có `VLM_UNTRACED_TEXT`.
   - Dòng chứa `[không đọc được]` cũng nhận `VLM_UNTRACED`.
   - Đây là tín hiệu để người dùng xem kỹ, không chứng minh chữ đúng.
4. **Mọi trang có văn bản từ VLM vẫn qua màn xem lại.**
   - Trang `tier_used = "vlm"` luôn có `VLM_READ`, nên luôn là `needs_review` và không được xác nhận theo lô (spec §6.5).
   - `VLM_READ` không bị bỏ khi sửa dòng.
   - VLM không xoá lý do nào đã có theo ADR-008.
5. **I1:** VLM không tính số nào. Mọi số trên trang là số VLM đọc từ ảnh, chịu cổng 2 và sự xác nhận của người dùng.

### 4. Adapter: một client OpenAI Chat Completions cho cloud và local

- Dùng lại `OpenAiChatClient` (`infrastructure/llm_client.py`) sau `Protocol` `ChatCompletionClient` (`domain/llm.py`). Không thêm dependency, không viết adapter riêng cho từng nhà cung cấp.
- Cùng một client dùng cho:
  - nhà cung cấp cloud tương thích OpenAI có nhận `image_url`;
  - server local (Ollama, llama-server, LM Studio) qua `http://127.0.0.1:<port>/v1`. `check_base_url` đã cho phép `http://` khi máy chủ là loopback.
- **Khoá trống với loopback (người dùng chốt 2026-10-29):**
  - Khi tên máy của `HOLOCRON_VLM_BASE_URL` thuộc `LOOPBACK_HOSTS` (`127.0.0.1`, `::1`, `localhost`), `HOLOCRON_VLM_API_KEY` được để trống. Khi đó client không gửi header `Authorization`.
  - Endpoint không phải loopback vẫn bắt buộc có khoá.
  - Contract của `infrastructure/llm_config.py` cho VLM đổi tương ứng (`VLM_REQUIRED_VARIABLES` thành điều kiện có xét loopback). Contract của LLM văn bản (`HOLOCRON_LLM_*`) không đổi.
- **Request:**
  - Một message `user`; `content` là mảng gồm phần `text` (prompt v1 nguyên văn) và phần `image_url` với `url = "data:image/png;base64,<…>"`.
  - Không gửi văn bản nào của tài liệu (`HOLOCRON_VLM_MODE = images_only`).
- **Contract nội bộ:**
  - `ChatMessage.content` đổi từ `str` thành `str | list[ContentPart]`. `ContentPart` là union phân biệt theo `type` (`text`, `image_url`).
  - Model này nội bộ, không có trong response API, không vào `types/holocron.ts`.
  - Việc LLM văn bản tiếp tục gửi `str` không đổi.
- Không gửi `temperature`, `top_p`. Tính tất định khi nhập lại đến từ cache (mục 8).
- `max_tokens` là field cấu hình `VlmTaskLimits.max_tokens` (mặc định 4096).
- VLM được gọi ở **tiến trình cha** (service nhập tài liệu). Worker đọc PDF không nhận khoá và không gọi VLM.

**Prompt v1** (người dùng gửi 2026-10-29, chép nguyên văn; hằng `PROMPT_VERSION = "v1"`):

```text
Bạn chép lại nội dung một trang tài liệu học tập tiếng Việt từ ảnh.
Chỉ chép những gì NHÌN THẤY. Không thêm, không sửa lỗi chính tả, không suy đoán.
Chỗ không đọc được ghi đúng "[không đọc được]".
Trả về DUY NHẤT một mảng JSON, mỗi phần tử là một khối theo thứ tự đọc:
{"type": "header|title|paragraph|list|table|code|figure|caption|page_number",
 "text": "...",                       // list: mỗi ý một dòng bắt đầu "- ", ý con thụt 2 dấu cách
 "rows": [["ô", "ô"], ...]            // chỉ khi type = table
}
Bố cục nhiều cột: đọc hết cột trái rồi mới sang cột phải.
Bảng: giữ đúng hàng và cột, ô xuống dòng vẫn là một ô.
Hình hoặc sơ đồ: type = figure, text = mô tả ngắn + "Nhãn:" + chữ trong hình.
Giữ nguyên chữ số, mã lệnh, ký hiệu.
```

**Câu nhắc khi gọi lại** (người dùng chốt Q10, nguyên văn; thuộc `PROMPT_VERSION = "v1"`):

```text
Chỉ trả về một mảng JSON đúng schema, không kèm giải thích.
```

- Đổi bất kỳ ký tự nào của hai văn bản trên thì phải tăng `PROMPT_VERSION`, vì giá trị này nằm trong khoá cache.

### 5. Đường đi an toàn của ảnh: PNG qua stdout

Người dùng chốt ngày 2026-10-29 (Q1, chọn 2). Lý do: máy chạy Windows, nơi `RLIMIT_FSIZE` không có cơ chế tương đương. Giữ bất biến "worker không ghi file" trên mọi OS thì không có rủi ro lách đường dẫn.

1. **Worker render và mã hoá.**
   - Worker đọc PDF (ADR-008 mục 3) render trang thành PNG, đã che đồ hoạ template lặp (mục 10).
   - Ảnh được thu về cạnh dài ≤ `HOLOCRON_VLM_MAX_EDGE_PX` (2048) **trước khi mã hoá**.
   - Worker gửi ảnh qua stdout trong một message riêng, ví dụ `VlmImageMessage {kind: "vlm_image", page_index, width_px, height_px, size_bytes, sha256, png_base64}`.
   - Worker không ghi file nào và giữ `RLIMIT_FSIZE = 0` trên Unix như ADR-008 mục 3.
   - Về worker: đây là tiến trình con có giới hạn OS theo ADR-008 mục 3 (RAM, CPU, `RLIMIT_FSIZE = 0` trên Unix, Job Object trên Windows), không phải sandbox chạy template. Mạng không bị chặn ở mức OS. Worker không gọi mạng vì code của nó không mở kết nối nào và nó không nhận khoá hay endpoint.
2. **Chỉ nâng trần byte cho loại message ảnh.**
   - Trần độ dài dòng chung của giao thức worker (`max_line_bytes` trong `pdf_reader.py`) giữ nguyên cho mọi loại message khác.
   - Một dòng chỉ được vượt trần chung khi bắt đầu bằng tiền tố cố định của message ảnh. Dòng đó vẫn không được vượt trần riêng, tính từ `HOLOCRON_VLM_MAX_IMAGE_BYTES` (độ dài base64 cộng phần đầu cố định). Vượt trần ⇒ message bị loại, trang nhận `VLM_FALLBACK`. Cách thực hiện trong bộ tách dòng: chi tiết suy ra, chờ xác nhận.
3. **Tiến trình cha kiểm trên chính các byte đã giải mã:**
   - giải mã base64 đúng chuẩn; số byte bằng `size_bytes` và ≤ `HOLOCRON_VLM_MAX_IMAGE_BYTES`;
   - 8 byte đầu là magic PNG `89 50 4E 47 0D 0A 1A 0A`;
   - chunk IHDR cho rộng và cao khớp message, cạnh dài ≤ `HOLOCRON_VLM_MAX_EDGE_PX`;
   - sha256 của các byte khớp `sha256`.

   Sai bất kỳ điều nào thì không gửi ảnh, trang đi đường OCR (trang A) hoặc giữ lớp chữ (trang B) và nhận `VLM_FALLBACK`. Log chỉ ghi mã lỗi.
4. **Chỉ giữ trong bộ nhớ.** Tiến trình cha giữ ảnh trong bộ nhớ cho tới khi gọi VLM xong. Ảnh không được ghi ra đĩa. Thứ duy nhất được lưu là kết quả đọc trong cache (mục 8), khoá theo sha256 của ảnh; cache không chứa byte ảnh.
5. **Test bắt buộc (người dùng chốt):**
   - message ảnh vượt trần bị loại;
   - ảnh sai magic bị loại;
   - worker vẫn không ghi được file (Unix: `RLIMIT_FSIZE = 0`).

### 6. Giới hạn và cấu hình

Biến môi trường (bảng ở spec §11.2):

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `HOLOCRON_VLM_MODE` | `off` | `off`: không gửi gì tới VLM, dù đã cấu hình địa chỉ và model. `images_only`: chỉ gửi ảnh trang kèm prompt v1 và câu nhắc cố định, không gửi văn bản tài liệu. Giá trị khác ⇒ `ConfigError` (chỉ nêu tên biến) |
| `HOLOCRON_VLM_TIMEOUT_S` | loopback `120`, cloud `60` | Cận tổng cho một lần gọi VLM ở mức việc, riêng với `HOLOCRON_LLM_TIMEOUT_S`. Đặt biến thì ghi đè cả hai mặc định. Không phải số dương hữu hạn ⇒ `ConfigError` |
| `HOLOCRON_VLM_CONCURRENCY` | loopback `1`, cloud `2` | Số trang gọi VLM cùng lúc ở tiến trình cha. Đặt biến thì ghi đè; phải là số nguyên 1–8 |
| `HOLOCRON_VLM_MAX_EDGE_PX` | `2048` | Cạnh dài tối đa của ảnh gửi đi. Ảnh vẫn không vượt 300 DPI và 25 megapixel (ADR-008 mục 4) |
| `HOLOCRON_VLM_MAX_IMAGE_BYTES` | `4194304` (4 MiB) | Trần byte của một ảnh PNG, cũng là cơ sở tính trần của message ảnh (mục 5) |

- "Loopback" nghĩa là tên máy của `HOLOCRON_VLM_BASE_URL` thuộc `LOOPBACK_HOSTS`.
- Giá trị loopback 1/120 s và cloud 2/60 s do người dùng chốt (Q13).
- Ngưỡng và giới hạn phụ là field có giá trị mặc định của model cấu hình ở `infrastructure/`, không phải biến môi trường (theo quy tắc của ADR-008 mục 12):
  - `VLM_TRACE_MIN = 0.5`;
  - `VlmTaskLimits`: `json_retry_max = 1`, `max_response_chars`, `max_json_depth`, `max_parse_starts`, `max_tokens = 4096`, `max_blocks_per_page = 200`, `max_table_rows = 200`, `max_table_cols = 30`;
  - số hộp che tối đa mỗi trang `= 16`.
- **Khả năng `vlm`** khả dụng ⇔ đủ ba điều kiện:
  - VLM đã cấu hình: có `HOLOCRON_VLM_BASE_URL` và `HOLOCRON_VLM_MODEL`; có thêm `HOLOCRON_VLM_API_KEY`, trừ khi endpoint là loopback;
  - `HOLOCRON_VLM_MODE = images_only`;
  - code đã có (`IMPLEMENTED_CAPABILITIES`).
- Lý do khi không khả dụng, xét theo thứ tự: `not_implemented`, `vlm_not_configured`, `vlm_disabled`. `vlm_disabled` là mã mới (người dùng chốt), dùng khi `HOLOCRON_VLM_MODE = off`. Khả năng được kiểm lúc khởi động, không gọi mạng.
- Khả năng `vlm` chỉ bật bộ đọc trang của bộ nhập tài liệu (spec §6.5). Nó không bật dual-pass VLM của upload đề (spec §6.1, mục 12).

### 7. Đồng ý gửi ảnh và dữ liệu rời máy

Người dùng chốt ngày 2026-10-29 (Q2, Q7, Q8): đồng ý **theo từng tài liệu**.

- `POST /v1/documents` nhận field form `send_page_images` (spec §6.5). Giá trị được lưu cùng tài liệu và trả về trong `DocumentImport.send_page_images` (spec §5.7). Khi không gửi field, mặc định tính theo endpoint:
  - **Endpoint cloud:** mặc định TẮT. Công tắc trên UI ghi rõ nơi nhận ảnh.
  - **Endpoint loopback:** mặc định BẬT. Nhãn của công tắc: "Đọc ảnh bằng AI chạy trên máy này".
- Khi khả năng `vlm` không khả dụng, `send_page_images` luôn được lưu là `false` (chi tiết suy ra, chờ xác nhận).
- UI phải biết endpoint là loopback hay không, và tên bên nhận, mà không được thấy endpoint (spec §5.12). `RuntimeInfo` thêm hai field (chi tiết suy ra, chờ xác nhận):
  - `vlm_local: bool`: tên máy của `HOLOCRON_VLM_BASE_URL` thuộc `LOOPBACK_HOSTS`;
  - `vlm_data_recipient: str | null`: nhãn người dùng đặt qua biến mới `HOLOCRON_VLM_DATA_RECIPIENT`, cùng quy tắc loại giá trị như `llm_data_recipient`. Khi `null`, UI hiện câu cố định "endpoint VLM đã cấu hình (chưa đặt tên bên nhận)".
- Công tắc chỉ hiện khi khả năng `vlm` khả dụng.
- **Dữ liệu rời máy** (spec §13.1):
  - Với endpoint cloud, ảnh trang (PNG, đã che đồ hoạ template lặp) rời máy tới endpoint VLM khi khả năng `vlm` khả dụng và tài liệu có `send_page_images = true`.
  - Với endpoint loopback, ảnh không rời máy.
- **Không lộ khoá hay endpoint.**
  - Log của bộ đọc VLM chỉ ghi: số trang, `status`, số lần gọi, `from_cache`, thời gian, số khối, số token lệch.
  - Log không ghi khoá, địa chỉ endpoint, tên model, prompt, phản hồi hay byte ảnh.
  - Thông điệp lỗi theo quy tắc của `LlmUpstreamError`.

### 8. Cache

- Bảng SQLite mới, thêm qua migration `0004_vlm_cache.sql` (người dùng chấp nhận Q15; nếu số 0004 đã bị dùng khi merge thì lấy số kế tiếp):

  ```sql
  vlm_cache(
    image_sha256   TEXT NOT NULL,
    model          TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    result_json    TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (image_sha256, model, prompt_version)
  )
  ```

- `result_json` là `VlmPageResult` có `status = "ok"`. Giá trị này được validate lại khi đọc. Chỉ kết quả `ok` được cache.
- `model` chỉ nằm trong DB cục bộ, không vào log hay response. Cache không chứa byte ảnh.
- Cache được truy cập qua repository sau `Protocol` (ví dụ `VlmCacheRepository`), cấp qua `Depends`. Bản in-memory dùng khi không đặt `HOLOCRON_DATA_DIR` và trong test. Không dùng dict module-level.
- Cache không gắn với tài liệu: xoá tài liệu không xoá kết quả VLM trong cache (người dùng chấp nhận Q9).
- Tính tất định: cùng PDF, cùng pypdfium2, cùng cấu hình render và che ⇒ cùng byte PNG ⇒ cùng kết quả. Vì vậy nhập lại cho `PageExtraction` giống hệt, trừ `confirmed_at`.

### 9. Parse JSON an toàn và lỗi

- Tách tiện ích parse JSON khỏi `services/concept_proposal.py` (`json_depth_exceeds`, `_json_arrays`) sang `services/llm_json.py` (người dùng chấp nhận Q15). Đây là refactor không đổi hành vi; test của đề xuất cặp khái niệm phải xanh mà không sửa.
- Giới hạn trước khi parse:
  - `max_response_chars`;
  - `max_json_depth`, đếm ngoài chuỗi JSON;
  - `max_parse_starts`.
- Bộ parse chịu được rào ```` ``` ```` và văn bản thừa quanh mảng JSON. Không dựa vào `response_format`.
- Validate bằng Pydantic:
  - `type` thuộc danh sách; `text` là chuỗi; `rows` là mảng các mảng chuỗi và chỉ có khi `type = table`;
  - khoá thừa bị bỏ qua;
  - các giới hạn của `VlmTaskLimits`;
  - văn bản được chuẩn hoá NFC.
- Lỗi parse hoặc validate ⇒ gửi lại **một** lần: cùng message của lần đầu, thêm đúng một phần `text` là câu nhắc cố định ở mục 4. Phản hồi hỏng của lần đầu không được gửi lại cho VLM.
- Các trường hợp sau dẫn tới cùng một cách xử lý: JSON hỏng lần nữa; lỗi HTTP sau khi hết lượt thử của adapter; quá `HOLOCRON_VLM_TIMEOUT_S`; ảnh bị loại ở mục 5. Cách xử lý:
  - trang A dùng kết quả OCR;
  - trang B giữ lớp chữ, `blocks` rỗng;
  - cả hai nhận `VLM_FALLBACK`;
  - trang không thành `failed` vì lỗi VLM.

### 10. Che đồ hoạ template lặp

Thiết kế an toàn do người dùng chốt ngày 2026-10-29 ("Việc A — trả lời 3 câu", ý 2), làm cùng đường render ảnh của khung VLM.

- **Định danh theo nội dung và vị trí:** (băm nội dung, hộp toạ độ đã làm tròn theo khổ trang).
  - Với ảnh nhúng, băm trên luồng dữ liệu thô chưa giải nén, để không phải giải nén ảnh nhúng (ADR-008 mục 4).
  - Bước làm tròn và cách băm đường vẽ: chi tiết suy ra, chờ xác nhận.
- **Template lặp** khi cùng định danh xuất hiện trên **≥ 5 trang VÀ ≥ 60% số trang**.
- **Không bao giờ che vùng phủ ≥ 50% diện tích trang.** Vì vậy trang scan (cả trang là một ảnh) không bị che gì.
- **Không che phần giao với hộp chữ** của lớp chữ.
- **Tối đa 16 hộp che mỗi trang**; vượt trần thì không che trang đó.
- **Test phủ định cho scan:** PDF scan tự tạo có các trang giống nhau ⇒ không vùng nào bị che.
- Cách che: tô màu nền lên ảnh đã render; không sửa PDF.
- Phạm vi áp dụng: ảnh gửi VLM **và** ảnh đưa vào OCR (Tesseract, RapidOCR) (người dùng chấp nhận Q11).

### 11. Đo

- Script opt-in `holocron/backend/scripts/eval_vlm.py`:
  - chạy ngoài pytest;
  - đọc khoá từ biến môi trường;
  - không chạy trong CI hay pre-commit (CLAUDE.md §2.2).
- Đáp án ở `eval_data/<chương>/<tên>.json` (đã nằm trong `.gitignore`), dạng mảng khối theo prompt v1 cho từng trang.
- Script báo riêng kết quả theo `structure_source`.
- Tiêu chí xong (người dùng chốt):

| Chỉ số | Ngưỡng |
|---|---|
| CER trên trang A (chữ chỉ có trong ảnh), NFC, tính cả dấu | ≤ 3% |
| Loại khối đúng | ≥ 90% |
| Ô bảng đúng | ≥ 90% |
| Số/mã VLM đọc lệch so với đáp án bị gắn cờ (`TOKEN_MISMATCH` hoặc `VLM_UNTRACED`) | 100% |
| Trang B: `lines` khớp kết quả không VLM | 100% |
| Nhập lại cùng file, cùng cấu hình: `PageExtraction` giống hệt (trừ `confirmed_at`) | 100% |

- Chỉ số "100% số/mã lệch bị gắn cờ" được đo trên bộ eval. Giới hạn ở mục 3 (số đổi chỗ giữa các dòng) có thể làm chỉ số này dưới 100%. Khi đó báo cáo đo phải liệt kê từng trường hợp.

### 12. Ngoài phạm vi

- Đóng gói VLM (model, runtime) trong app.
- Nhập đề thi bằng VLM: dual-pass của upload đề (spec §6.1, §7.1) vẫn dùng `MockDualPassExtractor`.
- Chữ viết tay.
- Màn xem lại theo khối.
- Dùng `blocks` để cắt chunk Vault: Vault vẫn cắt từ `lines` đã xác nhận (spec §12.1).
- Gửi văn bản tài liệu kèm ảnh.
- Đối chiếu số theo vị trí (cần `bbox`, mục 3).

### 13. Test

Các test nằm trong `holocron/backend/tests/`, không gọi mạng (client VLM là stub), dùng file mẫu tự tạo:

- **Định tuyến:**
  - không đồng ý, hoặc `HOLOCRON_VLM_MODE = off` ⇒ không gọi VLM, không tra cache, `PageExtraction` giống hệt khi không có VLM;
  - trang A, trang B và trang khác đi đúng nhánh.
- **Đồng ý:** mặc định của `send_page_images` theo loopback/cloud.
- **Ảnh qua stdout:**
  - message ảnh vượt trần bị loại;
  - ảnh sai magic bị loại;
  - sha lệch hoặc kích thước lệch bị loại;
  - dòng lớn không có tiền tố message ảnh vẫn bị loại ở trần chung;
  - worker vẫn không ghi được file.
- **Lớp chữ thắng:** trang B có VLM trả chữ khác ⇒ `lines` không đổi, khối lệch bị bỏ, trang có `VLM_STRUCTURE_UNALIGNED`.
- **Số lệch:**
  - VLM `9`, RapidOCR `8` ⇒ `TOKEN_MISMATCH`;
  - VLM bỏ sót số ⇒ `VLM_TOKEN_DROPPED`.
- **409 và giải quyết:** xác nhận trang còn chỗ lệch ⇒ 409; giải quyết bằng sửa dòng ⇒ xác nhận được; giải quyết bằng "Giữ giá trị này" ⇒ xác nhận được, có audit, `version` tăng 1.
- **Dấu vết OCR:** dòng không có dấu vết OCR ⇒ `VLM_UNTRACED`, `VLM_UNTRACED_TEXT`.
- **Xem lại:** trang `tier_used = "vlm"` luôn có `VLM_READ`.
- **Parse:** phản hồi hỏng ⇒ gọi lại đúng một lần, request có câu nhắc cố định; hỏng lần nữa ⇒ `VLM_FALLBACK`.
- **Cache:** lần nhập thứ hai không gọi client và cho kết quả giống hệt.
- **Bí mật:**
  - log và lỗi không chứa khoá, endpoint hay tên model;
  - loopback với khoá trống không gửi `Authorization`;
  - cloud với khoá trống ⇒ `vlm_not_configured`.
- **Che template:** đủ các tiêu chí ở mục 10, kèm test phủ định cho scan.

Người dùng chấp nhận Q12: gắn các test chặn I5 ở trên vào dòng I5 của CLAUDE.md §9. Việc sửa CLAUDE.md làm riêng khi test đã có (orchestrator hỏi người dùng lúc đó); ADR này không sửa CLAUDE.md.

## Hệ quả

- Khi người dùng bật VLM, trang scan và trang ảnh có bảng hoặc sơ đồ được đọc kèm cấu trúc. Khi không bật, không có gì thay đổi (G5, G6).
- Chữ của trang có lớp chữ không bao giờ bị VLM đổi.
- Contract mới:
  - `Block` (có `structure_source`), `PageExtraction.blocks`, các `ReviewReason` và `LineFlag` mới;
  - `DocumentImport.send_page_images`, field form `send_page_images`;
  - `kept_mismatches` của `PATCH …/lines` và 409 số lệch ở `POST …/confirm`;
  - mã lý do `vlm_disabled`;
  - `RuntimeInfo.vlm_local`, `vlm_data_recipient` (chi tiết suy ra);
  - khoá VLM được trống với loopback.

  `types/holocron.ts` cập nhật cùng PR triển khai (CLAUDE.md §2.3). `ChatMessage.content` đổi kiểu (nội bộ).
- VLM hết trạng thái park, nhưng chỉ cho bộ đọc trang. Gateway của ban tổ chức có nhận ảnh hay không vẫn phải kiểm bằng `eval_vlm.py`. Dual-pass của upload đề vẫn park.
- Chi phí: mỗi trang A/B tốn một lần gọi VLM (hai lần khi JSON hỏng). Cache làm lần nhập lại miễn phí.
- Giao thức worker có thêm một loại message lớn. Trần riêng cho loại này giữ bộ nhớ của tiến trình cha có cận.
- Rủi ro còn lại:
  - VLM bịa chữ đúng chỗ OCR cũng sai thì máy không phát hiện được, chỉ con người phát hiện (`VLM_READ`, I5);
  - số đổi chỗ giữa các dòng không bị bắt (mục 3);
  - prompt injection trong ảnh chỉ ảnh hưởng văn bản trang, vốn luôn qua xem lại; VLM không có công cụ nào để gọi.
- ADR-008 mục 6, 8, 9 được sửa cùng nhánh để định tuyến VLM theo mục 1 của ADR này (dòng "Sửa đổi 2026-10-29" ở ADR-008).

## Phương án đã loại

- **VLM cho mọi trang.** Tốn tiền và thời gian. Trang có lớp chữ sạch đã đúng chữ.
- **VLM thay hẳn OCR trên trang scan.** Mất lượt đối chiếu, không phát hiện được chữ bịa, không có dự phòng khi VLM hỏng.
- **Chữ VLM thắng lớp chữ.** Lớp chữ của PDF số chính xác nhất.
- **Worker ghi PNG vào workdir, message chỉ mang tên file và sha (phương án của nháp).** Phải nới `RLIMIT_FSIZE` của worker. Trên Windows không có giới hạn tương đương. Thêm rủi ro lách đường dẫn (symlink, junction, `..`). Người dùng chọn stdout (Q1).
- **Tiến trình cha tự render.** Trái ADR-008 mục 3: file PDF không tin cậy chỉ được mở trong worker.
- **Worker tự gọi VLM.** Worker xử lý file không tin cậy; cho nó khoá và quyền mạng là mở rộng bề mặt tấn công.
- **Đặt `temperature = 0`.** Trái spec §11.5, và không bảo đảm tất định. Cache cho tính tất định thật sự.
- **Adapter riêng cho từng nhà cung cấp.** Cả cloud lẫn server local phổ biến đều có endpoint tương thích OpenAI nhận `image_url`.
- **Gọi lại mà không kèm câu nhắc, hoặc gửi kèm phản hồi hỏng.** Người dùng chọn một câu nhắc cố định (Q10). Không gửi lại phản hồi hỏng để tránh đưa đầu ra không tin cậy trở lại prompt.
- **Đồng ý gửi ảnh là một công tắc chung của app.** Người dùng chọn đồng ý theo từng tài liệu (Q7).
