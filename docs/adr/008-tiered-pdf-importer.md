# ADR 008: Bộ nhập PDF theo tầng

- Trạng thái: Đề xuất
- Ngày: 2026-10-24

## Bối cảnh

`SPECIFICATION.md` §1.5 (v0.4) yêu cầu một bộ nhập PDF dùng chung cho tài liệu học (đi vào Vault, ADR-006) và đề thi (đi vào Ingestion Gate). Bộ nhập chia bốn tầng: lớp chữ, OCR, bố cục bảng và công thức, VLM tuỳ chọn. Các ràng buộc:

- **Không có VLM vẫn phải chạy đủ luồng** (G5). DeepSeek, API do ban tổ chức cấp, không có model thị giác (R4).
- **Tiếng Việt có dấu** phải đọc tốt. PDF tiếng Việt cũ hay dùng font mã TCVN3/VNI, khiến lớp chữ là ký tự rác dù trang hiển thị bình thường (R8).
- **Chạy offline** trong desktop app Tauri, backend là sidecar Python. Dung lượng gói là chi phí thật (R9).
- **Giấy phép tương thích MIT**, không GPL/AGPL (CLAUDE.md §2.2), vì app sẽ được phát hành.
- **File PDF là đầu vào không tin cậy.** CLAUDE.md §2.2 yêu cầu bộ đọc đặt ở `infrastructure/` sau một `Protocol`, có giới hạn kích thước, số trang và thời gian, không thực thi macro, script hay nội dung nhúng.
- Không tự train model.

## Khảo sát thư viện

Giấy phép và ngày phát hành lấy ngày 2026-10-24 từ PyPI JSON API (`license_expression`, `license`, classifier) và, với giấy phép tuỳ biến, từ file LICENSE/README trên GitHub của dự án. Kích thước là kích thước wheel `win_amd64` (hoặc `none-any`) của bản mới nhất, chưa giải nén. Chất lượng tiếng Việt **chưa đo** cho thư viện nào; cột đó chỉ ghi mức hỗ trợ được tài liệu công bố.

### Tầng 1: lớp chữ

| Thư viện | Bản, ngày phát hành | Giấy phép (đã kiểm) | Kích thước | Ghi chú | Kết luận |
|---|---|---|---|---|---|
| pypdfium2 | 5.14.0, 2026-10-04 | BSD-3-Clause, Apache-2.0 (PDFium) | 3.9 MB | Engine PDFium của Chrome, chịu được file hỏng tốt; có toạ độ từng ký tự và render trang ra ảnh cho OCR. Wheel mặc định không có V8/XFA nên không chạy JavaScript trong PDF | **Chọn** |
| pdfplumber + pdfminer.six | 0.11.10 (2026-06-15) + 20260107 | MIT + MIT | 0.1 + 6.6 MB | Tách bảng tốt trên PDF số; parser viết bằng Python, chậm hơn | Dùng cho tầng 3 |
| pypdf | 6.21.2, 2026-10-21 | BSD-3-Clause | 0.4 MB | Tách chữ kém giữ bố cục, không render được trang | Loại |
| PyMuPDF | 2.0, 2026-10-19 | **AGPL-3.0** hoặc thương mại | 63.0 MB | Mạnh nhất nhưng giấy phép không tương thích | **Loại** (AGPL) |

### Tầng 2: OCR

| Thư viện | Bản, ngày phát hành | Giấy phép (đã kiểm) | Kích thước và phụ thuộc nặng | Tiếng Việt (theo tài liệu) | Kết luận |
|---|---|---|---|---|---|
| RapidOCR (`rapidocr`) | 3.10.0, 2026-10-08 | Apache-2.0; model gốc từ PaddleOCR (Apache-2.0) | 27.3 MB + onnxruntime 15.3 MB (MIT) + opencv-python-headless 43.8 MB (Apache-2.0, đóng kèm FFmpeg LGPL-2.1) | Model `latin_PP-OCRv5` liệt kê `vi` | **Chọn sơ bộ**, chốt sau phép đo |
| Tesseract (`tesserocr` hoặc `pytesseract`) | tesserocr 2.12.0 (2026-10-22); pytesseract 0.3.13 (2024-08-16) | tesserocr MIT; pytesseract Apache-2.0; Tesseract Apache-2.0 | tesserocr 24.1 MB (kèm thư viện Tesseract trên Windows) + `vie.traineddata` (tessdata_best) 12.4 MB | Có model riêng cho tiếng Việt | **Phương án so sánh** trong phép đo |
| PaddleOCR | 3.7.0, 2026-06-11 | Apache-2.0 | paddlepaddle 104.8 MB + paddlex | Cùng model với RapidOCR | Loại: cùng model nhưng nặng hơn nhiều |
| EasyOCR | 1.7.2, 2024-09-24 | Apache-2.0 | Cần PyTorch (torch 124 MB trên Windows, 555 MB trên Linux) | Có `vi` | Loại: phụ thuộc PyTorch, hơn 2 năm không ra bản mới |
| VietOCR | 0.3.13, 2024-03-29 | MIT | Cần PyTorch; chỉ nhận dạng dòng, phải ghép thêm bộ phát hiện chữ | Chuyên tiếng Việt | Loại: phụ thuộc PyTorch, gần 3 năm không ra bản mới |

### Tầng 3: bảng và công thức

| Thư viện | Bản, ngày phát hành | Giấy phép (đã kiểm) | Phụ thuộc nặng | Kết luận |
|---|---|---|---|---|
| pdfplumber | 0.11.10, 2026-06-15 | MIT | Không | **Chọn** cho bảng trên PDF số |
| Lưới từ toạ độ chữ OCR (code của dự án) | — | MIT (code dự án) | Không | **Chọn** cho bảng trên PDF scan: gom ô theo toạ độ dòng và cột |
| Docling | 2.142.1, 2026-10-23 | MIT (cả `docling-ibm-models` 4.0.4) | PyTorch, transformers, model tải về | Để sau MVP: chất lượng bố cục tốt nhưng làm gói nặng thêm hàng trăm MB |
| Marker / Surya | marker-pdf 2.0.0 (2026-07-20); surya-ocr 0.22.1 | Code Apache-2.0; **model weights theo OpenRAIL-M sửa đổi** (miễn phí cho nghiên cứu, cá nhân và startup dưới 5 triệu USD) | PyTorch, transformers | **Loại**: điều kiện giấy phép weights không tương thích phát hành tự do |
| MinerU | 4.0.12, 2026-10-11 | Apache-2.0 **kèm điều khoản bổ sung** (bắt buộc ghi công khi cung cấp dịch vụ online; ngưỡng MAU/doanh thu cần giấy phép thương mại) | onnxruntime và nhiều model | **Loại**: điều khoản bổ sung, nặng; bản cũ `magic-pdf` 1.3.12 là AGPL-3.0 |

Công thức: không có thư viện nhận dạng công thức offline nào vừa nhẹ vừa không cần PyTorch. MVP chỉ lấy công thức từ lớp chữ của PDF số. Công thức trên trang scan được đánh dấu "có công thức" để người dùng xem lại, hoặc gửi VLM nếu đã cấu hình.

### Tầng 4: VLM

Dùng cấu hình `HOLOCRON_VLM_*` sẵn có (spec §11.2): nhà cung cấp bất kỳ tương thích OpenAI nhận `image_url`, người dùng tự nhập key. Không thêm dependency.

## Quyết định

1. **Thư viện:** tầng 1 dùng pypdfium2; tầng 2 dùng RapidOCR với onnxruntime và model `latin_PP-OCRv5` (chốt sau phép đo ở mục 5); tầng 3 dùng pdfplumber cho PDF số và code lưới riêng cho PDF scan; tầng 4 dùng VLM tuỳ chọn qua cấu hình hiện có. Dependency được ghim chính xác version. Model OCR đóng gói kèm app, tắt tính năng tự tải model của RapidOCR để chạy offline.
2. **Vị trí trong code:** `infrastructure/document_readers/` sau `Protocol` `PdfReader`. Service nhập tài liệu ở `services/` gọi qua `Protocol`; test thay bằng stub và file mẫu nhỏ tự tạo.
3. **Cô lập:** việc đọc PDF chạy trong **tiến trình con riêng** (worker), theo tinh thần ADR-002:
   - Worker tự áp giới hạn OS ngay khi khởi động: RLIMIT trên Unix (sau kiểm tra `sys.platform != "win32"`), Job Object trên Windows. RAM 2 GiB (OCR cần nhiều hơn sandbox), CPU theo trang, `RLIMIT_FSIZE` 0, cwd là thư mục tạm, biến môi trường tối thiểu, đọc/ghi UTF-8 tường minh.
   - Tiến trình cha giữ wall-clock timeout theo trang: 5 s cho tầng 1, 30 s cho tầng 2. Quá hạn thì kill worker, đánh dấu trang `failed`, dựng worker mới cho trang tiếp theo.
   - Worker nhận đường dẫn file chỉ-đọc, trả kết quả dạng JSON qua stdout theo từng trang. Kết quả được validate bằng model Pydantic ở tiến trình cha, như `SandboxResult`.
4. **Giới hạn đầu vào và xử lý file xấu:**
   - Nhận diện bằng magic bytes `%PDF-` (đã có trong `sniff_media_type`). Tối đa 50 MiB và 300 trang mỗi tài liệu.
   - Render trang để OCR ở 300 DPI, tối đa 25 megapixel mỗi trang; trang lớn hơn thì giảm DPI. Không giải nén ảnh nhúng trực tiếp, nên tránh được bom giải nén.
   - Không khởi tạo form, không chạy JavaScript/XFA (wheel không có V8), bỏ qua file đính kèm, liên kết và nội dung nhúng.
   - PDF cần mật khẩu để mở: trả 422 "PDF có mật khẩu", không thử phá. PDF chỉ có mật khẩu chủ (giới hạn quyền) vẫn đọc được vì là tài liệu của chính người dùng.
   - Không mở được tài liệu: 422 cho cả tài liệu. Lỗi ở một trang: trang đó `failed`, các trang khác vẫn xử lý.
5. **Phép đo chọn engine OCR**, làm đầu tiên ở Giai đoạn 4, sau khi được duyệt dependency:
   - Script opt-in ở `holocron/backend/scripts/`, chạy ngoài pytest, theo CLAUDE.md §2.2.
   - Dữ liệu: ít nhất 20 trang tiếng Việt có dấu do nhóm tự soạn, in ra PDF rồi render thành bản scan ở 200 và 300 DPI, có trang chứa bảng số.
   - Chỉ số: CER (tỉ lệ lỗi ký tự, tính cả dấu) và tỉ lệ chữ số đọc sai.
   - Chọn engine có CER thấp hơn. Ghi số liệu vào ADR này trước khi chuyển trạng thái sang *Chấp nhận*.
6. **Độ tin cậy theo trang và cách chọn tầng:**

   | Bước | Điểm | Cách tính |
   |---|---|---|
   | Tầng 1 | `text_score` ∈ [0, 1] | Tích của ba thành phần: (a) độ đủ chữ `min(1, số ký tự không trắng / 200)`; (b) `1 − tỉ lệ ký tự rác` (U+FFFD, vùng Private Use, ký tự điều khiển); (c) tỉ lệ từ hợp lệ, trong đó từ chứa ký tự đặc trưng của TCVN3/VNI (ví dụ `¬`, `−`, `®` lẫn giữa chữ) bị tính là không hợp lệ |
   | Tầng 2 | `ocr_score` ∈ [0, 1] | Trung bình điểm nhận dạng của các dòng, có trọng số theo số ký tự; dòng điểm thấp được tô trên màn hình xem lại |
   | Cờ trang | `has_figure`, `has_table`, `has_formula` | `has_figure`: tỉ lệ diện tích đối tượng ảnh và đường vẽ trên trang; `has_table`: lưới đường kẻ (pdfplumber) hoặc lưới toạ độ chữ; `has_formula`: mật độ ký hiệu toán (∑ ∫ √ ≤ ≥ ±, chữ Hy Lạp, chỉ số trên/dưới) |

   - Dùng lớp chữ khi `text_score ≥ 0.85`; ngược lại OCR trang đó.
   - Trang có điểm của tầng đã dùng `< 0.80`, hoặc có `has_figure` hay `has_formula` trên trang scan, được đánh dấu "cần xem kỹ". Nếu đã cấu hình VLM, các trang này được gửi VLM.
   - Ngưỡng 0.85 và 0.80 là giá trị khởi đầu, hiệu chỉnh bằng phép đo ở mục 5.
7. **Xem lại trước khi lưu:** mọi kết quả, kể cả trang `text_score` cao, đều qua màn hình xem lại. Trang "cần xem kỹ" và dòng điểm thấp được làm nổi bật. Chỉ văn bản người dùng đã xác nhận mới vào Vault hoặc Ingestion Gate.

## Hệ quả

- PDF số đọc nhanh và chính xác ở tầng 1. PDF scan tiếng Việt chạy offline bằng OCR mà không cần PyTorch.
- Dung lượng: phần tầng 1–2 (pypdfium2, rapidocr, onnxruntime, opencv-python-headless) khoảng 90 MB wheel Windows chưa giải nén, chưa tính numpy, shapely và model `latin`. Con số chính xác đo khi đóng gói Tauri.
- **Thêm dependency đọc file không tin cậy:** pypdfium2 (tầng 1), rapidocr và onnxruntime (tầng 2), pdfplumber (tầng 3). pdfplumber thêm một parser PDF thứ hai (pdfminer.six), làm tăng bề mặt tấn công; vì vậy nó cũng chạy trong worker có giới hạn, và có thể bỏ nếu tầng 3 bị cắt (thứ tự cắt ở kế hoạch 24–25/10).
- **Giấy phép đi kèm:** OpenCV đóng kèm FFmpeg theo LGPL-2.1 (liên kết động, không thuộc nhóm GPL/AGPL bị cấm). Bản phát hành phải kèm file thông báo giấy phép bên thứ ba.
- **Chạm sandbox, cần duyệt riêng:** worker cần cùng mã giới hạn OS đang nằm trong `sandbox/_sandbox_child.py`. Có hai cách: tách phần đó thành một module chỉ dùng thư viện chuẩn để hai child dùng chung (sửa file của sandbox), hoặc worker giữ một bản sao và ghi nợ trùng lặp vào ADR-004. Cả hai đều thuộc nhóm DỪNG VÀ HỎI ở CLAUDE.md §3.
- Công thức trên PDF scan chưa được nhận dạng offline; người dùng phải gõ lại hoặc bật VLM.
- Kiểm chứng: test với file mẫu nhỏ tự tạo, gồm một PDF số, một PDF scan, một PDF hỏng hoặc quá lớn, và một PDF dùng font TCVN3 để kiểm `text_score`. Có thêm test timeout và giới hạn trang.

## Phương án đã loại

- **Dùng VLM làm đường đọc chính.** Trái G5; tốn tiền của người dùng cho mỗi trang; DeepSeek không có model thị giác.
- **Gọi dịch vụ OCR đám mây.** Tài liệu rời máy, cần thêm key, không chạy offline.
- **Đọc PDF ngay trong tiến trình FastAPI.** Lỗi hoặc bom bộ nhớ trong một file độc hại làm sập cả backend.
- Các thư viện bị loại trong bảng khảo sát, với lý do ghi ở từng dòng.
