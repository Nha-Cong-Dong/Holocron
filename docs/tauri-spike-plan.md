# Kế hoạch spike đóng gói Tauri

- Trạng thái: kế hoạch, chưa làm. Viết ngày 2026-10-25.
- Liên quan: `SPECIFICATION.md` §1.4 (nền tảng), §5.13 (`HOLOCRON_DATA_DIR`), §6.0 (quy ước API), §11.2 (biến môi trường), §11.4 (chế độ khả năng), §13.1 (mô hình bảo mật), §16 (lịch); ADR-002 (sandbox), ADR-008 (bộ nhập PDF); `docs/releases/RELEASE_CHECKLIST.md` mục 3.

Máy phát triển hiện **chưa có Rust**. Tài liệu này chỉ là kế hoạch. Đợt viết tài liệu không cài gì; việc cài toolchain ở bước 0 do người dùng tự làm khi bắt đầu spike.

## 1. Mục tiêu

Trả lời được, bằng số đo trên máy thật, các câu hỏi sau trước khi cam kết đóng gói desktop:

1. Tauri bọc được frontend Next.js hiện có không, và phải đổi gì ở frontend.
2. Backend Python chạy được như tiến trình sidecar không, kể cả **sandbox** (ADR-002) và **worker đọc PDF** (ADR-008 mục 3), vì cả hai đều tự spawn một trình thông dịch Python con.
3. Mô hình bảo mật §13.1 (kiểm `Host` luôn bật, token khi đặt `HOLOCRON_REQUIRE_TOKEN`) hoạt động giữa WebView và sidecar.
4. Kích thước bộ cài, thời gian khởi động nguội (checklist mục 3: ≤ 5 s) và RAM lúc nghỉ.

Kết quả của spike là số đo và một danh sách quyết định cần người dùng duyệt, không phải bản phát hành.

## 2. Ngoài phạm vi

- macOS, ký số bộ cài, tự cập nhật, sandbox lớp L4 (§1.6).
- Đóng gói Tesseract cùng app (sau MVP, ADR-008 mục 8). Trong spike, Tesseract do người dùng tự cài.
- Thêm dependency vào repo. Mọi crate, plugin hay công cụ đóng gói dùng trong spike phải được duyệt riêng trước khi vào nhánh chính (CLAUDE.md §2.2: ghim version, giấy phép tương thích MIT, không GPL/AGPL).

## 3. Tiền đề (người dùng tự cài ở bước 0)

| Nền tảng | Cần cài |
|---|---|
| Windows | `rustup` (toolchain stable MSVC), Visual Studio Build Tools (C++), WebView2 Runtime (Windows 11 có sẵn), Node.js ≥ 20 |
| Linux | `rustup`, các gói phát triển của WebKitGTK theo tài liệu Tauri v2 cho bản phân phối đang dùng, Node.js ≥ 20 |

Ghi lại version của từng thứ đã cài vào kết quả spike.

## 4. Các bước

| Bước | Việc | Kết quả cần ghi |
|---|---|---|
| 0 | Người dùng cài tiền đề ở mục 3 | Version của toolchain |
| 1 | Dựng app Tauri v2 tối thiểu hiển thị một trang tĩnh, ngoài cây `holocron/` (thư mục tạm hoặc nhánh spike) | Kích thước bộ cài rỗng, thời gian mở cửa sổ |
| 2 | Thử build frontend dạng tĩnh (`output: "export"` của Next.js) và nạp vào Tauri | Danh sách chỗ vỡ: rewrite `/api/holocron/*` (không có Next server), route động `/ingestion/[itemId]`, `/blueprints/[blueprintId]` |
| 3 | Chạy backend như sidecar: Tauri khởi động tiến trình Python, backend chọn cổng trống trên `127.0.0.1` và báo cổng cho Tauri, Tauri chờ `GET /v1/runtime` trả 200 rồi mới mở UI, và kill sidecar khi đóng app | Thời gian từ lúc mở app tới lúc UI dùng được; sidecar có bị bỏ lại sau khi thoát không |
| 4 | Nối WebView với backend: thử cả CORS chỉ cho origin của Tauri và proxy qua Tauri; gửi token trong header (§13.1) | Phương án chạy được, số dòng phải đổi ở `lib/api.ts`, `Host` mà backend nhận được |
| 5 | Trong bản đóng gói, chạy walkthrough (`python -m scripts.run_walkthrough`) và một PDF mẫu tự tạo qua worker đọc PDF | Sandbox và worker có spawn được Python con không; Job Object (Windows) và RLIMIT (Linux) còn hiệu lực không |
| 6 | Kiểm khả năng `ocr` (§11.4) khi Tesseract cài ngoài app: PATH của app GUI có thể khác PATH của shell | Có cần `HOLOCRON_TESSERACT_CMD` đặt qua màn hình cài đặt không |
| 7 | Truyền `HOLOCRON_DATA_DIR` = thư mục dữ liệu app của Tauri, và key LLM từ kho khoá của hệ điều hành (§13.1) | Đường dẫn thực tế trên Windows và Linux |
| 8 | Đo | Kích thước bộ cài, cold start, RAM lúc nghỉ, so với R9 và checklist mục 3 |
| 9 | Viết kết quả và danh sách quyết định; nếu cần đổi contract (ví dụ §6.0 về CORS) thì viết ADR mới ở trạng thái *Đề xuất* | ADR mới hoặc ghi chú trong spec |

## 5. Rủi ro

### 5.1 Sidecar Python

- **Sandbox và worker cần một trình thông dịch Python thật.** Sandbox chạy template bằng tiến trình con `python -S -s -P` (§9.1), worker PDF cũng là tiến trình con (ADR-008 mục 3). Nếu backend được đóng gói thành một file thực thi đông lạnh (frozen), `sys.executable` trỏ vào file đó chứ không phải trình thông dịch, nên hai cơ chế này có thể hỏng. Spike PHẢI thử phương án mang theo một bản Python độc lập (bản phân phối Python dựng sẵn, cùng `site-packages` của app) và so với phương án đóng băng.
- **Giấy phép của công cụ đóng gói.** Một số công cụ đóng băng Python dùng GPL kèm ngoại lệ cho bootloader. CLAUDE.md §2.2 cấm GPL/AGPL; ngoại lệ này có được chấp nhận hay không là quyết định của người dùng. Ưu tiên phương án mang theo Python độc lập có giấy phép PSF hoặc tương thích MIT.
- **Thời gian khởi động.** Bản đóng băng dạng một file thường giải nén ra thư mục tạm mỗi lần chạy, có thể vượt ngưỡng 5 s của checklist mục 3.
- **Vòng đời tiến trình.** Đóng app phải kill sidecar cùng mọi tiến trình con (sandbox, worker, Tesseract). Trên Windows nên đặt sidecar vào Job Object có `KILL_ON_JOB_CLOSE`, như sandbox đang làm.
- **Cổng.** Cổng cố định 8000 có thể bị chiếm. Sidecar nên tự chọn cổng trống và báo cho Tauri; kiểm `Host` (§13.1) phải dùng đúng cổng đó.
- **Mã hoá.** Sidecar phải đọc/ghi UTF-8 tường minh và có môi trường tối thiểu (`SYSTEMROOT` trên Windows), theo CLAUDE.md §4.4.
- **Phần mềm diệt virus và SmartScreen** có thể cảnh báo file thực thi chưa ký, nhất là bản đóng băng Python. Ghi lại nếu gặp.

### 5.2 Tesseract do người dùng tự cài

- App GUI có thể không thấy `tesseract` trong PATH dù shell thấy. Khả năng `ocr` (§11.4) phải báo đúng `ocr_engine_missing` kèm hướng dẫn, và UI cần chỗ đặt `HOLOCRON_TESSERACT_CMD`, `HOLOCRON_TESSDATA_DIR`.
- Bản cài Tesseract có thể thiếu `vie.traineddata` hoặc mang bản `tessdata_fast`. Worker đã kiểm unicharset khi khởi động (ADR-008 mục 14 test 1); spike chỉ cần xác nhận kiểm tra này chạy được trong bản đóng gói.
- Không có Tesseract thì trang có lớp chữ vẫn đọc được ở tầng 1, còn lát cắt dọc chạy được với PDF số (§16).

### 5.3 Token và `Host`

- **Next.js rewrite không còn** khi frontend là bản tĩnh trong Tauri. Khi đó WebView gọi thẳng `http://127.0.0.1:<port>`, tức khác origin, nên cần một trong hai: bật CORS chỉ cho origin của Tauri, hoặc proxy request qua Tauri. Cả hai đều đổi quy ước "cùng origin, không cần CORS" ở §6.0, nên cần người dùng duyệt (và ADR nếu đổi contract).
- **Header token gây preflight** khi khác origin. Phương án CORS phải cho phép đúng header token và chỉ origin của Tauri.
- **`Host` mà backend nhận** phải là `127.0.0.1:<port>` hoặc `localhost:<port>`. Spike ghi lại giá trị thực tế với từng phương án ở bước 4.
- **Token** sinh ngẫu nhiên mỗi lần khởi động, truyền cho sidecar qua `HOLOCRON_REQUIRE_TOKEN` và cho WebView; không ghi log, không ghi ra file. Kiểm rằng trang web khác mở trong trình duyệt không gọi được API (403).

### 5.4 Frontend

- `output: "export"` không hỗ trợ route động nếu không liệt kê trước tham số. Có thể phải chuyển `item_id`, `blueprint_id` sang query string hoặc định tuyến phía client. Việc này đổi frontend, cần nhánh riêng sau spike.
- `lib/api.ts` là nơi duy nhất gọi backend (CLAUDE.md §4.2), nên đổi địa chỉ gốc và thêm header token chỉ chạm một file.

## 6. Tiêu chí kết thúc spike

- **Đạt:** walkthrough và một PDF mẫu chạy trong bản đóng gói trên Windows; sandbox vẫn chặn được payload thoát (chạy lại bộ test bảo mật bằng Python đi kèm); cold start ≤ 5 s; trang web khác không gọi được API.
- **Dừng và báo người dùng:** sandbox hoặc worker không spawn được Python con trong mọi phương án đóng gói đã thử; hoặc phương án duy nhất chạy được cần dependency GPL/AGPL; hoặc cold start vượt xa 5 s mà không có hướng giảm.

## 7. Quyết định cần người dùng duyệt sau spike

1. Cách mang Python theo app (Python độc lập hay bản đóng băng) và giấy phép của công cụ.
2. CORS cho origin Tauri hay proxy qua Tauri; cập nhật §6.0 tương ứng.
3. Crate và plugin Tauri cần thêm (ví dụ cho kho khoá hệ điều hành, khởi động sidecar), mỗi thứ ghim version và kiểm giấy phép.
4. Đổi route động của frontend nếu `output: "export"` bắt buộc.
