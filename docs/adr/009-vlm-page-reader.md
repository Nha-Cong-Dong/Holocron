# ADR 009: Khung VLM đọc vùng và Kết nối AI (tên cũ: Khung VLM đọc trang)

- Trạng thái: Chấp nhận (2026-10-29, người dùng duyệt). Nháp "Đề xuất" viết cùng ngày; người dùng trả lời bốn nhóm câu hỏi của nháp rồi chuyển ADR sang Chấp nhận (nguyên văn ở `.claude/state/decisions-answers-2026-10-29.md`, mục "Khung VLM — trả lời 4 nhóm câu hỏi ADR-009" và "VLM 1.2 — sửa test + chi tiết ADR-009").
- Sửa đổi 2026-11-03 (bản cập nhật X02b, nhánh `docs/adr-009-ai-connect`): cập nhật theo ADR-010 (khối, `CropRequest`, cache AI) và thêm mục 12 "Kết nối AI" (khoá API, nhà cung cấp, chi phí, công tắc đồng ý). Trạng thái không đổi. Bảng giữ/đổi/bỏ so với bản 2026-10-29 ở mục 0. Nhãn nguồn của từng quyết định:
  - "duyệt 2026-10-29 (ADR-009)": quyết định của bản 2026-10-29, còn giữ.
  - "duyệt 2026-11-01 (ADR-009)": thuộc phạm vi người dùng duyệt ở `.claude/state/briefs/hackathon-2026-11-01.md` mục 1 và mục 3 (trích nguyên văn dưới đây; file không nằm trong git).
  - "duyệt 2026-11-03 (người dùng uỷ quyền chọn phương án đề xuất)": ngày 2026-11-03 người dùng giao orchestrator chọn phương án đề xuất cho tám câu hỏi mở của X02b; phương án đã chọn ghi ở `.claude/state/briefs/x02b-open-questions-proposals.md` (mục 1–8).
  - "đề xuất suy ra, chờ xác nhận": tên, ngưỡng hay chi tiết do người viết đặt để code không phải đoán. Chưa được duyệt.
- Ngày: 2026-10-29; cập nhật 2026-11-03
- Liên quan: ADR-008 (bộ nhập PDF theo tầng; mục 6, 8, 9 sửa theo ADR này), ADR-010 (khối, `CropRequest`, cờ khối, migration cache AI; ADR này KHÔNG sửa ADR-010), ADR-002 (cô lập tiến trình), ADR-006 (Knowledge Vault), ADR-007 (job đề xuất cặp). Contract ở spec §5.7, §5.12, §5.16, §6.5, §6.11, §11.2, §11.4, §11.5, §13.1.
- Nguồn kế hoạch: quyết định của người dùng ngày 2026-10-29 ("Khung VLM 2 ngày"; prompt v1; "Khung VLM — trả lời 4 nhóm câu hỏi ADR-009"; "VLM 1.2"), ngày 2026-11-01 (file quyết định mục 0, 1, 3) và ngày 2026-11-03 (uỷ quyền chọn phương án đề xuất).

### Nguyên văn phần duyệt 2026-11-01 liên quan (file quyết định, trích)

Mục 1 (vai trò VLM/LLM):

> - Văn bản của trang CÓ lớp chữ LUÔN lấy từ lớp chữ; VLM/LLM chỉ được đề xuất CẤU TRÚC (loại khối, ranh giới, thứ tự đọc, cấp tiêu đề, gom mục, nhãn definition/example/exercise) bằng cách tham chiếu id khối/dòng có sẵn, không trả chữ.
> - Trang/vùng KHÔNG có lớp chữ hoặc khối có cờ (bảng ảnh, sơ đồ, scan xấu): VLM đọc vùng cắt (~896–1024 px) trả JSON khối (prompt v1 ở .claude/state/vlm/prompt-v1.txt làm điểm xuất phát). VLM đọc ĐỘC LẬP, không nhận token OCR (ADR-009). Chữ VLM chỉ được nhận khi khớp OCR độc lập theo ngưỡng cấu hình; mọi token số/mã phải trùng tuyệt đối; không đạt ⇒ khối giữ ảnh cắt + cờ. Câu do AI viết lại không bao giờ là trích dẫn (I3).
> - Bảng ảnh: VLM trả hàng/ô; ô có số/mã đối chiếu OCR; lệch ⇒ cờ (BLOCKING chỉ trong bảng, giữ quyết định 409).
> - Cache theo sha256(ảnh)+model+PROMPT_VERSION; chỉ gửi trang/vùng cần; có trần số trang mỗi tài liệu; timeout/retry qua llm_client hiện có; test KHÔNG gọi mạng (stub). Có công tắc đồng ý gửi ảnh (mặc định tắt) — UI ở X12.

Mục 3 (NGƯỜI DÙNG ĐÃ DUYỆT, phần liên quan ADR này):

> - Mở lại nhánh feat/vlm-page-reader: KHÔNG rebase (settings cấm) — tạo nhánh mới từ develop và cherry-pick/chép ADR-009 + SPEC, cập nhật ADR-009 theo khoá cấp khối + Block của ADR-010; ChatMessage.content hỗ trợ phần ảnh (image_url) trong llm_client; VisionReader + cache (migration mới); dùng gateway ban tổ chức khi có biến môi trường.
> - Gửi ảnh trang/vùng tài liệu tới endpoint cấu hình khi người dùng bật công tắc đồng ý.
> - Quản lý API key do người dùng nhập (V04) — đây là thay đổi bảo mật, NGƯỜI DÙNG ĐÃ DUYỆT với điều kiện: key chỉ nằm ở backend, lưu file cấu hình riêng trong thư mục dữ liệu người dùng (ngoài repo, quyền đọc chỉ chủ tài khoản; Windows dùng DPAPI qua ctypes nếu làm được KHÔNG thêm dependency, không thì file + cảnh báo rõ trong UI và OPEN), KHÔNG bao giờ trả key đầy đủ qua API (chỉ 4 ký tự cuối), KHÔNG ghi log/trace/exception/report, KHÔNG để frontend lưu localStorage; endpoint đọc/ghi key chỉ nhận từ 127.0.0.1 + token truy cập hiện có (routers/access.py); biến môi trường HOLOCRON_LLM_*/VLM_* vẫn hoạt động và được ưu tiên khi có. Base URL người dùng nhập phải là https, trừ loopback http đã cho phép trong llm_config. Test: key không xuất hiện trong mọi response/log (property test).
> - Theo dõi chi phí: đếm token vào/ra và số ảnh theo nhà cung cấp, ước tính USD theo bảng giá cấu hình được; trần chi tiêu mỗi nhà cung cấp (mặc định gateway ban tổ chức 4 USD để chừa dư); chạm trần ⇒ chuyển nhà cung cấp kế tiếp hoặc lùi về offline, báo trên UI. Không gọi mạng trong test.
>
> KHÔNG được duyệt (vẫn DỪNG VÀ HỎI, ghi vào OPEN rồi làm task khác): thêm dependency hoặc file model mới (...), đổi định nghĩa I1–I5, sandbox, hạ cổng, merge, tag, để chữ VLM/LLM vào kho mà không qua cổng đối chiếu ở mục 1.

## Bối cảnh

- ADR-008 để tầng 4 (VLM) ở trạng thái park từ 2026-10-25 (R4). Bản 2026-10-29 của ADR này mở lại VLM cho bộ đọc trang theo hai loại trang (A: trang cần OCR; B: trang lớp chữ kèm bảng/hình), mỗi trang một ảnh PNG cả trang qua stdout của worker.
- Ngày 2026-11-01 người dùng đổi trọng tâm sang "bóc tách legit" (file quyết định mục 0, 1) và duyệt ADR-010: đơn vị của Vault, cây mục và tra cứu là KHỐI có nguồn (trang + `bbox` + tham chiếu từ), có sổ bảo toàn và ảnh cắt vùng qua `CropRequest`. Bản 2026-10-29 của ADR này có model `Block` riêng (không `bbox`, không tham chiếu từ) và đường ảnh riêng; ADR-010 "Phương án đã loại" đã loại model đó. Bản cập nhật này đưa VLM về khung của ADR-010.
- Cùng ngày người dùng duyệt quản lý API key trong app (V04) và theo dõi chi phí (mục 3), vì nhà cung cấp VLM chính là Gemini của người dùng, gateway ban tổ chức chỉ có hạn mức 5 USD (file quyết định mục 0).
- Ràng buộc giữ nguyên:
  - **G5, G6** (spec §1.5): không có VLM/LLM vẫn chạy đủ luồng; không cấu hình thì không dữ liệu nào rời máy.
  - **LLM Gate** (CLAUDE.md §2.1): đầu ra VLM là dữ liệu không tin cậy và luôn đi vào một cổng kiểm.
  - **I1–I5 không đổi định nghĩa.** I3: chữ VLM chỉ thành trích dẫn sau cổng; I5: dữ liệu đọc từ ảnh phải được con người xác nhận trước khi dùng như kết quả.
  - **File PDF không tin cậy** (CLAUDE.md §2.2, ADR-008 mục 3): render chạy trong worker có giới hạn OS.
  - **Không đặt `temperature`/`top_p`** (spec §11.5).
  - **Không rẽ nhánh theo môn** (CLAUDE.md §1).
  - **`cross_check.TOKEN_PATTERN` không đổi** (test I5 `test_numeric_token_mismatch_raises_dual_pass_mismatch` dựa vào nó).
  - Không thêm dependency hay file model.

## Quyết định

### 0. Giữ, đổi, bỏ so với bản 2026-10-29 (B1)

Code của bản 2026-10-29 chưa vào `develop` (chỉ có ở nhánh `feat/vlm-page-reader`, commit cấu hình VLM và `ChatMessage` ảnh được V02 cherry-pick). Các phần bỏ dưới đây chưa từng là contract đang chạy.

| Phần của bản 2026-10-29 | Quyết định | Nguồn |
|---|---|---|
| Định tuyến theo trang A/B (mục 1 cũ) | **Đổi**: định tuyến theo VÙNG (mục 1). Phần "VLM cho cấu trúc trang lớp chữ" (trang B) chuyển sang V03: LLM/VLM chỉ đề xuất cấu trúc bằng id khối (ADR-010 mục 14), không chạy ở bộ đọc ảnh | duyệt 2026-11-01 (ADR-009), file quyết định mục 1 |
| Model `Block`, `PageExtraction.blocks`, `structure_source` (mục 2 cũ) | **Bỏ**: dùng `Block` của ADR-010; nguồn chữ là `Block.source` (`text_layer` \| `ocr` \| `vlm`), thay vai trò của `structure_source` (đổi tên để không trùng `Block`). `PageExtraction` không có `blocks`; khối lưu ở bảng `blocks` (ADR-010 mục 13) | duyệt 2026-11-03 (người dùng uỷ quyền chọn phương án đề xuất), phương án 4 (Q3 đổi tên) |
| `VlmPageResult` | **Đổi**: `VlmRegionResult` chứa `VlmRegionBlock` (mục 2), nội bộ `infrastructure/vision_reader.py` | đề xuất suy ra, chờ xác nhận |
| `tier_used = "vlm"`, dòng `engine = "vlm"`, `conf = trace_ratio`, `LineFlag VLM_UNTRACED` | **Bỏ**: chữ VLM không bao giờ vào `lines`; `lines` của trang luôn từ lớp chữ hoặc OCR. Giá trị `"vlm"` của `TierUsed` và `LineEngine` giữ trong `Literal` (không xoá giá trị khỏi contract) nhưng code không sinh | đề xuất suy ra, chờ xác nhận |
| `ReviewReason` `VLM_READ`, `VLM_UNTRACED_TEXT`, `VLM_TOKEN_DROPPED`, `VLM_FALLBACK`, `VLM_STRUCTURE_UNALIGNED` | **Bỏ**; thay bằng `VLM_REGION`, `TABLE_TOKEN_MISMATCH` (mục 2) và cờ khối của ADR-010 (`vlm_invalid`, `vlm_gate_rejected`, `table_token_mismatch`) | `VLM_REGION`: đề xuất suy ra (B4'); `TABLE_TOKEN_MISMATCH`: đề xuất suy ra (B2) |
| `DocumentImport.send_page_images`, field form `send_page_images`; đồng ý theo từng tài liệu; loopback mặc định bật | **Bỏ**: MỘT công tắc toàn cục, mặc định TẮT kể cả loopback (mục 7) | duyệt 2026-11-03 (người dùng uỷ quyền chọn phương án đề xuất), phương án 2 |
| `kept_mismatches = [{line_index, primary_value}]`; 409 cho trang `tier_used = "vlm"` | **Đổi**: 409 giữ, áp cho trang còn `vlm_mismatches` (ô bảng lệch); `kept_mismatches` liệt kê `mismatch_id` (mục 3.4) | 409: duyệt 2026-10-29 (ADR-009) (Q2) và duyệt 2026-11-01 (ADR-009) (mục 1 "giữ quyết định 409"); liệt kê id từng chỗ: duyệt 2026-10-29 (ADR-009) (VLM 1.2 (d)) |
| PNG cả trang qua stdout; message ảnh được vượt trần dòng chung (mục 5.2 cũ) | **Bỏ**: ảnh gửi VLM là ảnh cắt vùng qua `CropRequest`/`crop_batch` của ADR-010 mục 9, dưới `max_message_bytes` 8 MiB (không nâng trần nào). Giữ "worker không ghi file" và danh sách kiểm của tiến trình cha | duyệt 2026-11-03 (người dùng uỷ quyền chọn phương án đề xuất), phương án 4 (thêm trần thông điệp 8 MiB) |
| `HOLOCRON_VLM_MAX_EDGE_PX = 2048` | **Đổi**: mặc định `1024`, khoảng 256–1024 (≤ `crop_max_edge_px` của ADR-010) | duyệt 2026-11-01 (ADR-009) (vùng cắt ~896–1024 px) |
| `HOLOCRON_VLM_MAX_IMAGE_BYTES = 4 MiB` | **Giữ** biến và mặc định; trần hiệu lực = min(biến, `crop_max_image_bytes` = 1 MiB của ADR-010) | giữ: duyệt 2026-11-03, phương án 4 (Q13); trần hiệu lực: đề xuất suy ra (OPEN 6) |
| "`blocks` không sửa được qua PATCH" (Q4) | **Đổi** theo ADR-010 mục 13 (r): `PATCH …/lines` dựng lại trang; khối `vlm` có tập tham chiếu không đổi giữ nguyên, khối đổi thành khối OCR (mục 3.5) | ADR-010 (Chấp nhận) |
| `HOLOCRON_VLM_MODE` mặc định `off`; mã `vlm_disabled`; khả năng `vlm` ⇔ MODE | **Đổi**: MODE không đặt ⇒ không cấm; `off`/`images_only` là trần (mục 7). `vlm_disabled` bỏ khỏi `CapabilityReason`; trạng thái đồng ý/nhà cung cấp/trần ở `RuntimeInfo.ai_status` (mục 6) | "MODE không đặt ⇒ không cấm": duyệt 2026-11-03 (người dùng uỷ quyền chọn phương án đề xuất), câu hỏi 2 của `.claude/state/briefs/x02b-open-questions.md`; `images_only` cấm cả chữ và bỏ `vlm_disabled`: đề xuất suy ra, chờ xác nhận (OPEN 1, 2) |
| `RuntimeInfo.vlm_local`, `vlm_data_recipient` | **Đổi**: thành `ai_status.roles[].local`, `ai_status.roles[].recipient_label`; biến `HOLOCRON_VLM_DATA_RECIPIENT` và quy tắc của nó giữ | biến và quy tắc: duyệt 2026-10-29 (ADR-009) (VLM 1.2 (c)); chỗ đặt field: đề xuất suy ra (OPEN 3) |
| Cache `vlm_cache` không gắn tài liệu (Q9), migration `0004_vlm_cache.sql` | **Đổi**: bảng chung `ai_cache` gắn tài liệu, migration cache AI số kế tiếp lúc merge (mục 8) | duyệt 2026-11-03 (người dùng uỷ quyền chọn phương án đề xuất), phương án 3 (Q9 đảo); số migration: ADR-010 mục 17 |
| Đối chiếu số theo tập đếm cả trang (Q14), `trace_ratio` | **Đổi**: cổng cấp VÙNG với chữ OCR độc lập của chính vùng đó (mục 3) | duyệt 2026-11-01 (ADR-009) (file quyết định mục 1) |
| Prompt v1, câu nhắc gọi lại, gửi lại đúng một lần (Q10) | **Giữ** | duyệt 2026-10-29 (ADR-009); giữ: duyệt 2026-11-03, phương án 4 |
| Khoá trống với loopback | **Giữ** | duyệt 2026-10-29 (ADR-009); giữ: duyệt 2026-11-03, phương án 4 |
| `HOLOCRON_VLM_TIMEOUT_S`, `HOLOCRON_VLM_CONCURRENCY` (Q13, VLM 1.2 (e)) | **Giữ** | duyệt 2026-10-29 (ADR-009) |
| `ChatMessage.content` có phần ảnh, adapter `OpenAiChatClient` (mục 4 cũ) | **Giữ** | duyệt 2026-10-29 (ADR-009); duyệt 2026-11-01 (ADR-009) |
| Tách `services/llm_json.py` (mục 9 cũ) | **Giữ** | duyệt 2026-10-29 (ADR-009) |
| Che đồ hoạ template lặp (mục 10 cũ) | **Giữ**, áp cả ảnh cắt gửi VLM | duyệt 2026-10-29 (ADR-009) |
| `scripts/eval_vlm.py` | **Đổi** tên thành `scripts/eval_vlm_regions.py` (đo theo vùng) | đề xuất suy ra, chờ xác nhận |

### 1. Định tuyến theo vùng

VLM (bộ đọc ảnh, `VisionReader`) chỉ được gọi cho một VÙNG khi đủ cả bốn điều kiện:

1. `AiConsent.auto_send_allowed("image")` đúng, đọc ở chính lời gọi đó (mục 7).
2. `ProviderSelector.select("vlm", …)` trả một nhà cung cấp (có cấu hình cho vai `vlm`, chưa chạm trần; mục 12).
3. Vùng thuộc một trong các loại sau (tín hiệu hình học và cờ, không xét môn, chương hay topic):
   - vùng của trang KHÔNG có lớp chữ dùng được (trang đi OCR cả trang theo ADR-008 mục 6) mà khối offline của vùng mang cờ không chắc (`layout_uncertain`, `table_unresolved`, `formula_text_unverified`, `low_text_contrast`) hoặc là `figure`/`table` có `bbox`;
   - khối `figure` hoặc `table` mang `table_unresolved` trên trang lớp chữ có ảnh nhúng chứa chữ (bảng ảnh, sơ đồ ảnh) mà `text_layer_overlap` < ngưỡng (mục 3.2).
4. Chưa vượt trần vùng của tài liệu và của trang (mục 6).

- Trang có lớp chữ: chữ LUÔN lấy từ lớp chữ. VLM không bao giờ thay chữ lớp chữ (duyệt 2026-11-01 (ADR-009)). Đề xuất cấu trúc cho trang lớp chữ là việc của V03 (ADR-010 mục 14).
- Một vùng = một khối offline đã dựng (ADR-010 mục 13; vùng = `bbox` của khối). Thiếu bất kỳ điều kiện nào thì không gọi VLM, không tra cache (mục 8) và khối giữ đúng kết quả offline với các cờ của nó.
- Danh sách cờ chọn vùng là đề xuất suy ra, chờ xác nhận; X11 hiệu chỉnh trên bộ dev (không theo bộ mù).
- VLM đọc ĐỘC LẬP: request chỉ có prompt và ảnh vùng, không kèm token hay chữ OCR (duyệt 2026-11-01 (ADR-009)).

### 2. Contract

Contract chi tiết ở spec §5.7 (phần "Khung VLM đọc vùng") và §5.16. Tóm tắt kèm nguồn dữ liệu.

**Đầu ra của `VisionReader` (nội bộ, KHÔNG có trong response API, không vào `types/holocron.ts`):** định nghĩa trong `infrastructure/vision_reader.py` (V02).

| Model / field | Nguồn |
|---|---|
| `VlmRegionBlock.type: BlockType` (ADR-010) | VLM (khoá `type` của prompt v1), ánh xạ cố định: `header` → `header_footer`, `title` → `heading`, `paragraph` → `paragraph`, `list` → `list_item`, `table` → `table`, `code` → `code`, `figure` → `figure`, `caption` → `caption`, `page_number` → `page_number`; giá trị khác ⇒ `vlm_invalid` (ánh xạ: đề xuất suy ra, chờ xác nhận) |
| `VlmRegionBlock.text: str \| None` | VLM (khoá `text`), NFC; `null` với `table` và `figure` (phần mô tả của `figure` do VLM tự viết, không phải chữ tài liệu, bị bỏ ngay khi parse) |
| `VlmRegionBlock.rows: list[list[str]] \| None` | VLM (khoá `rows`), NFC; khác null ⇔ `type = table` |
| `VlmRegionBlock.order: int ≥ 0` | Code: vị trí 0-based trong mảng VLM trả |
| `VlmRegionResult.input_sha256` | Code: sha256 của đúng các byte PNG đã kiểm và gửi đi |
| `VlmRegionResult.prompt_version` | Hằng `PROMPT_VERSION` (`"v1"`) |
| `VlmRegionResult.status` | Code: `ok`; `vlm_invalid` (JSON hỏng hoặc khối lạ sau lần gọi lại); `upstream_error`; `timeout` |
| `VlmRegionResult.blocks` | VLM đã parse và validate; rỗng khi `status ≠ ok` |
| `VlmRegionResult.attempts` (1–2), `from_cache` | Code |

`VlmRegionResult` không chứa tên model, endpoint hay khoá.

**Từ kết quả VLM sang khối ADR-010 (code, sau cổng mục 3; đề xuất suy ra, chờ xác nhận, X11 hiệu chỉnh):** mỗi vùng sinh tối đa MỘT `Block` thay cho khối offline của vùng:
- VLM trả đúng một khối `table` ⇒ ứng viên `table` (`cells` dựng từ `rows`, mỗi ô `rowspan = colspan = 1`).
- VLM trả một hay nhiều khối chữ (mọi loại trừ `table`, `figure`) ⇒ ứng viên chữ: `text` = các `text` nối bằng `\n` theo `order`; loại = loại của khối VLM khi chỉ có một khối, ngược lại `paragraph` kèm cờ `layout_uncertain`.
- VLM chỉ trả `figure` ⇒ khối offline giữ nguyên (VLM xác nhận là hình; nhãn vẫn từ `label_line_refs` OCR).
- Trộn `table` với khối khác ⇒ cổng từ chối (mục 3.3).
- Khối nhận: `source = "vlm"`, `line_refs` = các từ OCR của vùng (ledger basis `ocr`, ADR-010 mục 6, 7), `ocr_text` = chữ OCR đối chiếu. Id khối theo ADR-010 mục 3 (tham chiếu từ OCR của vùng), nên id không phụ thuộc phản hồi VLM.

**`PageExtraction` thêm** (spec §5.7):
- `vlm_mismatches: list[VlmTokenMismatch] = []`: chỗ lệch token số/mã của ô bảng `vlm` chưa giải quyết (mục 3.4).
- `ReviewReason` thêm `VLM_REGION` (trang có ít nhất một vùng đã nhận kết quả VLM: khối `source = "vlm"`, hoặc khối mang `vlm_gate_rejected`/`vlm_invalid`) và `TABLE_TOKEN_MISMATCH` (trang còn `vlm_mismatches`). Cả hai khai báo SAU `LAYOUT_FAILED`. Tên: đề xuất suy ra, chờ xác nhận.
- Dòng (`lines`) không đổi: chữ VLM không bao giờ vào `lines`; `lines` vẫn là đơn vị xem lại, sửa, xác nhận (ADR-010 mục 1).

### 3. Cổng nhận chữ VLM (`services/vision_gate.py`)

Bộ đọc VLM thuộc **LLM Gate** (CLAUDE.md §2.1). Cổng là hàm THUẦN thuộc Deterministic Core: nhận `VlmRegionResult`, chữ OCR độc lập của CÙNG vùng (tham số; cổng không chạy OCR), `text_layer_overlap` của vùng và settings; trả quyết định cho vùng. Ngưỡng là field có mặc định của `VisionGateSettings` (cùng module), không phải biến môi trường (ADR-008 mục 12). Mọi giá trị dưới đây là giá trị khởi đầu, đề xuất suy ra, chờ xác nhận; hiệu chỉnh trên bộ dev (`arm_ch2`), KHÔNG theo bộ mù (B3).

#### 3.1 Chuẩn hoá và token
- Chuẩn hoá: NFC, gộp mọi khoảng trắng thành một dấu cách, bỏ khoảng trắng đầu/cuối.
- Token số: `cross_check.TOKEN_PATTERN` (`0x[0-9A-F]+|#?-?\d+`), import, KHÔNG sửa.
- Token mã: mẫu RIÊNG `VisionGateSettings.code_token_pattern`, mặc định `(?<![0-9A-Za-z_])(?:[A-Z][A-Z0-9_]+|[A-Za-z_]+[0-9][0-9A-Za-z_]*)(?![0-9A-Za-z_])` (ví dụ `R13`, `CPSR`, `MOV`), so phân biệt hoa thường. Mẫu tổng quát theo hình thức chữ, không theo môn.
- Từ (cho Jaccard): cụm không trắng, `casefold`, bỏ dấu câu đầu/cuối.
- OCR độc lập của vùng: từ Tesseract của vùng; không có Tesseract mà có RapidOCR thì dùng RapidOCR và so sau khi bỏ dấu thanh ở cả hai phía (từ điển RapidOCR thiếu chữ có dấu thanh, ADR-008 "Kết quả đo").

#### 3.2 Từ chối ở cấp vùng (trước mọi so sánh)
- `text_layer_overlap` (code: diện tích phần giao của vùng với hợp các hộp từ lớp chữ, chia diện tích vùng) ≥ `max_text_layer_overlap` (0.20) ⇒ từ chối chữ VLM; vùng dùng lớp chữ (B3').
- Chữ OCR độc lập của vùng là `None` hoặc rỗng sau chuẩn hoá ⇒ từ chối (B2'').
- Chữ VLM chứa `[không đọc được]` ⇒ từ chối.
- `status ≠ ok` ⇒ khối offline giữ nguyên + cờ `vlm_invalid` (JSON hỏng, khối lạ) hoặc không cờ (lỗi mạng, timeout, thiếu cấu hình: lùi offline, chỉ ghi log mã lỗi).

#### 3.3 Ứng viên chữ (không phải bảng)
Nhận chữ VLM khi ĐỦ cả bốn điều kiện:
1. `cer(vlm, ocr) ≤ max_cer` (0.15). `cer` = khoảng cách Levenshtein ký tự giữa hai chuỗi đã chuẩn hoá chia cho số ký tự chuỗi OCR.
2. `word_jaccard(vlm, ocr) ≥ min_word_jaccard` (0.70), trên tập đếm từ.
3. Tập đếm token số của hai bên BẰNG NHAU tuyệt đối.
4. Tập đếm token mã của hai bên BẰNG NHAU tuyệt đối.

Không đạt ⇒ khối là `figure` + ảnh cắt + cờ `vlm_gate_rejected`, từ OCR của vùng vào `label_line_refs` (ADR-010 mục 7). Đầu vào của cổng có trần (`max_region_chars = 20 000` mỗi bên; vượt ⇒ từ chối) để Levenshtein có cận.

#### 3.4 Ứng viên bảng: ô lệch là BLOCKING (B2)
- `word_jaccard` của chữ mọi ô (nối theo hàng) với chữ OCR của vùng < `min_word_jaccard` ⇒ từ chối như 3.3. Bảng không dùng `cer` vì thứ tự đọc của OCR trên bảng khác thứ tự ô.
- Đạt Jaccard ⇒ khối GIỮ là `table` (giữ cấu trúc, ADR-010 mục 7 "Ngoại lệ cho bảng"). So tập đếm token số và token mã của các ô với chữ OCR của vùng:
  - mỗi token VLM thừa (không còn trong tập OCR) ⇒ một `VlmTokenMismatch` có `row`, `col` của ô chứa nó, `vlm_value` = token, `ocr_value = null`;
  - mỗi token OCR thiếu bên VLM (VLM bỏ sót) ⇒ một `VlmTokenMismatch` có `row = col = null`, `vlm_value = null`, `ocr_value` = token. Đây là ca "bỏ sót số trong bảng: BLOCKING" (duyệt 2026-10-29 (ADR-009), VLM 1.2 (b)).
- Có ít nhất một chỗ lệch ⇒ khối mang cờ `table_token_mismatch` (BLOCKING), trang có `TABLE_TOKEN_MISMATCH`, `POST …/pages/{n}/confirm` trả 409 cho tới khi mọi chỗ lệch được giải quyết (duyệt 2026-11-01 (ADR-009): "BLOCKING chỉ trong bảng, giữ quyết định 409"; duyệt 2026-10-29 (ADR-009), Q2). Khối mang `table_token_mismatch` bị loại khỏi tập đủ điều kiện (ADR-010 mục 13).
- **Giải quyết** (cách gỡ cờ mà ADR-010 mục 13 để cho X02b; tên field: đề xuất suy ra, chờ xác nhận), qua `PATCH …/lines` (spec §6.5), mỗi thao tác tăng `version` đúng 1 và ghi `PageEditAudit`:
  - `kept_mismatches: list[str]`: liệt kê `mismatch_id` từng chỗ ("Giữ giá trị này": người dùng chấp nhận bảng như VLM đọc). Không có "giữ tất cả" (duyệt 2026-10-29 (ADR-009), VLM 1.2 (d)).
  - `rejected_vlm_blocks: list[str]`: liệt kê `block_id` của khối `vlm` người dùng không nhận; khối thành `figure` + ảnh cắt + `vlm_gate_rejected`, các chỗ lệch của khối bị bỏ.
  - **Lưu bền** (đề xuất suy ra (orchestrator), chờ xác nhận): thao tác đã áp được ghi vào hai field của `PageExtraction` (spec §5.7): `kept_vlm_mismatch_ids: list[str]` (mỗi `mismatch_id` đã "Giữ giá trị này") và `vlm_rejected_block_ids: list[str]` (mỗi `block_id` người dùng đã bỏ). Hai field nằm trong JSON trang, ghi trong cùng giao dịch với `version` + 1 và `PageEditAudit`, nên còn sau khi khởi động lại và sau mọi lần dựng lại trang (mục 3.5).
  - Sửa dòng OCR của vùng: trang được dựng lại (mục 3.5); cổng chạy lại trên kết quả VLM đã lưu với chữ OCR mới.
  - Khối không còn chỗ lệch ⇒ gỡ `table_token_mismatch`; trang không còn `vlm_mismatches` ⇒ bỏ `TABLE_TOKEN_MISMATCH`.
- `mismatch_id = "vmm_" + sha256(JSON chuẩn hoá của [block_id, row, col, vlm_value, ocr_value, k])[:12]`, `k` là số thứ tự của chỗ lệch trùng các giá trị trước trong khối (JSON chuẩn hoá như ADR-010 mục 3) (đề xuất suy ra).
- **Giới hạn đã biết:** so theo tập đếm của vùng không bắt được hai ô cùng bảng tráo giá trị cho nhau. Ghi rõ trong báo cáo đo; đối chiếu theo vị trí ô làm sau khi có toạ độ ô từ OCR.

#### 3.5 Sau `PATCH …/lines` (OPEN của ADR-010 mục 7, chốt tại đây; đề xuất suy ra, chờ xác nhận)
- Dòng đã xác nhận của trang có vùng VLM lấy từ OCR (lớp chữ nếu có), không bao giờ từ VLM.
- Dựng lại trang (ADR-010 mục 13): khối `vlm` có tập tham chiếu từ OCR KHÔNG đổi giữ id; kết quả VLM của vùng được lấy lại từ cache AI theo liên kết của tài liệu (mục 8) và cổng chạy lại (hàm thuần, không gọi mạng trong giao dịch). Cache không còn dòng ⇒ khối thành khối offline.
- Vùng có dòng OCR bị sửa: khối dựng lại từ `confirmed_line` (basis `confirmed_lines`) với `source = "ocr"`, cờ `human_edited`; cổng chạy lại trên kết quả VLM đã lưu với chữ OCR đã sửa, đạt thì khối lại là `source = "vlm"` (id mới vì tham chiếu đổi).
- Dựng lại trang và đọc cache AI KHÔNG BAO GIỜ khôi phục khối người dùng đã bỏ (đề xuất suy ra (orchestrator), chờ xác nhận): vùng có khối mà `block_id` ∈ `vlm_rejected_block_ids` không nhận kết quả VLM từ cache, khối giữ `figure` + ảnh cắt + `vlm_gate_rejected`. Khi sửa dòng OCR làm khối của vùng đổi id, khối mới có tập tham chiếu OCR giao với tập tham chiếu của một khối đã bỏ thì thừa hưởng việc bỏ: id mới thay id cũ trong `vlm_rejected_block_ids` trong cùng giao dịch (phía an toàn: việc bỏ không tự mất; đề xuất suy ra, chờ xác nhận). Người dùng muốn dùng lại chữ VLM của vùng đó thì nhập lại tài liệu.
- `kept_vlm_mismatch_ids` lọc các chỗ lệch cổng sinh lại sau khi dựng lại (cùng `mismatch_id` ⇒ không vào `vlm_mismatches`). `mismatch_id` chứa `block_id`, nên khối đổi id ⇒ chỗ lệch có id mới, không còn được giữ và lại BLOCKING (phía an toàn). Id trong `kept_vlm_mismatch_ids` không còn khớp chỗ lệch nào sau dựng lại bị bỏ khỏi field.

#### 3.6 Không tự xác nhận (B4)
- Trang có `VLM_REGION` luôn `needs_review`: không tự xác nhận (`page_clean_v1`), không xác nhận theo lô; `services/auto_confirm.py` coi `VLM_REGION` là lý do chặn như mọi `ReviewReason`.
- **`source_trust_v1` (spec §5.7) xét theo khối, không theo `tier_used`** (đề xuất suy ra (orchestrator), chờ xác nhận; chặt hơn, không nới I5): `tier_used = "vlm"` không còn được sinh (mục 0), nên trang có vùng VLM vẫn mang `tier_used` `ocr` hoặc `text_layer`. Trang có ít nhất một khối `source = "vlm"` HOẶC có `VLM_REGION` trong `review_reasons` là KHÔNG đáng tin, bất kể `tier_used` và `cross_check`, kể cả sau khi người dùng xác nhận. Hệ quả: trang đó không qua `page_clean_v1` và trượt G1 của `preselect_page_flags_v1` (spec §5.8; cùng hàm). Test: `test_vlm_block_page_not_source_trusted` (mục 14.1).
- Điều này đi ngược mục tiêu "không duyệt chay, không phiền" (file quyết định mục 0). Duyệt 2026-11-03 (người dùng uỷ quyền chọn phương án đề xuất), phương án 6: giữ cho tới khi có số đo cổng VLM trên bộ mù, xem lại sau đo (OPEN 4).
- `VLM_REGION` không bị bỏ khi sửa dòng (như các lý do cấp trang khác).

#### 3.7 I1, I3
- **I1:** VLM không tính số nào. Mọi số là số VLM đọc từ ảnh, chịu cổng token tuyệt đối và sự xác nhận của người dùng.
- **I3 (không đổi định nghĩa):** chữ VLM chỉ thành văn bản khối (và từ đó thành `raw_text` của chunk, trích dẫn) SAU cổng. Khối bị từ chối không có `text`. Phần mô tả `figure` do VLM viết không bao giờ vào khối, chunk hay trích dẫn. Trích dẫn trong khối `vlm` là chuỗi con của `text` khối sau NFC (ADR-010 mục 7).

### 4. Adapter và prompt

- Dùng lại `OpenAiChatClient` (`infrastructure/llm_client.py`) sau `Protocol` `ChatCompletionClient` (`domain/llm.py`). Không thêm dependency, không adapter riêng cho từng nhà cung cấp (duyệt 2026-10-29 (ADR-009); duyệt 2026-11-01 (ADR-009)).
- `ChatMessage.content`: `str | list[ContentPart]`, `ContentPart` là union phân biệt theo `type` (`text`, `image_url` mang `data:image/png;base64,<…>`). Nội bộ, không vào `types/holocron.ts`. Payload chỉ có chữ giữ đúng byte như trước.
- Request VLM: một message `user` gồm phần `text` (prompt v1 nguyên văn) và một phần `image_url` (ảnh vùng). Không gửi chữ tài liệu nào kèm ảnh.
- Không gửi `temperature`, `top_p`. Tính tất định khi nhập lại đến từ cache (mục 8).
- `max_tokens` = `VlmTaskLimits.max_tokens` (4096).
- Gọi ở **tiến trình cha**. Worker đọc PDF không nhận khoá, endpoint hay quyền gọi mạng.
- `llm_client` nhận `UsageRecorder` tuỳ chọn và ghi `AiUsage` sau mỗi lần gọi (mục 12.5); kết nối theo luật mạng ở mục 12.8.

**Prompt v1** (người dùng gửi 2026-10-29, chép nguyên văn từ `.claude/state/vlm/prompt-v1.txt`; hằng `PROMPT_VERSION = "v1"`):

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

- Prompt v1 viết cho "một trang" và chưa có loại `formula`; dùng nguyên văn cho vùng cắt. Prompt riêng cho vùng (thêm `formula`) là `PROMPT_VERSION = "v2"` sau này, cần người dùng duyệt văn bản (OPEN 7).
- Đổi bất kỳ ký tự nào của hai văn bản trên phải tăng `PROMPT_VERSION` (giá trị này nằm trong khoá cache).
- V03 (đề xuất cấu trúc) có `PROMPT_VERSION` riêng, đặt ở `services/structure_proposal_prompt.py` (B6').

### 5. Ảnh gửi VLM: ảnh cắt vùng của ADR-010

- Ảnh gửi VLM là PNG do worker render qua `RegionRenderer.render_regions(path, requests)` với `CropRequest` dùng chung của ADR-010 mục 9 (V02 sở hữu cài đặt), `max_edge_px = HOLOCRON_VLM_MAX_EDGE_PX` (mặc định 1024). Che đồ hoạ template lặp áp trước khi mã hoá (mục 10).
- Worker trả `crop_batch` dưới `max_message_bytes` (8 MiB, không nâng); không có loại thông điệp ảnh riêng và không nới trần dòng nào (duyệt 2026-11-03 (người dùng uỷ quyền chọn phương án đề xuất), phương án 4). Worker không ghi file; `RLIMIT_FSIZE = 0` trên Unix giữ nguyên (duyệt 2026-10-29 (ADR-009), Q1).
- Tiến trình cha kiểm trên chính các byte đã giải mã, trước khi gửi: base64 chuẩn; `size_bytes` khớp và ≤ trần hiệu lực (min(`HOLOCRON_VLM_MAX_IMAGE_BYTES`, `crop_max_image_bytes`)); magic PNG `89 50 4E 47 0D 0A 1A 0A`; IHDR khớp `width_px`/`height_px`; cạnh dài ≤ `max_edge_px`; sha256 khớp. Sai ⇒ không gửi, khối giữ kết quả offline (+ `crop_failed` theo ADR-010), log chỉ mã lỗi.
- Ảnh chỉ nằm trong bộ nhớ của tiến trình cha cho tới khi gọi xong; ảnh gửi VLM không ghi ra đĩa. Ảnh cắt hiển thị (`CropImage`, ADR-010 mục 19) là việc riêng của job cắt ảnh, không do đường VLM ghi.
- Test bắt buộc (duyệt 2026-10-29 (ADR-009), Q1, áp cho đường mới): ảnh vượt trần bị loại trước khi gửi; ảnh sai magic bị loại; worker vẫn không ghi được file.

### 6. Giới hạn và cấu hình

Biến môi trường (bảng đầy đủ ở spec §11.2):

| Biến | Mặc định | Ý nghĩa | Nguồn |
|---|---|---|---|
| `HOLOCRON_VLM_MODE` | không đặt | Trần do người vận hành đặt cho mọi lần gửi TỰ ĐỘNG lúc nhập: không đặt ⇒ không cấm (còn tuỳ công tắc, mục 7); `off` ⇒ cấm mọi gửi tự động (ảnh và chữ); `images_only` ⇒ chỉ cho gửi ảnh, cấm gửi đoạn chữ tự động. Giá trị khác ⇒ `ConfigError` chỉ nêu tên biến | tên và giá trị `off`/`images_only`: duyệt 2026-10-29 (ADR-009); nghĩa "không đặt ⇒ không cấm": duyệt 2026-11-03 (người dùng uỷ quyền chọn phương án đề xuất), câu hỏi 2 của `.claude/state/briefs/x02b-open-questions.md` (thay mặc định `off` của 2026-10-29); phạm vi gồm chữ: đề xuất suy ra, chờ xác nhận (OPEN 1) |
| `HOLOCRON_VLM_TIMEOUT_S` | loopback `120`, cloud `60` | Cận tổng một lần gọi VLM ở mức việc | duyệt 2026-10-29 (ADR-009) |
| `HOLOCRON_VLM_CONCURRENCY` | loopback `1`, cloud `2` | Số vùng gọi VLM cùng lúc ở tiến trình cha; số nguyên 1–8, ngoài khoảng ⇒ `ConfigError`, coi như VLM chưa cấu hình (không tự kẹp) | duyệt 2026-10-29 (ADR-009) (Q13, VLM 1.2 (e)) |
| `HOLOCRON_VLM_MAX_EDGE_PX` | `1024` | Cạnh dài tối đa của ảnh vùng gửi VLM; số nguyên 256–1024 | duyệt 2026-11-01 (ADR-009); khoảng: đề xuất suy ra |
| `HOLOCRON_VLM_MAX_IMAGE_BYTES` | `4194304` (4 MiB) | Trần byte một ảnh gửi VLM; trần hiệu lực = min(biến, `crop_max_image_bytes` = 1 MiB) | giữ: duyệt 2026-11-03, phương án 4; trần hiệu lực: đề xuất suy ra (OPEN 6) |
| `HOLOCRON_VLM_DATA_RECIPIENT` | không đặt | Nhãn bên nhận của nhà cung cấp VLM từ biến môi trường: chữ thuần ≤ 80 ký tự, từ chối chuỗi dạng URL; cùng quy tắc loại như `HOLOCRON_LLM_DATA_RECIPIENT` | duyệt 2026-10-29 (ADR-009) (VLM 1.2 (c)) |
| `HOLOCRON_AI_CONSENT` | không đặt | `1` ⇒ công tắc đồng ý bật, `0` ⇒ tắt, khoá giá trị (UI không đổi được); khác ⇒ `ConfigError`. Dùng cho tới khi V04 có UI và cho người vận hành | cơ chế: duyệt 2026-11-03 (file chốt X02b); tên: đề xuất suy ra, chờ xác nhận |

Biến giá, trần và mẫu nhà cung cấp ở mục 12.3.

- "Loopback" nghĩa là tên máy của base URL thuộc `LOOPBACK_HOSTS` (`127.0.0.1`, `::1`, `localhost`).
- Field có mặc định (không phải biến môi trường, ADR-008 mục 12; đề xuất suy ra trừ chỗ ghi khác):
  - `VlmTaskLimits`: `json_retry_max = 1` (duyệt 2026-10-29 (ADR-009)), `max_response_chars = 100 000`, `max_json_depth = 32`, `max_parse_starts = 64`, `max_tokens = 4096`, `max_blocks_per_region = 200` (≤ 200 khối: duyệt 2026-10-29 (ADR-009), Q13), `max_table_rows = 200`, `max_table_cols = 30`.
  - Trần vùng: `vlm_max_regions_per_page = 16`, `vlm_max_regions_per_document = 200` (file quyết định mục 1 yêu cầu "có trần số trang mỗi tài liệu"; trần theo vùng chặt hơn). Vùng vượt trần giữ kết quả offline và cờ sẵn có của nó; số vùng bị bỏ ghi log.
  - Số hộp che tối đa mỗi trang `= 16` (duyệt 2026-10-29 (ADR-009), Q13).
  - `VisionGateSettings` ở mục 3.

### 7. Đồng ý gửi nội dung: một công tắc toàn cục (B5)

Duyệt 2026-11-03 (người dùng uỷ quyền chọn phương án đề xuất), phương án 2; câu cũ bị thay (duyệt 2026-10-29, Q2/Q7/Q8): "chọn 1 — theo từng tài liệu.- Endpoint cloud: mặc định TẮT, công tắc ghi rõ nơi nhận ảnh.- Endpoint loopback: mặc định BẬT, nhãn "Đọc ảnh bằng AI chạy trên máy này"."

- **MỘT công tắc toàn cục**, mặc định TẮT ở mọi endpoint, kể cả loopback. Nhãn trên UI: "Cho phép gửi nội dung tài liệu (ảnh và đoạn chữ) tới nhà cung cấp AI khi nhập", kèm một câu giải thích nơi nhận (`ai_status.roles[].recipient_label`).
- **Phạm vi đích:** công tắc chặn MỌI lần gửi TỰ ĐỘNG lúc nhập và sau khi xác nhận: V02 (ảnh vùng), V03 (đề xuất cấu trúc, chữ và ảnh thu nhỏ), job đề xuất cặp tự tạo khi xác nhận tài liệu (spec §5.8, `trigger = "document_confirmed"`), LLM chọn tên chương (spec §5.7 "Chương tự động"). Thao tác người dùng tự bấm (nút "Đề xuất" cặp khái niệm, `POST …/test` ở Cài đặt) KHÔNG bị chặn.
- **Áp theo giai đoạn** (đề xuất suy ra (orchestrator), chờ xác nhận): tới hết V04, công tắc (và `HOLOCRON_VLM_MODE`) CHỈ chặn lần gửi tự động của V02 và V03 — hai tính năng mới, không có test sẵn có. Job đề xuất cặp tự tạo và LLM chọn tên chương CHƯA bị công tắc chặn: giữ nguyên hành vi đã triển khai và test hiện có (`tests/test_document_confirm_auto_proposal.py` và test chương), `auto_send_blocked` chưa được sinh. Áp công tắc cho hai tính năng đó làm CÙNG V04 (khi người dùng có UI để bật công tắc), và chỉ sau khi người dùng đồng ý sửa fixture của các test hiện có (CLAUDE.md §7; OPEN 11). ADR này KHÔNG cho phép sửa test sẵn có.
- **`AiConsent` đọc ở MỖI lời gọi** (không chụp lúc khởi động): `auto_send_allowed(kind)` = công tắc bật VÀ `HOLOCRON_VLM_MODE` cho phép `kind` (`image` hoặc `text`).
- **Nguồn giá trị công tắc**, theo thứ tự: `HOLOCRON_AI_CONSENT` đặt ⇒ giá trị đó, UI chỉ đọc (`source = "env"`); ngược lại giá trị lưu trong file cài đặt (mục 12.4; `source = "setting"`); chưa lưu ⇒ tắt (`source = "default"`). Không có `HOLOCRON_DATA_DIR` ⇒ công tắc chỉ đặt được bằng biến môi trường (`PUT` trả 409 `settings_not_persistent`).
- Công tắc tắt ⇒ không gọi mạng, không tra cache AI; kết quả nhập giống hệt khi không có AI.
- Thứ tự triển khai (file chốt X02b 2026-11-03): V02 chạy trước V04. V02 cài `AiConsent` tối thiểu ở `infrastructure/ai_runtime.py` chỉ đọc `HOLOCRON_AI_CONSENT` và `HOLOCRON_VLM_MODE`; V04 thêm giá trị lưu trong file và UI, giữ Protocol.

### 8. Cache AI (B6)

Duyệt 2026-11-03 (người dùng uỷ quyền chọn phương án đề xuất), phương án 3; câu cũ bị thay (duyệt 2026-10-29, Q9, "chấp nhận … như đề xuất"): "Cache không gắn với tài liệu: xoá tài liệu không xoá kết quả VLM trong cache."

- Bảng chung cho V02 và V03, thêm bằng **migration cache AI** (số kế tiếp lúc merge, ADR-010 mục 17; V02 dự kiến `0007_ai_cache.sql`). Schema (đề xuất suy ra, chờ xác nhận):

  ```sql
  ai_cache(
    cache_key      TEXT PRIMARY KEY,   -- sha256 hex của JSON chuẩn hoá [kind, input_sha256, model, prompt_version]
    kind           TEXT NOT NULL CHECK (kind IN ('vlm_region', 'structure_proposal')),
    input_sha256   TEXT NOT NULL,      -- vlm_region: sha256 byte PNG đã gửi; structure_proposal: sha256 JSON chuẩn hoá của input (V03)
    model          TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    result_json    TEXT NOT NULL,      -- chỉ kết quả status = ok, đã validate
    created_at     TEXT NOT NULL,
    last_used_at   TEXT NOT NULL
  )
  ai_cache_documents(
    cache_key   TEXT NOT NULL REFERENCES ai_cache(cache_key) ON DELETE CASCADE,
    document_id TEXT NOT NULL,
    PRIMARY KEY (cache_key, document_id)
  )
  ```

- Khoá: `kind` + `sha256(input)` + `model` + `PROMPT_VERSION` (duyệt 2026-11-01 (ADR-009): "Cache theo sha256(ảnh)+model+PROMPT_VERSION"). Mỗi `kind` một `PROMPT_VERSION`.
- Một dòng thuộc được nhiều tài liệu (cùng ảnh vùng ở hai tài liệu) qua bảng liên kết `ai_cache_documents`.
- Chỉ JSON đã validate được lưu; khi đọc validate lại, hỏng ⇒ coi như trượt và xoá dòng. **Cổng (mục 3) luôn chạy lại khi trúng cache.**
- **Trần:** `ai_cache_max_rows = 20 000`, `ai_cache_ttl_days = 90` (field có mặc định của store, đề xuất suy ra). Khi ghi: xoá dòng có `last_used_at` quá TTL, rồi nếu vượt trần xoá dòng `last_used_at` cũ nhất, cùng giao dịch. Trúng cache cập nhật `last_used_at`.
- **Xoá tài liệu** (`delete_document`): trong CÙNG giao dịch, xoá liên kết của tài liệu rồi xoá các dòng `ai_cache` không còn liên kết nào (B6').
- **Xoá toàn bộ:** `DELETE /v1/settings/ai-cache` (spec §6.11), bảo vệ như mục 12.7.
- Cache là repository của store hiện có: `Protocol` ở `domain/repositories.py`, cài ở `infrastructure/storage/sqlite_store.py` (dùng chung kết nối và giao dịch) và `infrastructure/memory_store.py` (thuộc tính instance; không dict cấp module). Kho in-memory có cùng ngữ nghĩa.
- `model` chỉ nằm trong DB cục bộ, không vào log hay response. Cache không chứa byte ảnh.
- Công tắc tắt ⇒ không tra cache.

### 9. Parse JSON an toàn và lỗi

Giữ bản 2026-10-29 (duyệt 2026-10-29 (ADR-009)):
- Tách tiện ích parse JSON khỏi `services/concept_proposal.py` sang `services/llm_json.py` (refactor không đổi hành vi; test đề xuất cặp xanh không sửa).
- Giới hạn trước khi parse: `max_response_chars`, `max_json_depth` (đếm ngoài chuỗi), `max_parse_starts`. Chịu được rào ```` ``` ```` và văn bản thừa quanh mảng. Không dựa vào `response_format`.
- Validate bằng Pydantic: `type` thuộc danh sách prompt v1; `text` là chuỗi; `rows` là mảng các mảng chuỗi và chỉ có khi `type = table`; khoá thừa bỏ qua; giới hạn `VlmTaskLimits`; NFC.
- Lỗi parse hoặc validate ⇒ gửi lại **một** lần: cùng message của lần đầu, thêm đúng một phần `text` là câu nhắc cố định. Phản hồi hỏng của lần đầu không gửi lại.
- Hỏng lần nữa ⇒ `status = vlm_invalid`, khối offline giữ nguyên + cờ `vlm_invalid`. Lỗi HTTP sau khi hết lượt thử của adapter, 429, timeout, thiếu cấu hình ⇒ khối offline giữ nguyên, không cờ, chỉ log mã lỗi; không bao giờ ném ra luồng nhập, trang không thành `failed` vì lỗi VLM.

### 10. Che đồ hoạ template lặp

Giữ thiết kế bản 2026-10-29 (duyệt 2026-10-29 (ADR-009), "Việc A — trả lời 3 câu", ý 2; dùng `find_template_images` của ADR-008): định danh theo nội dung và vị trí; template khi có trên ≥ 5 trang VÀ ≥ 60% số trang; không bao giờ che vùng phủ ≥ 50% diện tích trang (trang scan không bị che); không che phần giao với hộp chữ của lớp chữ; tối đa 16 hộp che mỗi trang; tô màu nền lên ảnh đã render, không sửa PDF. Phạm vi: ảnh vùng gửi VLM và ảnh đưa vào OCR. Test phủ định cho scan.

### 11. Đo

- Script opt-in `holocron/backend/scripts/eval_vlm_regions.py`: chạy ngoài pytest, đọc khoá từ biến môi trường, không chạy trong CI hay pre-commit (CLAUDE.md §2.2); chỉ in số, không in nội dung tài liệu.
- Báo hai cột offline và có VLM; dev (`arm_ch2`) và mù (`arm_ch3`, `arm_ch4`, `de_cuong`) tách riêng; không chỉnh ngưỡng theo mù.
- Chỉ số: CER của khối `vlm` đã nhận; tỉ lệ vùng được nhận; số "sai không báo" (khối `vlm` đã nhận mà sai so với đáp án, mục tiêu 0); số/mã sai bị bắt (mục tiêu 100%, liệt kê từng ca bỏ lọt do giới hạn ở mục 3.4); ô bảng đúng; tái lập (nhập lại cùng file, cùng cấu hình, cache ⇒ khối giống hệt). Chi phí USD mỗi tài liệu.
- Ngưỡng cổng ở mục 3 chỉ được đổi theo số đo dev, ghi số trước/sau trong PR.

### 12. Kết nối AI (contract mới)

Phạm vi duyệt: file quyết định mục 3 (trích ở đầu ADR), "duyệt 2026-11-01 (ADR-009)". Chi tiết dưới đây là cách thực hiện điều kiện đã duyệt; tên field, mã lỗi, con số là đề xuất suy ra, chờ xác nhận trừ chỗ ghi khác. Contract đầy đủ ở spec §5.16 (model), §6.11 (endpoint), §11.2 (biến), §13.1 (bảo mật).

#### 12.1 Nhà cung cấp (`ProviderConfig`, B7)
- Field: `id`, `kind = "openai_compatible"`, `source` (`user` | `env`), `template` (`gemini` | `organizer_gateway` | `custom`), `label`, `base_url` (nhà cung cấp người dùng: giá trị đã chuẩn hoá; nhà cung cấp env: `null`, không lộ endpoint của người vận hành), `local`, `models {vlm, llm}` (nhập tay; gợi ý từ `GET {base_url}/models` qua `POST …/test` với `action = "list_models"`), `key_set`, `key_last4`, `key_storage` (`dpapi` | `plaintext_file` | `env` | `none`), `key_unreadable`, `enabled`, `priority`, `budget_usd`, `spent_usd`, `budget_exhausted`, `budget_shared_with`, `prices`. Nguồn từng field ở spec §5.16.
- **`key_last4` (B13):** 4 ký tự cuối chỉ khi khoá dài ≥ 12 ký tự; ngắn hơn ⇒ `null` (không hiện ký tự nào). Không field nào chứa khoá đầy đủ (duyệt 2026-11-01 (ADR-009): "chỉ 4 ký tự cuối").
- Mẫu trên UI: "Gemini (Google AI Studio)" (`gemini`), "Gateway ban tổ chức" (`organizer_gateway`), "Tuỳ chỉnh (tương thích OpenAI)" (`custom`). Mẫu chỉ quyết định nhãn mặc định, bảng giá mặc định và trần mặc định; không có code rẽ nhánh theo nhà cung cấp khác.
- Trần số nhà cung cấp người dùng: 20.

#### 12.2 Nhà cung cấp từ biến môi trường
- `HOLOCRON_LLM_*` ⇒ nhà cung cấp `env_llm` (vai `llm`, model `HOLOCRON_LLM_MODEL_EXTRACT`); `HOLOCRON_VLM_*` ⇒ `env_vlm` (vai `vlm`, model `HOLOCRON_VLM_MODEL`). Hai nhóm có cùng base URL đã chuẩn hoá VÀ cùng khoá ⇒ MỘT nhà cung cấp `env_shared` có cả hai model, MỘT bộ đếm, MỘT trần (B7''; duyệt 2026-11-03, phương án 5).
- Chỉ đọc: `PATCH`/`DELETE` ⇒ 409 `provider_read_only` (B13). Đứng trước mọi nhà cung cấp người dùng khi chọn (duyệt 2026-11-01 (ADR-009): "biến môi trường … được ưu tiên khi có"); `priority = 0`.
- `template`: `HOLOCRON_LLM_TEMPLATE` (mặc định `organizer_gateway`, B7'), `HOLOCRON_VLM_TEMPLATE` (mặc định `custom`: không suy ra nhà cung cấp từ địa chỉ; demo với Gemini đặt `HOLOCRON_VLM_TEMPLATE=gemini`). `env_shared` có hai mẫu khác nhau ⇒ `custom`.
- `label`: `HOLOCRON_LLM_DATA_RECIPIENT` / `HOLOCRON_VLM_DATA_RECIPIENT` nếu hợp lệ; không thì câu cố định "endpoint LLM đã cấu hình (chưa đặt tên bên nhận)" / "nhà cung cấp AI đã cấu hình" (câu VLM: duyệt 2026-10-29 (ADR-009), VLM 1.2 (c)).
- Nhà cung cấp người dùng trùng base URL chuẩn hoá và khoá với một nhà cung cấp khác (kể cả env) ⇒ dùng chung bộ đếm và trần với nó (`budget_shared_with`; duyệt 2026-11-03, phương án 5). Trần của nhóm = trần nhỏ nhất trong nhóm.

#### 12.3 Chi phí và trần (B7, B7', B7'')
- Bộ đếm theo nhà cung cấp: `input_tokens`, `output_tokens`, `images`, `spent_usd`. Nguồn: `usage.prompt_tokens`/`usage.completion_tokens` của phản hồi; thiếu `usage` ⇒ ước tính bảo thủ (vào = số ký tự phần chữ / 2, ra = `max_tokens` của lời gọi; `estimated = true`). `spent_usd = Σ(vào/1000 × giá vào + ra/1000 × giá ra + ảnh × giá ảnh)`.
- Chi phí USD là số vận hành, KHÔNG thuộc I1 (I1 chỉ áp cho nội dung câu hỏi, đáp án, lời giải).
- **Giữ chỗ nguyên tử trước khi gọi** (đề xuất suy ra (orchestrator), chờ xác nhận): `ProviderSelector.select` xét và giữ chỗ trong MỘT đoạn dưới khoá của bộ đếm (thuộc tính instance): nhà cung cấp chỉ đủ điều kiện khi `spent_usd` của nhóm + tổng chỗ đang giữ của nhóm + chi phí tối đa của lời gọi sắp gửi (`CostEstimate`: vào ước tính, ra = `max_tokens`, số ảnh) ≤ trần; đủ thì cộng chi phí tối đa đó vào chỗ đang giữ của nhóm và trả `SelectedProvider` kèm `reservation_id`. Sau lời gọi: `UsageRecorder.record(reservation_id, usage)` cộng chi phí thật (thiếu `usage` ⇒ ước tính bảo thủ) vào `spent_usd` và nhả chỗ trong cùng một đoạn khoá; lỗi TRƯỚC khi gửi request ⇒ `release(reservation_id)` chỉ nhả chỗ; lỗi SAU khi đã gửi (timeout, mất kết nối) ⇒ `record` với ước tính bảo thủ (`estimated = true`). Nhờ vậy các lời gọi song song (`HOLOCRON_VLM_CONCURRENCY` > 1, nhiều job) không cùng lọt qua một khoảng trống trần; chỉ vượt trần khi usage thật lớn hơn `CostEstimate`. Chỗ giữ chỉ ở RAM (mất khi khởi động lại cùng lời gọi đang bay).
- **Giá:** biến `HOLOCRON_LLM_PRICE_IN_PER_1K`, `HOLOCRON_LLM_PRICE_OUT_PER_1K`, `HOLOCRON_VLM_PRICE_IN_PER_1K`, `HOLOCRON_VLM_PRICE_OUT_PER_1K`, `HOLOCRON_VLM_PRICE_PER_IMAGE` (USD, số ≥ 0 hữu hạn; sai ⇒ `ConfigError`). Thiếu ⇒ bảng giá MẶC ĐỊNH BẢO THỦ theo `template` (một quy tắc duy nhất; không "coi như chạm trần"; duyệt 2026-11-03, phương án 5: "thiếu bảng giá ⇒ dùng giá cao (an toàn)"):

  | `template` | USD / 1k token vào | USD / 1k token ra | USD / ảnh |
  |---|---|---|---|
  | `gemini` | 0.0025 | 0.015 | 0.003 |
  | `organizer_gateway` | 0.005 | 0.025 | 0.005 |
  | `custom` | 0.005 (cao nhất của bảng) | 0.025 (cao nhất) | 0.005 (cao nhất) |

  Nguồn: giá trị bảo thủ do người viết đặt ngày 2026-11-03, KHÔNG trích từ bảng giá công bố của nhà cung cấp nào (không tra được trong phiên viết). Người dùng hoặc V01 thay bằng giá thật qua biến môi trường hay UI (OPEN 5).
- **Trần mặc định theo mẫu:** `organizer_gateway` 4 USD (duyệt 2026-11-01 (ADR-009): "mặc định gateway ban tổ chức 4 USD"), `gemini` 5 USD, `custom` 1 USD (hai giá trị sau: đề xuất suy ra). Biến `HOLOCRON_LLM_BUDGET_USD`, `HOLOCRON_VLM_BUDGET_USD` ghi đè cho nhà cung cấp env.
- **Chạm trần** ⇒ chuyển nhà cung cấp kế tiếp của cùng vai; không còn ⇒ lùi offline và báo trên UI (`ai_status.roles[].reason = "budget_exhausted"`). Thao tác người dùng bấm khi mọi nhà cung cấp của vai đã chạm trần ⇒ 409 `{"code": "AI_BUDGET_EXHAUSTED", "message"}`.
- **Lưu bộ đếm:** file `ai_usage.json` trong `HOLOCRON_DATA_DIR` (ghi nguyên tử, có khoá). Không có `HOLOCRON_DATA_DIR` ⇒ bộ đếm trong RAM, reset khi khởi động lại, UI cảnh báo (duyệt 2026-11-03, phương án 5). Bộ đếm là thuộc tính của instance (không dict cấp module, CLAUDE.md §2.2).

#### 12.4 Lưu cấu hình (B8)
- File `ai_settings.json` trong `HOLOCRON_DATA_DIR` (cùng gốc với SQLite, ngoài repo): danh sách nhà cung cấp người dùng và giá trị công tắc. Ghi nguyên tử: tệp tạm tạo bằng `os.open(…, 0o600)` rồi `os.replace`; khoá tiến trình khi ghi; thư mục 0700 trên Unix.
- **Khoá:** Windows mã hoá bằng DPAPI (`CryptProtectData`/`CryptUnprotectData`, phạm vi người dùng hiện tại) qua `ctypes`, KHÔNG thêm dependency (`key_storage = "dpapi"`); DPAPI lỗi ⇒ KHÔNG lưu, trả 500 `key_storage_failed` (không lưu dạng rõ âm thầm). Unix: lưu dạng rõ trong file quyền 0600 (`key_storage = "plaintext_file"`), UI hiện banner cảnh báo (duyệt 2026-11-01 (ADR-009): "không thì file + cảnh báo rõ trong UI và OPEN"; OPEN 8).
- File hỏng hoặc DPAPI không giải mã được ⇒ nhà cung cấp mang `key_unreadable = true`, không được chọn, người dùng nhập lại khoá.
- **Không có `HOLOCRON_DATA_DIR`** ⇒ chỉ nhà cung cấp env; endpoint ghi trả 409 `settings_not_persistent`; UI cảnh báo (B8).
- `load_runtime_config(env)` chỉ đọc biến môi trường; kho cấu hình là `Protocol` riêng (`infrastructure/provider_store.py`), cấp qua `Depends`, test dùng thư mục tạm, không đọc thư mục dữ liệu thật (B2'').

#### 12.5 Chọn nhà cung cấp (B13, B12')
- MỘT nơi chọn: `services/provider_selection.py` cài `ProviderSelector` (logic chọn, kiểm trần, giữ chỗ), ở mọi giai đoạn kể cả V02. `infrastructure/llm_config.py` chỉ hợp nhất nguồn (env + file), `infrastructure/ai_runtime.py` chỉ đọc nguồn từ biến môi trường; cả hai không chọn.
- Thứ tự: nhà cung cấp env trước, rồi nhà cung cấp người dùng theo `priority` tăng dần, hoà thì theo `id`.
- Nhà cung cấp đủ điều kiện cho vai: `enabled`, có model cho vai, base URL hợp lệ, khoá đọc được (hoặc loopback không khoá), nhóm chưa chạm trần theo `CostEstimate`.
- `infrastructure/vision_reader.py` và mọi nơi gọi AI chỉ nhận `AiConsent`, `ProviderSelector`, `UsageRecorder` qua tham số; router cấp bằng `Depends` (nối ở `routers/vault.py:get_chat_client`, `get_chat_client_factory` và nơi dựng `VisionReader`).

#### 12.6 Protocol ở `domain/ai.py` (B12, B12')
`AiConsent.auto_send_allowed(content: "image" | "text") -> bool`; `ProviderSelector.select(role: "vlm" | "llm", estimate: CostEstimate) -> SelectedProvider | None` (giữ chỗ, mục 12.3); `UsageRecorder.record(reservation_id: str, usage: AiUsage) -> None`, `UsageRecorder.release(reservation_id: str) -> None`. `SelectedProvider` mang khoá trong `SecretStr`, không ra response hay log. Chữ ký đầy đủ ở spec §5.16. Phần V02 (đề xuất suy ra (orchestrator)): `infrastructure/ai_runtime.py` chỉ cung cấp NGUỒN đọc từ biến môi trường (danh sách nhà cung cấp env, `AiConsent` từ `HOLOCRON_AI_CONSENT` và `HOLOCRON_VLM_MODE`, bộ đếm trong RAM); `ProviderSelector` là cài đặt duy nhất ở `services/provider_selection.py` (mục 12.5) nhận nguồn đó qua tham số. V04 thêm nguồn từ file cài đặt, giữ Protocol và giữ nơi chọn.

#### 12.7 Bảo vệ endpoint ghi (B9, B9', B9'')
Duyệt 2026-11-01 (ADR-009): "endpoint đọc/ghi key chỉ nhận từ 127.0.0.1 + token truy cập hiện có (routers/access.py)". Kiểm IP nguồn vô hiệu sau proxy Next.js (request đến từ tiến trình Next trên loopback), nên lớp bảo vệ là token. Chi tiết duyệt 2026-11-03 (người dùng uỷ quyền chọn phương án đề xuất), phương án 1:
- Endpoint ghi (`POST`/`PATCH`/`DELETE /v1/settings/providers…`, `POST …/{id}/test`, `PUT /v1/settings/ai-consent`, `DELETE /v1/settings/ai-cache`): CHƯA đặt `HOLOCRON_REQUIRE_TOKEN` ⇒ 403 `token_not_configured` kèm hướng dẫn đặt token (fail-closed). Đã đặt ⇒ middleware hiện có bắt buộc `X-Holocron-Token`; thiếu/sai ⇒ 403 `token_required`.
- Endpoint đọc (`GET`, không có khoá) theo middleware hiện có.
- Mọi 403 của middleware và của router cài đặt có thêm field `code` cạnh `detail` (chuỗi `detail` giữ nguyên): `host_forbidden`, `token_required`, `token_config_invalid`, `token_not_configured` (sửa `routers/access.py` và test của nó, commit test riêng; tên `token_required`/`token_not_configured`: duyệt 2026-11-03; hai tên còn lại: đề xuất suy ra).
- Frontend khi không có vỏ desktop: ô "Mã truy cập" ở trang Cài đặt; AppShell hiện cùng ô khi nhận 403 `token_required` ở bất kỳ trang nào (đề xuất suy ra). Mã chỉ giữ trong biến module của `lib/api.ts` (RAM), KHÔNG localStorage/sessionStorage, gửi qua `X-Holocron-Token`. Người dùng đặt `HOLOCRON_REQUIRE_TOKEN` (cách hiện có) rồi nhập mã. Chưa đặt token ⇒ trang Cài đặt hiện "đang cấu hình bằng biến môi trường" kèm hướng dẫn, không có nút lưu. Hệ quả: bản web không token không cấu hình được khoá qua UI (chấp nhận theo phương án 1).

#### 12.8 Mạng và SSRF (B10, B10')
- **Base URL người dùng nhập:** `https://`; `http://` chỉ khi tên máy thuộc `LOOPBACK_HOSTS` (như `check_base_url`). Từ chối userinfo (`user:pass@`), query, fragment, ký tự điều khiển; dài ≤ 2048. Lúc lưu chỉ kiểm tĩnh (gồm IP viết thẳng), không phân giải DNS.
- **Mỗi request** (kể cả `/test`): phân giải tên máy, mọi địa chỉ phải là công khai; chặn riêng (10/8, 172.16/12, 192.168/16, 100.64/10, fc00::/7), link-local (169.254/16, fe80::/10, gồm 169.254.169.254), không xác định, multicast, dành riêng, và dạng IPv4-mapped (`::ffff:a.b.c.d`) của các dải đó. Loopback chỉ được khi tên máy trong URL thuộc `LOOPBACK_HOSTS` (tên miền công khai phân giải ra loopback bị chặn). Kết nối tới đúng địa chỉ đã kiểm (ghim IP, giữ SNI và `Host` là tên máy) để chống DNS rebinding giữa lúc kiểm và lúc nối.
- Không theo redirect (giữ `_NoRedirect`), không dùng proxy hệ thống (giữ hiện có).
- Đổi `base_url` ⇒ bắt nhập lại khoá (`PATCH` đổi `base_url` mà thiếu `api_key` ⇒ 422 `key_required_after_base_url_change`), trừ base URL loopback không khoá.
- **Nhà cung cấp env MIỄN chặn IP riêng** (cấu hình của người vận hành, có thể là gateway nội bộ) nhưng vẫn không redirect, không userinfo/query/fragment (B10'). Hiện `check_base_url` chưa từ chối query/fragment cho biến môi trường; thêm kiểm này là đổi hành vi nhỏ của cấu hình env (đề xuất suy ra).

#### 12.9 Không lộ khoá (B11)
- Khoá là `SecretStr` từ lúc parse request tới lúc dựng header; không trong `repr`, log, trace, thông điệp exception, report hay response.
- Handler lỗi validate CHỈ của router cài đặt che `input` (và `ctx`) trong 422, để 422 không vọng khoá.
- `RuntimeInfo` không lộ endpoint hay khoá; `ai_status` chỉ có id nhà cung cấp, nhãn đã lọc, boolean và mã lý do.
- Nhãn người dùng đặt (`label`) ≤ 80 ký tự, không ký tự điều khiển, không chứa `://`, không chứa khoá, tên model hay tên máy của chính nhà cung cấp đó (sai ⇒ 422 `label_rejected`).
- `POST …/test` trả mã đã làm sạch (`ok`, `auth_failed`, `not_found`, `rate_limited`, `upstream_error`, `timeout`, `blocked_address`, `redirect_blocked`, `invalid_response`, `image_rejected`, `key_unreadable`, `no_model_for_role`, `budget_exhausted`), không thân phản hồi, không URL đầy đủ, không khoá. Lời gọi `/test` tính vào bộ đếm.

#### 12.10 Endpoint (B12; spec §6.11)
`GET /v1/settings/providers`; `POST /v1/settings/providers`; `PATCH /v1/settings/providers/{id}`; `DELETE /v1/settings/providers/{id}`; `POST /v1/settings/providers/{id}/test`; `GET`/`PUT /v1/settings/ai-consent`; `DELETE /v1/settings/ai-cache`. Router `routers/settings.py`.

#### 12.11 `RuntimeInfo.ai_status` (B2', B2'')
- Trạng thái đồng ý, nhà cung cấp, trần là field RIÊNG `ai_status` (spec §5.12, §5.16), tính lại ở MỖI `GET /v1/runtime` (công tắc và chi phí đổi trong lúc chạy), không gọi mạng. Mã lý do của từng vai (`AiAutoBlockReason`, xét theo thứ tự): `no_provider`, `budget_exhausted`, `mode_off`, `mode_images_only` (chỉ vai `llm`), `consent_off`.
- Khả năng `vlm` giữ `available = false`, `reason = "not_implemented"` tới task bật VLM vào luồng nhập (X11). `tests/test_capabilities.py:85-101` và `tests/test_llm_config.py:61` không đổi tới khi đó. Ở task đó (duyệt 2026-11-03 (người dùng uỷ quyền chọn phương án đề xuất), phương án 7) sửa hai test bằng commit `test(...)` riêng kèm câu thay thế ở spec §5.12: "khả năng `vlm` khả dụng ⇔ code bộ đọc vùng VLM đã có VÀ `vlm_configured`; ngược lại `not_implemented` hoặc `vlm_not_configured`. Công tắc, `HOLOCRON_VLM_MODE` và trần chi tiêu không đổi `available` của khả năng mà chỉ đổi `ai_status`."
- Nghĩa sau hợp nhất: `llm_configured` (`vlm_configured`) = có ít nhất một nhà cung cấp (env hoặc người dùng) `enabled`, đủ cấu hình cho vai `llm` (`vlm`): base URL, model của vai, khoá đọc được hoặc loopback không khoá; không xét công tắc hay trần. Khi không có nhà cung cấp người dùng, nghĩa trùng nghĩa hiện tại (riêng `vlm_configured` thêm trường hợp loopback không khoá, duyệt 2026-10-29 (ADR-009)). Khả năng cần LLM tính lại theo nguồn hợp nhất ở mỗi `GET /v1/runtime`; phần kiểm cục bộ (Tesseract, unicharset, FTS5) vẫn chỉ chạy một lần (spec §11.4).
- Mã `vlm_disabled` (duyệt 2026-10-29 cho `CapabilityReason`) KHÔNG thêm vào code; thay bằng `ai_status.roles[].reason = "mode_off"` (đề xuất suy ra, chờ xác nhận; OPEN 2).

### 13. Ngoài phạm vi

- Đóng gói VLM (model, runtime) trong app.
- Nhập đề thi bằng VLM: dual-pass của upload đề (spec §6.1, §7.1) vẫn dùng `MockDualPassExtractor`.
- Chữ viết tay.
- VLM thay chữ của trang có lớp chữ (cấm, mục 1).
- Gửi chữ OCR kèm ảnh cho VLM.
- Đối chiếu số theo vị trí ô (mục 3.4).
- Khoá OS (Windows Credential Manager, Secret Service) cho khoá người dùng nhập; vỏ Tauri chèn token.

### 14. Test

Test nằm trong `holocron/backend/tests/`, không gọi mạng (client và DNS là stub), dùng file mẫu tự tạo:

- **Định tuyến và công tắc:** công tắc tắt, `HOLOCRON_VLM_MODE = off`, hoặc không có nhà cung cấp ⇒ không gọi transport, không tra cache, kết quả giống hệt khi không có AI; `images_only` ⇒ V02 gọi được, V03 không gọi (job đề xuất tự tạo và tên chương: chỉ từ V04, mục 7); `AiConsent` đọc lại ở mỗi lời gọi (đổi giữa hai vùng có hiệu lực); thao tác người dùng bấm không bị công tắc chặn.
- **Cổng:** token số lệch 1 ký tự ⇒ từ chối; token mã lệch ⇒ từ chối; CER hoặc Jaccard dưới ngưỡng ⇒ từ chối; thiếu OCR độc lập (`None`, rỗng) ⇒ từ chối; `text_layer_overlap` ≥ ngưỡng ⇒ từ chối; `[không đọc được]` ⇒ từ chối; khớp ⇒ nhận `source = "vlm"`; ô bảng lệch ⇒ `table_token_mismatch` + `TABLE_TOKEN_MISMATCH` + `vlm_mismatches`; bảng bỏ sót số ⇒ BLOCKING.
- **409 và giải quyết:** xác nhận trang còn `vlm_mismatches` ⇒ 409; `kept_mismatches` từng id ⇒ xác nhận được, audit, `version` tăng 1; `rejected_vlm_blocks` ⇒ khối thành `figure` + `vlm_gate_rejected`; sửa dòng OCR ⇒ cổng chạy lại; dựng lại trang và đọc lại cache không khôi phục khối đã bỏ, chỗ lệch đã giữ không quay lại (mục 3.5).
- **Chi phí và trần:** hai lời gọi song song không cùng lọt qua phần trần còn lại (giữ chỗ nguyên tử); lỗi trước khi gửi nhả chỗ, lỗi sau khi gửi ghi ước tính; mọi nhà cung cấp của vai chạm trần ⇒ lùi offline cho lần gửi tự động và 409 `AI_BUDGET_EXHAUSTED` cho thao tác người dùng bấm; thứ tự chọn env trước, rồi `priority`, rồi `id`; nhóm dùng chung trần (mục 12.2, 12.3).
- **Ảnh:** ảnh vượt trần, sai magic, sai IHDR, sai sha ⇒ không gửi; worker không ghi được file.
- **Parse:** phản hồi hỏng ⇒ gọi lại đúng một lần kèm câu nhắc; hỏng lần nữa ⇒ `vlm_invalid`; JSON bọc code fence được nhận.
- **Cache:** lần nhập thứ hai không gọi transport và cho khối giống hệt; đổi model hoặc `PROMPT_VERSION` ⇒ trượt; xoá tài liệu dọn liên kết và dòng mồ côi ở CẢ SQLite và in-memory; trần dòng và TTL; xoá toàn bộ.
- **Kết nối AI:** xem bảng 14.1 và V04.

#### 14.1 Test cưỡng chế đề xuất (OPEN 9: bổ sung bảng CLAUDE.md §9)

Bảng dưới là đề xuất cho bảng CLAUDE.md §9; ADR này KHÔNG sửa CLAUDE.md. Test cưỡng chế hiện có của §9 giữ nguyên, không sửa assert (riêng hai test khả năng `vlm` ở mục 12.11 sửa ở task bật VLM theo phương án 7, chúng không thuộc bảng §9).

| Bất biến | Test (file gợi ý; task làm) |
|---|---|
| I3 | `test_vlm_text_only_becomes_quote_after_gate` (khối bị từ chối không có `text`, không vào chunk; khối nhận có trích dẫn là chuỗi con của `text` khối; `tests/test_vision_gate.py`, V02 + X11); `test_vlm_text_rejected_on_text_layer_region` (`text_layer_overlap` ≥ ngưỡng ⇒ chữ lớp chữ giữ nguyên; V02); `test_vlm_figure_description_never_in_block_text` (V02) |
| I5 | `test_vlm_region_page_never_auto_confirmed` (cùng file với `test_auto_confirm_never_confirms_flagged_page`; X11); `test_vlm_table_cell_mismatch_blocks_confirm` (409 giữ tới khi giải quyết; đã có trong ADR-010 mục 21, X11); `test_vlm_table_dropped_token_is_blocking` (X11); `test_vision_gate_rejects_number_or_code_token_mismatch`, `test_vision_gate_rejects_without_independent_ocr` (`tests/test_vision_gate.py`, V02); `test_consent_off_blocks_all_auto_sends` (V02 và V03 từ V02; job tự tạo và tên chương thêm ở V04 sau khi người dùng đồng ý sửa fixture, OPEN 11; `tests/test_ai_runtime.py`); `test_vlm_block_page_not_source_trusted` (trang có khối `source = "vlm"` hoặc `VLM_REGION`, mang `tier_used` `ocr` + `cross_check.status = "ok"` hoặc `text_layer`, không qua `source_trust_v1`, không tự xác nhận, trượt G1 của `preselect_page_flags_v1`; X11); `test_rejected_vlm_block_stays_rejected_after_rebuild` (bỏ khối, sửa dòng OCR của vùng và của vùng khác, khởi động lại store, đọc lại cache ⇒ khối vẫn `figure` + `vlm_gate_rejected`, không có `text`; chỗ lệch đã giữ không quay lại; X11) |
| Chi phí (mới, không phải I1–I5) | `test_budget_reservation_blocks_concurrent_overspend` (N lời gọi song song, trần chỉ đủ một ⇒ đúng một được chọn; V02); `test_budget_release_on_presend_error_and_estimate_on_postsend_error` (V02); `test_user_action_budget_exhausted_returns_409` (409 `AI_BUDGET_EXHAUSTED`; V04); `test_selector_order_env_priority_id` (env trước, `priority` tăng dần, hoà theo `id`, bỏ qua nhà cung cấp chạm trần sang kế tiếp; V02 + V04); `test_shared_budget_group_uses_min_cap` (V04) |
| Bảo mật khoá và mạng (mới, không phải I1–I5) | `test_api_key_never_in_response_log_repr_or_422` (property ≥ 100 khoá ngẫu nhiên có seed: mọi response của endpoint cài đặt, `/v1/runtime`, caplog, `repr`, thông điệp exception, 422; `tests/test_provider_security.py`, V04); `test_key_last4_hidden_for_short_keys` (V04); `test_settings_write_forbidden_without_token` (`token_not_configured`, `token_required`; V02 cho `DELETE /v1/settings/ai-cache`, V04); `test_provider_ssrf_blocked` (IP riêng, link-local, IPv6 riêng, IPv4-mapped, DNS rebinding, redirect, userinfo/query/fragment; V04); `test_env_provider_read_only` (V04); `test_dpapi_round_trip_and_no_plaintext_on_disk` (Windows) và `test_settings_file_mode_0600` (Unix) (V04) |

### 15. Đọc lại theo yêu cầu người dùng (thêm 2026-11-04; đề xuất suy ra (orchestrator), chờ xác nhận)

Người dùng bấm "Đọc lại bằng AI" trên khối có cờ (`table_unresolved`, `code_layout_uncertain`, `low_text_contrast`) là thao tác tự bấm của mục 7: không bị công tắc đồng ý chặn, nhưng vẫn cần nhà cung cấp `vlm` đã cấu hình, chưa chạm trần (`HOLOCRON_VLM_MODE = off` vẫn chặn), và UI báo ảnh vùng sẽ gửi tới `recipient_label`. Cùng `VisionReader` và `vision_gate` (mục 3), không ngưỡng mới. Đề xuất không ghi kho; chỉ khi người dùng bấm Nhận, khối thành `source = "vlm"` qua đường dựng lại (mục 3.5) và trang không tự xác nhận (mục 3.6). Contract: spec §6.10a. I3, I5, I1 như mục 3.7. Không đổi trạng thái ADR, không đổi mục nào ở trên. Điểm cần người dùng duyệt: Reject không ghi `vlm_rejected_block_ids`; dựng lại sau Nhận đọc `ai_cache` bất kể công tắc (không gửi gì).

## OPEN

Không mục nào dưới đây chặn V02. Mỗi mục chờ người dùng xác nhận.

1. **`images_only` cấm cả gửi đoạn chữ tự động** (mục 6, 7). Đề xuất suy ra, chờ xác nhận. (Phần "`HOLOCRON_VLM_MODE` không đặt ⇒ không cấm" đã đóng: duyệt 2026-11-03 (người dùng uỷ quyền chọn phương án đề xuất), câu hỏi 2 của `.claude/state/briefs/x02b-open-questions.md` ghi rõ "`HOLOCRON_VLM_MODE` mặc định off (đề xuất: không đặt ⇒ không cấm)"; thay câu duyệt 2026-10-29 "HOLOCRON_VLM_MODE mặc định off". An toàn giữ nhờ công tắc mặc định tắt.)
2. **Bỏ mã `vlm_disabled` khỏi `CapabilityReason`**, thay bằng `ai_status.roles[].reason = "mode_off"` (mục 12.11). Mã này được duyệt 2026-10-29 (cùng câu ở OPEN 1) và test ngược "không đặt MODE ⇒ `vlm_disabled`" được duyệt ở "VLM 1.2 — sửa test" (chưa vào `develop`). Đề xuất suy ra, chờ xác nhận.
3. **`vlm_local`, `vlm_data_recipient` thành `ai_status.roles[].local`, `recipient_label`** (mục 0). Biến và quy tắc giữ theo VLM 1.2 (c); chỉ chỗ đặt field đổi. Đề xuất suy ra, chờ xác nhận.
4. **Khối `vlm` qua cổng vẫn không tự xác nhận** (mục 3.6): đi ngược "không duyệt chay, không phiền". Đã duyệt tạm 2026-11-03 (phương án 6); xem lại sau khi có số đo cổng VLM trên bộ mù.
5. **Bảng giá mặc định** (mục 12.3) là số bảo thủ người viết đặt, không trích bảng giá công bố. Cần giá thật (V01 hoặc người dùng) trước khi dựa vào con số USD.
6. **Trần byte ảnh:** phương án 4 giữ "ảnh ≤ 4 MiB" (Q13) nhưng ADR-010 giới hạn `crop_max_image_bytes` ≤ 1 MiB; trần hiệu lực là 1 MiB (mục 5, 6). Nâng trần cần sửa ADR-010.
7. **Prompt cho vùng** (`PROMPT_VERSION = "v2"`, thêm loại `formula`, câu chữ "vùng" thay "trang"): cần người dùng duyệt văn bản. Tới đó dùng prompt v1 nguyên văn.
8. **Khoá dạng rõ trên Unix** (`plaintext_file`, mục 12.4): điều kiện "không thì file + cảnh báo rõ trong UI và OPEN" của file quyết định mục 3.
9. **Bổ sung bảng CLAUDE.md §9** theo mục 14.1.
10. **Đặt lại bộ đếm chi phí** của một nhà cung cấp (khi nạp thêm credit) chưa có endpoint; hiện chỉ đổi được bằng sửa trần hoặc xoá rồi thêm lại nhà cung cấp người dùng.
11. **Sửa fixture test `tests/test_document_confirm_auto_proposal.py` (và test LLM chọn tên chương) cần người dùng đồng ý** (CLAUDE.md §7): áp công tắc cho job đề xuất cặp tự tạo và LLM chọn tên chương (mục 7) làm các test hiện có fail nếu không bật công tắc trong fixture. Tới khi người dùng đồng ý, hai tính năng đó KHÔNG bị công tắc chặn và `auto_send_blocked` không được sinh. Dự kiến cùng V04.

## Hệ quả

- Khi người dùng bật công tắc và có nhà cung cấp, vùng khó (bảng ảnh, sơ đồ, scan xấu) được đọc kèm cấu trúc; chữ VLM chỉ vào khối khi khớp OCR độc lập, ngược lại khối giữ ảnh cắt + cờ. Khi không bật, không có gì thay đổi (G5, G6).
- Chữ của trang có lớp chữ không bao giờ bị VLM đổi.
- Contract mới (chưa triển khai; `types/holocron.ts` cập nhật cùng PR triển khai, CLAUDE.md §2.3): `PageExtraction.vlm_mismatches`, `VlmTokenMismatch`, `ReviewReason` `VLM_REGION`, `TABLE_TOKEN_MISMATCH`; `kept_mismatches`, `rejected_vlm_blocks` của `PATCH …/lines` và nơi lưu bền `PageExtraction.kept_vlm_mismatch_ids`, `vlm_rejected_block_ids`; 409 ô bảng lệch; `ProposalJobAbsentReason` thêm `auto_send_blocked`; `RuntimeInfo.ai_status`; model và endpoint "Kết nối AI"; field `code` của 403; biến môi trường mới.
- Tính năng đã có (job đề xuất cặp tự tạo, LLM chọn tên chương) không còn tự gửi dữ liệu khi công tắc tắt, từ V04 và sau khi người dùng đồng ý sửa fixture test (mục 7, OPEN 11); trước đó hành vi của chúng không đổi.
- Kho có thêm file cấu hình và file bộ đếm trong thư mục dữ liệu, và bảng cache AI.
- Rủi ro còn lại: VLM và OCR cùng sai một cách thì cổng không phát hiện (con người phát hiện ở bước xác nhận, I5); hai ô bảng tráo giá trị không bị bắt (mục 3.4); prompt injection trong ảnh chỉ ảnh hưởng chữ của vùng, vốn luôn qua cổng và xác nhận, VLM không có công cụ để gọi; khoá dạng rõ trên Unix (OPEN 8).
- ADR-008 mục 6, 8, 9 sửa cùng nhánh (dòng "Sửa đổi 2026-11-03").

## Phương án đã loại

- **VLM cho mọi trang hay mọi vùng.** Tốn tiền và thời gian; trang lớp chữ đã đúng chữ.
- **VLM thay hẳn OCR.** Mất đối chiếu độc lập, không phát hiện chữ bịa.
- **Chữ VLM thắng lớp chữ.** Lớp chữ của PDF số chính xác nhất (file quyết định mục 1).
- **Giữ `Block`/`VlmPageResult` của bản 2026-10-29.** Không truy được nguồn, không kiểm được bảo toàn (ADR-010 "Phương án đã loại").
- **Ảnh cả trang qua loại thông điệp riêng vượt trần dòng chung.** Thêm một đường ảnh thứ hai cạnh `CropRequest`; vùng cắt nhỏ hơn và đủ dưới 8 MiB.
- **Cổng theo tập đếm cả trang (Q14).** Có `bbox` vùng nên so được với OCR của chính vùng; chặt hơn.
- **Đồng ý theo từng tài liệu** (bản 2026-10-29). Thay bằng công tắc toàn cục (phương án 2): một nơi bật/tắt, bao cả chữ.
- **Cache không gắn tài liệu** (bản 2026-10-29). "Xoá là xoá" (phương án 3).
- **Kiểm IP nguồn cho endpoint khoá.** Mọi request qua proxy Next.js đều đến từ loopback; token mới phân biệt được.
- **Lưu khoá ở `localStorage` hay trả khoá về frontend.** Trái điều kiện duyệt của file quyết định mục 3.
- **Coi thiếu bảng giá là chạm trần.** Làm nhà cung cấp mới thêm không dùng được ngay; bảng giá bảo thủ theo mẫu cho cùng mức an toàn (B7').
- **Adapter riêng cho từng nhà cung cấp, SDK của hãng.** Một adapter tương thích OpenAI đủ (file quyết định mục 0).
- **Đặt `temperature = 0`.** Trái spec §11.5; cache cho tính tất định thật.

## Truy vết Phụ lục B → mục

B1 → 0, 2, 5 · B2 → 3.4 (`TABLE_TOKEN_MISMATCH`), 12.11 · B2' → 12.11 · B2'' → 3.2, 12.11 · B3 → 3.1, 3.3 · B3' → 3.2 · B4 → 3.6 · B4' → 2, 3.6 · B5, B5', B5'' → 6, 7 · B6, B6' → 8 · B7, B7', B7'' → 12.1–12.3 · B8 → 12.4 · B9, B9', B9'' → 12.7 · B10, B10' → 12.8 · B11 → 12.9 · B12, B12' → 12.6, 12.10 · B13 → 12.1, 12.2, 12.5.
