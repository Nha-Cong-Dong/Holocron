# ADR 008: Bộ nhập PDF theo tầng

- Trạng thái: Chấp nhận (2026-10-23). Engine OCR của tầng 2 đã chốt ngày 2026-10-24, sau phép đo ở mục 5 của phần Quyết định (đo trên scan giả lập): Tesseract `vie` là engine chính, RapidOCR chỉ đối chiếu token số và mã (mục 8). Các mục 8–14 bổ sung ngày 2026-10-24. Bảy điểm từng ghi "đề xuất" trong các mục này được người dùng duyệt ngày 2026-10-24, ghi lại tại chỗ là "đã duyệt 2026-10-24". Contract chi tiết (mục 11, 13) nằm ở spec §5.7, §6.5 và §7.4. Sửa ngày 2026-10-25 theo quyết định của người dùng: câu chữ VLM và nhà cung cấp LLM (Bối cảnh, Phương án đã loại), pdfplumber đã duyệt (mục 1, Hệ quả), ghi chú `find_tables` (mục 6), lệnh Tesseract (mục 8), `page_number` (mục 13), chữ hiếm được phép thiếu (mục 14).
- Sửa đổi 2026-10-27: bỏ nhà cung cấp mặc định DeepSeek, xem PR #61.
- Sửa đổi 2026-10-27 (Q4): người dùng duyệt ngoại lệ GEOS (LGPL-2.1, liên kết động) cho bản zip portable Windows đủ có RapidOCR (`build_portable.py --ocr full`), xem phần Hệ quả (mục "Giấy phép đi kèm") và spec dòng "Bộ nhập PDF" ở bảng công nghệ. Điều kiện: kèm toàn văn LGPL-2.1 và mã nguồn GEOS 3.14.1; THIRD_PARTY_NOTICES của zip ghi DLL đã bị delvewheel sửa đổi; build dừng nếu GEOS ngoài `shapely.libs/` hoặc shapely không có ánh xạ GEOS (cưỡng chế ở `scripts/build_portable.py`, nhánh P4, chưa merge). Bản lite không có GEOS. Trạng thái không đổi.
- Ngày: 2026-10-23; cập nhật 2026-10-24 (kết quả đo, engine OCR, chuyển tầng, đối chiếu chéo; duyệt các điểm đề xuất; contract ở spec); cập nhật 2026-10-25, 2026-10-27

## Bối cảnh

`SPECIFICATION.md` §1.5 (v0.4) yêu cầu một bộ nhập PDF dùng chung cho tài liệu học (đi vào Vault, ADR-006) và đề thi (đi vào Ingestion Gate). Bộ nhập chia bốn tầng: lớp chữ, OCR, bố cục bảng và công thức, VLM tuỳ chọn. Các ràng buộc:

- **Không có VLM vẫn phải chạy đủ luồng** (G5). Nhà cung cấp LLM do cấu hình quyết định, không có nhà cung cấp mặc định (hiện là gateway kiểu OpenAI của ban tổ chức, model `claude-haiku-5-5`). Contract hiện chỉ có adapter kiểu OpenAI Chat Completions sau `Protocol` `ChatCompletionClient` (tên đề xuất, spec §11.5); DeepSeek hay server local tương thích OpenAI dùng được qua adapter này; Anthropic gốc (API không theo giao thức OpenAI) cần một adapter riêng cài cùng `Protocol`, chưa có. Chưa kiểm gateway của ban tổ chức có nhận ảnh không; VLM park (R4; duyệt 2026-10-25).
- **Tiếng Việt có dấu** phải đọc tốt. PDF tiếng Việt cũ hay dùng font mã TCVN3/VNI, khiến lớp chữ là ký tự rác dù trang hiển thị bình thường (R8).
- **Chạy offline** trong desktop app Tauri, backend là sidecar Python. Dung lượng gói là chi phí thật (R9).
- **Giấy phép tương thích MIT**, không GPL/AGPL (CLAUDE.md §2.2), vì app sẽ được phát hành.
- **File PDF là đầu vào không tin cậy.** CLAUDE.md §2.2 yêu cầu bộ đọc đặt ở `infrastructure/` sau một `Protocol`, có giới hạn kích thước, số trang và thời gian, không thực thi macro, script hay nội dung nhúng.
- Không tự train model.

## Khảo sát thư viện

Giấy phép và ngày phát hành lấy ngày 2026-10-23 từ PyPI JSON API (`license_expression`, `license`, classifier) và, với giấy phép tuỳ biến, từ file LICENSE/README trên GitHub của dự án. Kích thước là kích thước wheel `win_amd64` (hoặc `none-any`) của bản mới nhất, chưa giải nén. Lúc khảo sát, chất lượng tiếng Việt **chưa đo** cho thư viện nào; cột đó ghi mức hỗ trợ được tài liệu công bố. Kết quả đo tầng 2 ngày 2026-10-24 nằm ở mục "Kết quả đo".

### Tầng 1: lớp chữ

| Thư viện | Bản, ngày phát hành | Giấy phép (đã kiểm) | Kích thước | Ghi chú | Kết luận |
|---|---|---|---|---|---|
| pypdfium2 | 5.14.0, 2026-10-04 | BSD-3-Clause, Apache-2.0 (PDFium) | 3.9 MB | Engine PDFium của Chrome, chịu được file hỏng tốt; có toạ độ từng ký tự và render trang ra ảnh cho OCR. Wheel mặc định không có V8/XFA nên không chạy JavaScript trong PDF | **Chọn** |
| pdfplumber + pdfminer.six | 0.11.10 (2026-06-15) + 20260107 | MIT + MIT | 0.1 + 6.6 MB | Tách bảng tốt trên PDF số; parser viết bằng Python, chậm hơn | Dùng cho tầng 3 |
| pypdf | 6.21.2, 2026-10-21 | BSD-3-Clause | 0.4 MB | Tách chữ kém giữ bố cục, không render được trang | Loại |
| PyMuPDF | 2.0, 2026-10-19 | **AGPL-3.0** hoặc thương mại | 63.0 MB | Mạnh nhất nhưng giấy phép không tương thích | **Loại** (AGPL) |

### Tầng 2: OCR

| Thư viện | Bản, ngày phát hành | Giấy phép (đã kiểm) | Kích thước và phụ thuộc nặng | Tiếng Việt | Kết luận |
|---|---|---|---|---|---|
| RapidOCR (`rapidocr`) | 3.10.0, 2026-10-08 | Apache-2.0; model gốc từ PaddleOCR (Apache-2.0) | 27.3 MB + onnxruntime 15.3 MB (MIT) + opencv-python-headless 43.8 MB (Apache-2.0, đóng kèm FFmpeg LGPL-2.1). Wheel đóng sẵn model PP-OCRv6 small | Tài liệu liệt kê `vi` cho `latin_PP-OCRv5`, nhưng **đo ngày 2026-10-24 thì từ điển ký tự của mọi model nhận dạng thiếu chữ có dấu thanh** (mục "Kết quả đo") | **Chỉ làm lượt đọc thứ hai**, đối chiếu token số và mã (mục 8) |
| Tesseract (binary `tesseract`; wrapper `tesserocr`, `pytesseract`) | tesserocr 2.12.0 (2026-10-22); pytesseract 0.3.13 (2024-08-16) | Tesseract Apache-2.0; tesserocr MIT nhưng wheel kèm thư viện LGPL/GPL (mục "Kết quả đo"); pytesseract Apache-2.0 | Binary do người dùng tự cài trong MVP; `vie.traineddata` (tessdata_best) 12.4 MB; wheel tesserocr 24.1 MB (không dùng) | Có model riêng cho tiếng Việt; CER 1.9% trên scan giả lập | **Chọn làm engine chính** (2026-10-24): gọi binary qua subprocess, không dùng `tesserocr` hay `pytesseract` |
| PaddleOCR | 3.7.0, 2026-06-11 | Apache-2.0 | paddlepaddle 104.8 MB + paddlex | Cùng model với RapidOCR | Loại: cùng model nhưng nặng hơn nhiều |
| EasyOCR | 1.7.2, 2024-09-24 | Apache-2.0 | Cần PyTorch (torch 124 MB trên Windows, 555 MB trên Linux) | Có `vi` | Loại: phụ thuộc PyTorch, hơn 2 năm không ra bản mới |
| VietOCR | 0.3.13, 2024-03-29 | MIT | Cần PyTorch; chỉ nhận dạng dòng, phải ghép thêm bộ phát hiện chữ | Chuyên tiếng Việt | Loại: phụ thuộc PyTorch, gần 3 năm không ra bản mới |

### Tầng 3: bảng và công thức

| Thư viện | Bản, ngày phát hành | Giấy phép (đã kiểm) | Phụ thuộc nặng | Kết luận |
|---|---|---|---|---|
| pdfplumber | 0.11.10, 2026-06-15 | MIT | Không | **Chọn** cho bảng trên PDF số |
| Lưới từ toạ độ chữ OCR (code của dự án) | — | MIT (code dự án) | Không | **Chọn** cho bảng trên PDF scan: gom ô theo toạ độ dòng và cột |
| Docling | 2.142.1, 2026-10-23 | Code MIT (cả `docling-ibm-models` 4.0.4); weights Apache-2.0 và CDLA-Permissive-2.0 | PyTorch, transformers, model tải về | Không dùng cho MVP; số đo ở mục "Đánh đổi Docling" bên dưới |
| Marker / Surya | marker-pdf 2.0.0 (2026-07-20); surya-ocr 0.22.1 | Code Apache-2.0; **model weights theo OpenRAIL-M sửa đổi** (miễn phí cho nghiên cứu, cá nhân và startup dưới 5 triệu USD) | PyTorch, transformers | **Loại**: điều kiện giấy phép weights không tương thích phát hành tự do |
| MinerU | 4.0.12, 2026-10-11 | Apache-2.0 **kèm điều khoản bổ sung** (bắt buộc ghi công khi cung cấp dịch vụ online; ngưỡng MAU/doanh thu cần giấy phép thương mại) | onnxruntime và nhiều model | **Loại**: điều khoản bổ sung, nặng; bản cũ `magic-pdf` 1.3.12 là AGPL-3.0 |

Công thức: không có thư viện nhận dạng công thức offline nào vừa nhẹ vừa không cần PyTorch. MVP chỉ lấy công thức từ lớp chữ của PDF số. Công thức trên trang scan được đánh dấu "có công thức" để người dùng xem lại, hoặc gửi VLM nếu đã cấu hình.

### Tầng 4: VLM

Dùng cấu hình `HOLOCRON_VLM_*` sẵn có (spec §11.2): nhà cung cấp bất kỳ tương thích OpenAI nhận `image_url`, người dùng tự nhập key. Không thêm dependency.

### Đánh đổi Docling với bộ thư viện nhẹ (đo 2026-10-24)

Đo với docling 2.142.1:

- **Dung lượng:** 1.1 GB site-packages (torch 481 MB, 105 gói), cộng model 0.34 GB. Bật CodeFormula thêm 0.61 GB.
- **Tốc độ:** 1.52 s/trang khi không OCR; 14.7 s/trang khi bật công thức.
- **Công thức tách dòng:** không bật CodeFormula thì mất chữ (`formula-not-decoded`).
- **OCR tiếng Việt không tốt hơn**, vì Docling cũng cắm cùng các engine OCR ở trên.
- **Giấy phép:** code MIT; weights Apache-2.0 và CDLA-Permissive-2.0.

Chi phí nếu đổi sang Docling:

- duyệt lại khoảng 100 dependency, trong đó có torch;
- gói desktop nặng thêm 1.5–2 GB;
- viết lại tầng 1.

Kết luận: không dùng cho MVP. Sau MVP có thể thêm Docling như một worker tuỳ chọn, tải về riêng, đi qua cùng `Protocol` `PdfReader`.

## Quyết định

1. **Thư viện:** tầng 1 dùng pypdfium2. Tầng 2 dùng Tesseract `vie` (tessdata_best) làm engine chính, gọi binary `tesseract` có sẵn trên máy qua subprocess (mục 8); RapidOCR với onnxruntime chỉ làm lượt đọc thứ hai để đối chiếu token số và mã (mục 11). Tầng 3 dùng pdfplumber cho PDF số và code lưới riêng cho PDF scan. Tầng 4 dùng VLM tuỳ chọn qua cấu hình hiện có. Model của RapidOCR đóng gói kèm app, tắt tính năng tự tải model để chạy offline. Tesseract và `vie.traineddata` do người dùng tự cài trong MVP.
   - **Đã duyệt ngày 2026-10-23, ghim chính xác:** `pypdfium2==5.14.0`, `rapidocr==3.10.0`, `onnxruntime==1.31.0`. `opencv-python-headless==5.0.0.93` là phụ thuộc bắc cầu của `rapidocr` và cũng được ghim, vì nó mang thành phần LGPL (xem phần Hệ quả). Các version này giữ nguyên sau quyết định ngày 2026-10-24.
   - **Không thêm gói Python nào cho Tesseract** (không `tesserocr`, không `pytesseract`), xem mục 8.
   - `pdfplumber==0.11.10` và `pdfminer.six==20260107` (MIT) duyệt 2026-10-25 (giấy phép kiểm trên PyPI cùng ngày). Chỉ thêm vào `pyproject.toml` khi bắt đầu làm tầng 3.
2. **Vị trí trong code:** `infrastructure/document_readers/` sau `Protocol` `PdfReader`. Service nhập tài liệu ở `services/` gọi qua `Protocol`; test thay bằng stub và file mẫu nhỏ tự tạo.
3. **Cô lập:** việc đọc PDF chạy trong **tiến trình con riêng** (worker), theo tinh thần ADR-002:
   - Worker tự áp giới hạn OS ngay khi khởi động: RLIMIT trên Unix (sau kiểm tra `sys.platform != "win32"`), Job Object trên Windows. RAM 2 GiB (OCR cần nhiều hơn sandbox), CPU theo trang, `RLIMIT_FSIZE` 0, cwd là thư mục tạm, biến môi trường tối thiểu, đọc/ghi UTF-8 tường minh.
   - **Mã giới hạn OS dùng chung (đã duyệt, phương án a):** phần áp RLIMIT/Job Object hiện nằm trong `sandbox/_sandbox_child.py` được tách thành một module chỉ dùng thư viện chuẩn, để child của sandbox và worker đọc PDF cùng dùng. Việc tách nằm trong **một commit riêng chỉ để refactor** và được review riêng. Mọi test sandbox hiện có (`tests/test_sandbox_security.py`, `tests/test_sandbox_limits.py`) phải xanh mà không sửa test. Commit này không đổi giá trị giới hạn hay whitelist nào của sandbox.
   - Tiến trình cha giữ wall-clock timeout theo trang: 5 s cho tầng 1, 30 s cho tầng 2. Quá hạn thì kill worker, đánh dấu trang `failed`, dựng worker mới cho trang tiếp theo.
   - Worker nhận đường dẫn file chỉ-đọc, trả kết quả dạng JSON qua stdout theo từng trang. Kết quả được validate bằng model Pydantic ở tiến trình cha, như `SandboxResult`.
4. **Giới hạn đầu vào và xử lý file xấu:**
   - Nhận diện bằng magic bytes `%PDF-` (đã có trong `sniff_media_type`). Tối đa 50 MiB và 300 trang mỗi tài liệu.
   - Render trang để OCR ở 300 DPI, tối đa 25 megapixel mỗi trang; trang lớn hơn thì giảm DPI. Không giải nén ảnh nhúng trực tiếp, nên tránh được bom giải nén.
   - Không khởi tạo form, không chạy JavaScript/XFA (wheel không có V8), bỏ qua file đính kèm, liên kết và nội dung nhúng.
   - PDF cần mật khẩu để mở: trả 422 "PDF có mật khẩu", không thử phá. PDF chỉ có mật khẩu chủ (giới hạn quyền) vẫn đọc được vì là tài liệu của chính người dùng.
   - Không mở được tài liệu: 422 cho cả tài liệu. Lỗi ở một trang: trang đó `failed`, các trang khác vẫn xử lý.
5. **Phép đo chọn engine OCR**, làm **trước khi viết code tầng 2** (đầu Giai đoạn 4):
   - Script opt-in ở `holocron/backend/scripts/`, chạy ngoài pytest, theo CLAUDE.md §2.2. Lúc đo, Tesseract chỉ được cài trong môi trường đo. Sau quyết định ngày 2026-10-24, Tesseract là engine chính do người dùng tự cài (mục 8), nhưng vẫn không phải dependency Python của app.
   - **Dữ liệu (cách đo kết hợp, thay yêu cầu "5–10 trang scan có bản gõ tay đầy đủ", duyệt 2026-10-24):**
     - (i) PDF số: lớp chữ làm đáp án. Render trang thành ảnh, làm mờ và thêm nhiễu (scan giả lập), rồi OCR và so với đáp án.
     - (ii) 2–3 trang scan thật: mỗi trang chỉ gõ tay bảng số và một đoạn văn để làm đáp án.
     - File giữ trong `eval_data/` (đã nằm trong `.gitignore`), không commit. Đáp án gõ tay cũng giữ trong `eval_data/`.
   - Chỉ số: CER tính cả dấu (sau chuẩn hoá Unicode NFC) và tỉ lệ chữ số đọc đúng, so các model RapidOCR với Tesseract `vie` (tessdata_best và tessdata_fast).
   - **Quy tắc dừng (ngưỡng đã duyệt 2026-10-23):** nếu RapidOCR kém rõ rệt, tức CER cao hơn Tesseract quá 2 điểm phần trăm tuyệt đối hoặc tỉ lệ chữ số sai cao hơn, thì DỪNG và báo người làm dự án, không tự đổi engine. **Đã kích hoạt ngày 2026-10-24**; người dùng chọn engine theo mục 8.
   - Ghi số liệu vào mục "Kết quả đo" của ADR này. Phép đo (ii) chưa làm; khi làm xong thì bổ sung vào cùng mục.
6. **Độ tin cậy theo trang và cách chọn tầng:**

   | Bước | Điểm | Cách tính |
   |---|---|---|
   | Tầng 1 | `text_score` ∈ [0, 1] | Tích của ba thành phần: (a) độ đủ chữ `min(1, số ký tự không trắng / 200)`; (b) `1 − tỉ lệ ký tự rác` (U+FFFD, vùng Private Use, ký tự điều khiển); (c) tỉ lệ từ hợp lệ, trong đó từ chứa ký tự đặc trưng của TCVN3/VNI (ví dụ `¬`, `−`, `®` lẫn giữa chữ) bị tính là không hợp lệ |
   | Tầng 2 | `ocr_score` ∈ [0, 1] | Trung bình điểm nhận dạng các dòng của engine chính, có trọng số theo số ký tự. Với Tesseract, điểm dòng là trung bình cột `conf` (0–100, chia 100) của các từ trong dòng ở đầu ra TSV, có trọng số theo số ký tự. Dòng điểm thấp được tô trên màn hình xem lại. Điểm trung bình cao chưa đủ: phải kiểm thêm độ phủ dòng (mục 10) |
   | Cờ trang | `has_figure`, `has_table`, `has_formula` | `has_figure`: trên trang scan dùng `ink_outside_text_ratio` (mục 9); trên PDF số dùng diện tích đối tượng ảnh và đường vẽ, nhưng chỉ kết hợp với tín hiệu khác (mục 9). `has_table`: lưới toạ độ chữ (tầng 1–2), cộng lưới đường kẻ của pdfplumber khi có tầng 3. `has_formula`: mật độ ký hiệu toán (∑ ∫ √ ≤ ≥ ±, chữ Hy Lạp, chỉ số trên/dưới, và dải ký tự toán U+1D400–1D7FF) |

   - Dùng lớp chữ khi `text_score ≥ 0.85`; ngược lại OCR trang đó.
   - Trang có điểm của tầng đã dùng `< 0.80` được đánh dấu "cần xem kỹ". Cách chuyển tầng theo cờ trang ở mục 9. Nếu đã cấu hình VLM, các trang "cần xem kỹ" được gửi VLM.
   - Ngưỡng 0.85 và 0.80 là giá trị khởi đầu, hiệu chỉnh bằng phép đo ở mục 5. Chúng là cấu hình, không ghi cứng (mục 12).
   - Ghi chú `has_table` (đo 2026-10-25 trên slide thật): `find_tables` của pdfplumber báo bảng ở hầu hết các trang vì khung slide; `has_table` phải lọc thêm, không lấy thẳng kết quả `find_tables` (cách lọc đề xuất, chờ duyệt, ở spec §5.7).
7. **Xem lại trước khi lưu:** mọi kết quả, kể cả trang `text_score` cao, đều qua màn hình xem lại. Trang "cần xem kỹ" và dòng điểm thấp được làm nổi bật. Chỉ văn bản người dùng đã xác nhận mới vào Vault hoặc Ingestion Gate.
8. **Engine OCR (người dùng chọn ngày 2026-10-24):**
   - **Engine chính của tầng 2 là Tesseract `vie` (tessdata_best).** App gọi binary `tesseract` do người dùng tự cài, qua subprocess. Không thêm gói Python nào: không dùng `tesserocr` (rủi ro giấy phép của wheel, mục "Kết quả đo"), không dùng `pytesseract`.
   - **Cách gọi:** đầu vào là ảnh PNG do pypdfium2 render, không phải file PDF, nên Tesseract không đọc file không tin cậy gốc. Subprocess dùng UTF-8 tường minh, môi trường tối thiểu (`SYSTEMROOT` trên Windows) và timeout (CLAUDE.md §4.4). Tesseract chạy như tiến trình con của worker ở mục 3, nên chịu giới hạn OS của worker (RLIMIT kế thừa trên Unix; Job Object bao cả tiến trình con trên Windows). Đã duyệt 2026-10-24: truyền ảnh qua stdin và đọc kết quả TSV qua stdout, để giữ được `RLIMIT_FSIZE` 0. Lệnh (sửa 2026-10-25): `tesseract stdin stdout -l vie -c tessedit_create_tsv=1`, vì file cấu hình `tsv` không có trong thư mục `--tessdata-dir` riêng (đo ngày 2026-10-25: dùng `tsv` thì ra văn bản thường); lệnh cũ `tesseract stdin stdout -l vie tsv` bị thay.
   - **Thiếu engine:** worker kiểm có binary và có `vie.traineddata` khi khởi động (`tesseract --version`, `--list-langs` với `--tessdata-dir`, có timeout). Thiếu một trong hai thì trang scan được gửi VLM nếu có key; nếu không có key thì trang bị đánh dấu `failed`, kèm hướng dẫn cài Tesseract và `vie.traineddata`. Trang có lớp chữ tốt vẫn đọc bình thường ở tầng 1.
   - **RapidOCR chỉ làm lượt đọc thứ hai**, đối chiếu token số và mã (mục 11). Các dependency đã duyệt giữ nguyên version ghim. Không dùng văn bản của RapidOCR cho nội dung. Đã duyệt 2026-10-24: lượt thứ hai dùng model PP-OCRv6 small đóng sẵn trong wheel `rapidocr` 3.10.0, vì có tỉ lệ chữ số đúng cao nhất (98.6%) và không cần tải thêm model `latin`.
   - **Sau MVP:** đóng gói Tesseract cùng app. Khi làm việc đó phải kiểm giấy phép của bản build Tesseract được đóng gói (thư viện ảnh và thư viện kèm theo).
   - PKG-001 (FFmpeg LGPL trong `opencv-python-headless`) vẫn còn, vì RapidOCR vẫn được dùng.
9. **Chuyển tầng theo cờ trang:**
   - Trang có `has_table` lên tầng 3: pdfplumber trên PDF số, lưới toạ độ chữ OCR trên trang scan.
   - Trang có `has_formula` hoặc `has_figure` lên tầng 3 nếu là PDF số. Trên trang scan, các trang này được đánh dấu "cần xem kỹ", và gửi VLM nếu đã có key.
   - Nếu tầng 3 bị cắt khỏi MVP thì các trang lẽ ra lên tầng 3 được đánh dấu "cần xem kỹ".
   - **`has_figure` trên trang scan:** đo bằng `ink_outside_text_ratio`, tức tỉ lệ mực nằm ngoài các hộp chữ của lượt OCR chính; bật cờ khi `≥ FIGURE_INK_MIN` (0.10). Số đo: trang thường ≤ 0.004, trang có sơ đồ 0.17. Mẫu số (đã duyệt 2026-10-24): tổng số điểm ảnh mực của trang sau khi nhị phân hoá.
   - **`has_figure` trên PDF số:** diện tích path vector do pypdfium2 báo nhầm khung viền quanh code (16–18% diện tích trang), nên tín hiệu này chỉ dùng cùng tín hiệu khác (ví dụ có đối tượng ảnh trên trang), không dùng một mình.
   - **`has_formula`:** bổ sung dải ký tự toán U+1D400–1D7FF vào mật độ ký hiệu toán (mục 6).
10. **Độ phủ dòng và chữ viết tay:**
    - **Độ phủ dòng:** điểm trung bình không đủ để tin một trang (mục "Kết quả đo": Tesseract bỏ sót chữ trong khung viền mà điểm trung bình vẫn 0.88–0.94). Định nghĩa (đã duyệt 2026-10-24): `coverage_ratio = min(n_chính, n_phụ) / max(n_chính, n_phụ)`, với `n` là số dòng mỗi lượt đọc trả về; `null` khi không có lượt đọc thứ hai. Trang có `coverage_ratio < COVERAGE_MIN` (0.85) thành `needs_review` dù điểm trung bình cao. Khi không có lượt thứ hai, `ink_outside_text_ratio` cao (mục 9) cũng đưa trang vào "cần xem kỹ".
    - **Chữ viết tay:** MVP không nhận dạng chữ viết tay (spec §1.5, phi mục tiêu), chỉ coi một dòng là "nghi ngờ" khi có một trong hai tín hiệu dưới đây. Dòng nghi ngờ được đánh dấu "cần xem kỹ". Cả hai ngưỡng **chưa thử trên chữ viết tay thật**.
      - Độ tin cậy dòng của Tesseract `< LINE_CONF_MIN` (0.70). Đo trên font giả chữ viết tay: 0.25–0.58.
      - Tỉ lệ số dòng giữa hai lượt đọc `< COVERAGE_MIN` (0.85), nếu có lượt đọc thứ hai.
    - **Không làm được:**
      - phân biệt chữ viết tay với chữ in mờ;
      - nhận dạng công thức trên trang scan khi không có VLM;
      - phát hiện lỗi mà cả hai lượt đọc cùng sai (mô phỏng: 1/232 dòng).
11. **Đối chiếu chéo hai lượt đọc:**
    - Lượt đọc thứ hai phải **độc lập**, tức khác họ model với lượt chính. Hai model cùng gốc PaddleOCR (ví dụ PP-OCRv6 và `latin_PP-OCRv5`) không tính là độc lập. Tesseract (LSTM riêng) và RapidOCR (gốc PaddleOCR) là hai họ khác nhau.
    - Chỉ so **token số và mã**, lấy bằng regex `0x[0-9A-F]+|#?-?\d+`, so khớp chính xác sau chuẩn hoá NFC. Dòng của hai lượt được ghép theo hộp toạ độ.
    - Token lệch thì dùng lại mã `DUAL_PASS_MISMATCH` của Gate (spec §7.2), kèm `alternatives` là giá trị của hai lượt. Không thêm mã issue mới cho việc này.
    - Không có lượt đọc thứ hai thì ghi `cross_check = unavailable`, kèm một issue mức WARNING, và ô số không được AUTO_PASS. Mã issue là `CROSS_CHECK_UNAVAILABLE`, gắn cho từng ô số chưa được con người xác nhận (contract ở spec §5.3, §7.2 và §7.4, viết ở Đợt 2b ngày 2026-10-24).
    - Ngưỡng CER văn bản giữa hai lượt (`CROSS_LINE_CER_WARN` 0.05 để tô dòng, 0.15 để đánh dấu trang) là **chưa xác minh**.
    - Lưu ý: đường OCR một lượt hiện **không sinh** `DUAL_PASS_MISMATCH`. Tín hiệu S1 của Gate hiện chỉ đến từ dual-pass VLM; nếu không có lượt thứ hai, S1 im lặng, nên mới cần quy tắc "ô số không được AUTO_PASS" ở trên.
    - **Kiểm theo logic môn:** miền giá trị của ô lấy từ `input_schema` của template đã kiểm chứng (ADR-005 bước 1), không rẽ nhánh theo môn (CLAUDE.md §1).
12. **Cấu hình** (không ghi cứng trong code xử lý):
    - Biến môi trường:

      | Biến | Mặc định | Ý nghĩa |
      |---|---|---|
      | `HOLOCRON_PDF_OCR_PRIMARY` | `tesseract` | Engine chính của tầng 2. Đã duyệt 2026-10-24: MVP chỉ nhận `tesseract`, vì văn bản RapidOCR không dùng cho nội dung |
      | `HOLOCRON_PDF_OCR_SECONDARY` | `rapidocr` | Lượt đọc thứ hai. Rỗng nghĩa là không có lượt thứ hai (`cross_check = unavailable`). Không được cùng họ model với lượt chính (mục 11) |
      | `HOLOCRON_TESSERACT_CMD` | `tesseract` (tìm trong PATH) | Đường dẫn binary Tesseract |
      | `HOLOCRON_TESSDATA_DIR` | thư mục mặc định của bản cài Tesseract | Thư mục chứa `vie.traineddata`, truyền qua `--tessdata-dir`; `TESSDATA_PREFIX` không được app đọc hay truyền (mục 14c, duyệt 2026-10-30) |

    - Ngưỡng, là field có giá trị mặc định của model cấu hình bộ nhập ở `infrastructure/`: `TEXT_SCORE_MIN=0.80`, `OCR_SCORE_MIN=0.80` (giữ ngưỡng 0.80 ở mục 6), `LINE_CONF_MIN=0.70`, `COVERAGE_MIN=0.85`, `FIGURE_INK_MIN=0.10`, `CROSS_LINE_CER_WARN=0.05`. Ngưỡng 0.85 chọn lớp chữ ở mục 6 cũng là cấu hình, tên `TEXT_LAYER_MIN=0.85` (đã duyệt 2026-10-24).
    - Danh sách chữ được phép thiếu trong unicharset của engine chính, `OCR_CHARSET_ALLOWED_MISSING` (mặc định `Ỵ`, `ỵ`; mục 14, duyệt 2026-10-25), cũng là field có giá trị mặc định của model cấu hình OCR ở `infrastructure/`, không phải biến môi trường (theo quy tắc của mục này). Danh sách chỉ được chứa các chữ hiếm đã duyệt (hiện `Ỵ`, `ỵ`); mở rộng danh sách cần người dùng duyệt riêng.
    - Bảng biến môi trường ở spec §11.2 và `.env.example` (gốc repo) đã được cập nhật ở Đợt 2b (2026-10-24), trước khi có code.
13. **Kết quả theo trang `PageExtraction`** (mô tả ở mức ADR; contract JSON chi tiết ở spec §5.7, viết ở Đợt 2b ngày 2026-10-24; `types/holocron.ts` cập nhật cùng nhánh triển khai):

    | Field | Nguồn |
    |---|---|
    | `page_index` | Chỉ số trang trong tài liệu do pypdfium2 trả. Đã duyệt 2026-10-25 (spec §5): contract chỉ dùng `page_number = page_index + 1`, như `ExtractedTable` và `Citation`; `page_index` chỉ dùng bên trong worker |
    | `tier_used` | Tầng đã cho ra văn bản của trang, theo mục 6 và mục 9 |
    | `status` | `ok`, `needs_review` (có ít nhất một lý do trong `review_reasons`) hoặc `failed` (timeout, lỗi trang, thiếu engine OCR mà không có VLM) |
    | `text_score` | Tầng 1, công thức ở mục 6 |
    | `ocr_score` | Engine chính của tầng 2, mục 6; `null` khi trang không OCR |
    | `coverage_ratio` | So số dòng hai lượt đọc, mục 10; `null` khi không có lượt thứ hai |
    | `ink_outside_text_ratio` | Ảnh render của trang và hộp chữ của lượt chính, mục 9; `null` khi trang không OCR |
    | `has_figure`, `has_table`, `has_formula` | Cờ trang, mục 6 và mục 9 |
    | `lines[]` | Mỗi dòng gồm `text` (NFC), `bbox`, `conf` ∈ [0, 1] và `engine` (lớp chữ, Tesseract, RapidOCR hoặc VLM). Trên trang OCR, `text` lấy từ lượt chính; RapidOCR chỉ đóng góp token để đối chiếu |
    | `cross_check` | `ok`, `mismatch` hoặc `unavailable` (mục 11), kèm danh sách token lệch (dòng, giá trị lượt chính, giá trị lượt thứ hai) |
    | `review_reasons[]` | Các lý do đưa trang vào "cần xem kỹ": điểm thấp, độ phủ dòng thấp, có hình hoặc công thức trên trang scan, dòng nghi chữ viết tay, token lệch, thiếu lượt đối chiếu, thiếu engine OCR. Mã cụ thể ở spec §5.7 (`ReviewReason`) |

14. **Sáu test bắt buộc** (trong `holocron/backend/tests/`; tên test và tên file đã duyệt 2026-10-24):
    1. `test_ocr_charset_covers_vietnamese`: từ điển của engine chính chứa đủ 134 chữ cái tiếng Việt ngoài ASCII (67 thường, 67 hoa: 66 nguyên âm có dấu và `đ`), sau chuẩn hoá NFC. Với Tesseract, file `vie.traineddata` do người dùng cài nên CI không kiểm tất định trên file thật được. Cách kiểm thay thế (đã duyệt 2026-10-24): một hàm thuần đọc phần unicharset của file `.traineddata` và trả danh sách chữ còn thiếu; test chạy hàm đó trên file mẫu nhỏ tự tạo (một file đủ chữ, một file thiếu chữ). Worker gọi cùng hàm khi khởi động. Cho phép thiếu các chữ trong danh sách `OCR_CHARSET_ALLOWED_MISSING` (mặc định `Ỵ`, `ỵ`, vì `vie.traineddata` bản best thiếu hai chữ này), kèm cảnh báo. Danh sách chỉ được chứa các chữ hiếm đã duyệt (hiện `Ỵ`, `ỵ`); mở rộng danh sách cần người dùng duyệt riêng; thiếu chữ ngoài danh sách thì coi như thiếu engine (mục 8) (duyệt 2026-10-25). Script đánh giá opt-in chạy hàm trên `vie.traineddata` thật và ghi kết quả.
       Bổ sung (duyệt 2026-10-30): (a) worker (`build_ocr`, `_pdf_worker.py`) gọi CÙNG hàm kiểm bộ chữ với bộ kiểm khả năng, không nhân bản; thiếu chữ ngoài `OCR_CHARSET_ALLOWED_MISSING` ⇒ trang scan `failed` `OCR_ENGINE_MISSING`. (b) Chữ bản chính thức thiếu là `Ỵ` và `ỵ`: đã đo, bản `vie.traineddata` chính thức (tessdata, tessdata_best, tessdata_fast) thiếu `Ỵ`/`ỵ`; nếu không cho phép thiếu thì mọi máy dùng bản chính thức đều bị báo thiếu `vie`. Vì vậy hai chữ này nằm trong danh sách được phép thiếu cùng cảnh báo; test opt-in ghi lại số đo trên ba bản. (c) Dò `vie.traineddata` (duyệt 2026-10-30, thay thứ tự cũ có `TESSDATA_PREFIX`): chỉ kiểm đúng thư mục mà lần gọi OCR thật sẽ dùng. App truyền `--tessdata-dir` từ `HOLOCRON_TESSDATA_DIR` ⇒ kiểm thư mục đó; không truyền, Tesseract 5.x ⇒ kiểm thư mục do `--list-langs` in ra; không truyền, Tesseract 4.x (không in thư mục) ⇒ đường lùi `HOLOCRON_TESSDATA_DIR` → đường dẫn mặc định của bản cài. `TESSDATA_PREFIX` bị bỏ khỏi thứ tự dò: worker chạy môi trường tối thiểu nên engine không đọc nó và app không truyền nó. Một hàm chung xác định thư mục tessdata cho cả bộ kiểm khả năng lẫn worker. Ghi chú code: `HOLOCRON_TESSDATA_DIR` khi đặt luôn được truyền bằng `--tessdata-dir`, nên đường lùi 4.x chỉ còn thư mục mặc định của bản cài (và cạnh `tesseract.exe` trên Windows). Hai trường hợp phân biệt (duyệt 2026-10-30): (i) thư mục đã xác định (`--tessdata-dir` từ `HOLOCRON_TESSDATA_DIR`, hoặc thư mục `--list-langs` in ra) mà KHÔNG TỒN TẠI, thiếu `vie.traineddata`, hoặc file không phải file thường ⇒ `ocr_language_missing`; `detail_vi` nêu tên biến `HOLOCRON_TESSDATA_DIR`, không nêu đường dẫn. (ii) Ngoại lệ đã duyệt: KHÔNG xác định được thư mục tessdata (Tesseract 4.x không in thư mục, đường lùi qua đường dẫn mặc định của bản cài không thấy) ⇒ cả bộ kiểm khả năng lẫn worker bỏ qua kiểm bộ chữ (worker không `failed`), cảnh báo một lần, chỉ kiểm `vie` có trong danh sách ngôn ngữ; không ghi đường dẫn tuyệt đối vào log. Khi không xác định được tessdata thì không kiểm được bộ chữ, app ghi cảnh báo; các cổng sau (độ phủ dòng, đối chiếu số, xem lại trước khi lưu) vẫn áp dụng như với mọi trang OCR. Luồng kiểm nền lỗi ⇒ ghi log và coi như `ocr_engine_missing` (quyết định kỹ thuật của orchestrator, nguồn: brief r4).
    2. `test_low_line_coverage_marks_page_needs_review`: độ phủ dòng thấp thì trang thành `needs_review`, dù điểm trung bình cao.
    3. `test_numeric_token_mismatch_raises_dual_pass_mismatch`: token số lệch giữa hai lượt đọc thì sinh `DUAL_PASS_MISMATCH` kèm `alternatives`.
    4. `test_cross_check_unavailable_blocks_auto_pass`: `cross_check = unavailable` thì ô số không AUTO_PASS.
    5. `test_page_flags_select_tier`: cờ trang `has_table`, `has_formula`, `has_figure` chọn đúng tầng, cho cả PDF số và trang scan.
    6. `test_bad_pdf_limits`: giới hạn file xấu (magic bytes, số trang, timeout), dùng file mẫu nhỏ tự tạo.

    File (đã duyệt 2026-10-24): test 1–4 ở `tests/test_pdf_ocr.py`, test 5 ở `tests/test_pdf_tiers.py`, test 6 ở `tests/test_pdf_input_limits.py`.

    **Đã duyệt 2026-10-24:** sáu test này được gắn vào dòng I5 trong bảng test cưỡng chế ở CLAUDE.md §9 (CLAUDE.md đã cập nhật ở Đợt 2b). Test 2–4 trực tiếp ngăn số chưa được đối chiếu đi tới AUTO_PASS; test 1, 5 và 6 liên quan gián tiếp hơn (chất lượng đọc, chọn tầng, xử lý file không tin cậy).

## Hệ quả

- PDF số đọc nhanh và chính xác ở tầng 1. PDF scan tiếng Việt chạy offline bằng Tesseract mà không cần PyTorch, **với điều kiện người dùng đã cài Tesseract và `vie.traineddata`**. Chưa cài thì trang scan đi VLM nếu có key, nếu không thì `failed` kèm hướng dẫn cài (mục 8). Vì vậy G5 (spec §1.5) với PDF scan cần Tesseract đã cài.
- Dung lượng gói MVP: phần tầng 1–2 (pypdfium2, rapidocr, onnxruntime, opencv-python-headless) khoảng 90 MB wheel Windows chưa giải nén, chưa tính numpy và shapely. Model PP-OCRv6 small đã nằm trong wheel `rapidocr`. Tesseract và `vie.traineddata` (12.4 MB) không nằm trong gói MVP. Con số chính xác đo khi đóng gói Tauri.
- **Thêm dependency đọc file không tin cậy:** pypdfium2 (tầng 1), rapidocr và onnxruntime (lượt đọc thứ hai của tầng 2) đã được duyệt. Tesseract là chương trình ngoài, không phải dependency Python; nó chỉ đọc ảnh PNG do app render, chạy trong worker có giới hạn. pdfplumber (tầng 3) đã được duyệt ngày 2026-10-25 (mục 1) nhưng chỉ được thêm vào `pyproject.toml` khi làm tầng 3; nó mang theo parser PDF thứ hai (pdfminer.six), làm tăng bề mặt tấn công, nên cũng chạy trong worker có giới hạn. Nếu tầng 3 bị cắt (thứ tự cắt ở kế hoạch 24–25/10) thì không thêm.
- **Giấy phép đi kèm (đã kiểm ngày 2026-10-23):**
  - `rapidocr` 3.10.0 vốn phụ thuộc `opencv-python-headless`, không phải `opencv-python`. Bản headless bỏ được Qt (LGPLv3, có trong wheel Linux của bản thường), nhưng **không bỏ được FFmpeg** (thành phần LGPL thứ nhất được phát hiện; GEOS của `shapely` là thành phần thứ hai, xem dưới): wheel `opencv_python_headless-5.0.0.93-cp37-abi3-win_amd64.whl` vẫn chứa `cv2/opencv_videoio_ffmpeg500_64.dll` (30,9 MB, LGPL-2.1). Wheel Linux và macOS chưa được kiểm.
  - FFmpeg là plugin video mà OpenCV chỉ nạp khi đọc hoặc ghi video; OCR không dùng tới. Việc bỏ file plugin này khi đóng gói Tauri, chạy lại test OCR và kiểm wheel Linux/macOS được ghi thành nợ PKG-001 ở ADR-004. Nếu bỏ được thì gói không còn thành phần LGPL, trừ GEOS ở bản zip đủ (xem Sửa đổi 2026-10-27 (Q4)).
  - GEOS (Sửa đổi 2026-10-27, Q4): `shapely==2.2.0` là phụ thuộc bắc cầu của `rapidocr`, wheel `win_amd64` nhúng `shapely.libs/geos-*.dll` và `geos_c-*.dll` (GEOS 3.14.1, LGPL-2.1, do delvewheel đổi tên và sửa bảng import). Cặp ghim: `shapely==2.2.0` → GEOS 3.14.1. Người dùng duyệt phân phối ở bản zip đủ, liên kết động, với điều kiện ở phần Sửa đổi đầu file; bản lite không có GEOS.
  - Wheel `tesserocr` kèm thư viện LGPL/GPL (kiểm ngày 2026-10-24, mục "Kết quả đo"), nên không dùng; cách tránh đã chọn ở mục 8 và được ghi vào PKG-001.
  - Dù bỏ được hay không, repo PHẢI có file `THIRD_PARTY_NOTICES` liệt kê giấy phép của mọi dependency được phát hành kèm app. Giấy phép Apache-2.0 và BSD vốn yêu cầu giữ thông báo bản quyền. File này được thêm trong cùng commit với dependency.
- Công thức trên PDF scan chưa được nhận dạng offline; người dùng phải gõ lại hoặc bật VLM.
- Kiểm chứng: test với file mẫu nhỏ tự tạo, gồm một PDF số, một PDF scan, một PDF hỏng hoặc quá lớn, và một PDF dùng font TCVN3 để kiểm `text_score`. Có thêm test timeout và giới hạn trang. Sáu test bắt buộc ở mục 14.

## Kết quả đo

### Đo ngày 2026-10-24 (scan giả lập)

Đây là phép đo (i) của mục 5, trên **scan giả lập**: trang PDF số được render, làm mờ và thêm nhiễu. Phép đo (ii) trên scan thật **chưa làm**. Dữ liệu: 10 trang của đề cương môn Vi xử lý. Máy đo: CPU i7-6820HQ.

| Engine | CER trang render | CER scan giả lập | Chữ số đúng | Giây/trang |
|---|---|---|---|---|
| RapidOCR PP-OCRv6 small | 13.3% | 14.5% | 98.6% | 4.5–5.1 |
| RapidOCR latin v5 | 16.8% | 17.4% | 98.0% | 6.7–7.6 |
| Tesseract `vie` best | 9.5% (trung vị 1.3%) | 1.9% | 93.0% | 4.5–7.0 |
| Tesseract `vie` fast | 10.2% | 2.6% | 85.2% | 2.0 |

Phát hiện:

- **Nguyên nhân gốc của CER cao ở RapidOCR:** từ điển ký tự trong metadata ONNX của mọi model nhận dạng RapidOCR thiếu chữ tiếng Việt có dấu thanh. Kiểm độc lập trên 146 chữ cái hoa và thường (73 mỗi kiểu: 72 dạng của sáu nguyên âm, kể cả không dấu, và `đ`): PP-OCRv6 small và PP-OCRv6 medium thiếu 88; `latin_PP-OCRv5` thiếu 99. Tài liệu của RapidOCR liệt kê `vi` cho `latin_PP-OCRv5`, nhưng từ điển của model không đủ chữ.
- `rapidocr` 3.10.0 đóng sẵn model PP-OCRv6 small trong wheel.
- Điểm tin cậy của RapidOCR vẫn cao (0.99) dù chữ mất dấu. Điểm tin cậy không phát hiện được lỗi này.
- **Quy tắc dừng ở mục 5 đã kích hoạt:** CER của RapidOCR cao hơn Tesseract `vie` best quá 2 điểm phần trăm. Người dùng chọn engine ngày 2026-10-24 (mục 8).
- **Tesseract bỏ sót chữ trong khung viền:** trang 3 và 4 có CER 50% và 33%, trong khi điểm tin cậy trung bình vẫn 0.88–0.94. Hai trang này kéo CER trung bình của Tesseract best trên trang render lên 9.5%, trong khi trung vị là 1.3%. Vì vậy không được dựa vào điểm trung bình, phải kiểm độ phủ dòng (mục 10).
- **Tesseract đọc chữ số kém hơn RapidOCR** (93.0% so với 98.6%). Đây là lý do giữ RapidOCR làm lượt đọc thứ hai cho token số và mã (mục 11).
- **Rủi ro giấy phép của wheel `tesserocr`:** bản Windows kèm libiconv (LGPL-2.1), bản manylinux kèm libjbig (GPL-2.0+). Hệ quả: không dùng wheel `tesserocr`. Tesseract được gọi bằng binary `tesseract` có sẵn trên máy, qua subprocess, đầu vào là ảnh PNG do pypdfium2 render chứ không phải file PDF (mục 8).

## Phương án đã loại

- **Dùng VLM làm đường đọc chính.** Trái G5; tốn tiền của người dùng cho mỗi trang; chưa kiểm gateway của ban tổ chức có nhận ảnh không, VLM park (duyệt 2026-10-25).
- **Gọi dịch vụ OCR đám mây.** Tài liệu rời máy, cần thêm key, không chạy offline.
- **Đọc PDF ngay trong tiến trình FastAPI.** Lỗi hoặc bom bộ nhớ trong một file độc hại làm sập cả backend.
- **RapidOCR làm engine chính (2026-10-24).** Từ điển model thiếu chữ có dấu thanh, CER trên scan giả lập 14.5–17.4% so với 1.9% của Tesseract `vie` best.
- **Dùng wheel `tesserocr` (2026-10-24).** Wheel kèm thư viện LGPL-2.1 (Windows) và GPL-2.0+ (manylinux).
- **Hai model cùng gốc PaddleOCR làm hai lượt đọc.** Không độc lập, dễ cùng sai một chỗ (mục 11).
- **Docling cho MVP (2026-10-24).** Nặng thêm 1.5–2 GB, khoảng 100 dependency mới gồm torch, OCR tiếng Việt không tốt hơn (mục "Đánh đổi Docling").
- Các thư viện bị loại trong bảng khảo sát, với lý do ghi ở từng dòng.
