# Checklist thử bản zip portable trên máy Windows sạch

Tài liệu cho người kiểm tra chạy tay; KHÔNG đưa vào bản zip. Mỗi mục ghi `ĐẠT` / `HỎNG` kèm ghi chú vào bảng
kết quả cuối file (hoặc bản sao của nó), rồi gửi lại cho orchestrator.

## 0. Chuẩn bị

Chạy đủ checklist trên **từng** môi trường dưới đây (mỗi môi trường một cột trong bảng kết quả):

| Mã | Môi trường | Cách dựng |
|---|---|---|
| A | Windows Sandbox (Windows 10/11 Pro), không Python, không VC++ Redistributable | Bật tính năng "Windows Sandbox", mở Sandbox, chép zip vào |
| B | Máy/VM Windows 10 hoặc 11 64-bit không cài Python, không cài VC++ Redistributable | Kiểm `Apps & features` không có "Python" và "Microsoft Visual C++ 2015-2022 Redistributable" |
| C | Máy có Python 3.14 cài chuẩn (bộ cài python.org, có khoá registry `PythonPath`) | Kiểm `reg query "HKCU\Software\Python\PythonCore\3.14\PythonPath"` hoặc `HKLM\...` có giá trị |
| D | Như A, B hoặc C, thêm một subkey HKCU giả dưới `PythonPath` | Xem các lệnh ngay dưới bảng |
| E | Máy ĐÃ cài sẵn một Python khác (bản 3.x bất kỳ, có registry `PythonCore`), thêm `PYTHONPATH`/`PYTHONHOME` | Xem mục 7 |

Dựng môi trường D. Chỉ tạo subkey `HolocronProbe`; không sửa hay xoá khoá nào có sẵn trên máy (có thể là khoá của
bản Python đã cài).

Bước 0, TRƯỚC `reg add`: kiểm từng khoá cha có tồn tại không và ghi lại CÓ/KHÔNG (lệnh báo "ERROR: The system was
unable to find" nghĩa là KHÔNG). `reg add` sẽ tự tạo các khoá cha còn thiếu; bước dọn dựa vào ghi chép này.

```
reg query "HKCU\Software\Python"
reg query "HKCU\Software\Python\PythonCore"
reg query "HKCU\Software\Python\PythonCore\3.14"
reg query "HKCU\Software\Python\PythonCore\3.14\PythonPath"
```

Bước 1, tạo thư mục giả và subkey thử:

```
mkdir C:\gia-mao
echo raise SystemExit("gia mao")> C:\gia-mao\fastapi.py
reg add "HKCU\Software\Python\PythonCore\3.14\PythonPath\HolocronProbe" /ve /d "C:\gia-mao" /f
reg query "HKCU\Software\Python\PythonCore\3.14\PythonPath\HolocronProbe"
```

Dọn sau khi thử xong môi trường D. Luôn xoá đúng subkey đã tạo và thư mục giả:

```
reg delete "HKCU\Software\Python\PythonCore\3.14\PythonPath\HolocronProbe" /f
rmdir /s /q C:\gia-mao
```

Sau đó xoá các khoá cha do `reg add` tạo ra, theo thứ tự từ dưới lên. Chỉ xoá một khoá khi bước 0 ghi **KHÔNG** cho
đúng khoá đó; khoá ghi CÓ thì giữ nguyên, và dừng luôn ở đó (khoá cha của nó cũng đã có sẵn):

| Bước 0 ghi KHÔNG cho | Lệnh dọn |
|---|---|
| `...\3.14\PythonPath` | `reg delete "HKCU\Software\Python\PythonCore\3.14\PythonPath" /f` |
| `...\PythonCore\3.14` | `reg delete "HKCU\Software\Python\PythonCore\3.14" /f` |
| `...\Python\PythonCore` | `reg delete "HKCU\Software\Python\PythonCore" /f` |
| `HKCU\Software\Python` | `reg delete "HKCU\Software\Python" /f` |

Cuối cùng chạy lại bốn lệnh `reg query` của bước 0: kết quả CÓ/KHÔNG phải giống hệt lúc đầu.

Kỳ vọng ở môi trường D: thư mục `C:\gia-mao` KHÔNG được xuất hiện trong `sys.path` của tiến trình sandbox (mục 5) và
của launcher. Thấy `C:\gia-mao` trong `sys.path` hoặc thấy chữ "gia mao" ⇒ ghi **HỎNG — báo orchestrator**.

Ghi lại: phiên bản Windows (`winver`), tên file zip và SHA-256 (so với file `.sha256` đi kèm:
`certutil -hashfile Holocron-<version>-win64.zip SHA256`).

## 1. Tải, Unblock, giải nén

1. Tải zip (trình duyệt, để Windows gắn nhãn "tải từ Internet").
2. Nhấp phải zip > Properties > tích **Unblock** > OK. Ghi lại nếu không thấy ô Unblock.
3. Giải nén vào **hai** nơi và chạy các bước 2–6 cho cả hai:
   - đường dẫn ASCII ngắn, ví dụ `C:\Holocron\`;
   - đường dẫn có dấu tiếng Việt, ví dụ `C:\Users\<tên>\Tài liệu\Thử ố\`.
4. Kiểm thư mục `python\` KHÔNG có file `*._pth` nào.

## 2. Khởi động

1. Nhấp đúp `Holocron.cmd`. Cửa sổ đen hiện `Holocron: http://127.0.0.1:<cổng>/`, trình duyệt tự mở.
2. Ghi lại dòng trạng thái OCR (có/thiếu Tesseract, có/thiếu `vie`).
3. Nếu hiện lỗi thiếu `msvcp140.dll`, `vcruntime140.dll` hoặc `VCRUNTIME140_1.dll` (hộp thoại hệ thống hoặc
   `ImportError: DLL load failed`): ghi **HỎNG — báo orchestrator** kèm ảnh chụp và nguyên văn lỗi. KHÔNG tự cài VC++
   Redistributable để chữa (chưa có quyết định đóng gói VC++ runtime); dừng ở đây với môi trường đó.
4. Môi trường C và D: launcher phải chạy bình thường; nếu thấy "gia mao" hoặc lỗi import lạ, ghi **HỎNG — báo
   orchestrator**.

## 3. Giao diện và API (trình duyệt)

1. Trang chủ mở được; xem nguồn trang (Ctrl+U) có chuỗi `__HOLOCRON_RUNTIME__`.
2. Mở `http://127.0.0.1:<cổng>/vault/`: trang hiện, không lỗi 404.
3. Mở `http://127.0.0.1:<cổng>/v1/khong-co`: trả JSON có `"detail"` (404).
4. Mở `http://127.0.0.1:<cổng>/v1/runtime`: trả JSON (200).

## 4. Nhập tài liệu

1. Tải lên một PDF có sẵn chữ (tự tạo, ví dụ in ra PDF từ Word/Notepad). Chờ đọc xong; trang 1 hiện đúng văn bản.
2. Tải lên một PDF scan không có lớp chữ (chụp/quét một trang chữ in, hoặc "Microsoft Print to PDF" một ảnh).
   Chờ đọc xong; mở trang 1:
   - có Tesseract: trang có văn bản OCR; ô đối chiếu (cross-check) là "khớp" hoặc "lệch" với lượt RapidOCR
     (KHÔNG phải "không khả dụng" hay "không áp dụng");
   - không có Tesseract: ghi lại thông báo hiển thị (dự kiến hướng dẫn cài Tesseract).

## 5. Kiểm sandbox bằng tay (I1)

Mở `cmd` tại thư mục đã giải nén, chạy:

```
python\python.exe -S -s -P -c "import sys; print(sys.path); import holocron_core"
```

Đạt khi: mọi đường dẫn in ra đều nằm trong thư mục `python\` của zip, KHÔNG có `python\Lib\site-packages`, VÀ dòng
`import holocron_core` báo `ModuleNotFoundError`. Môi trường C và D: thêm `import fastapi` thay cho `import holocron_core`
ở lệnh trên, cũng phải `ModuleNotFoundError` (không được in "gia mao"). Môi trường D: `C:\gia-mao` có trong danh sách
in ra ⇒ **HỎNG — báo orchestrator**.

## 6. Tắt

1. Đóng cửa sổ đen (hoặc Ctrl+C). Trong 10 s, Task Manager không còn `python.exe` hay `tesseract.exe` chạy từ thư mục zip.
2. Mở lại `Holocron.cmd`: tài liệu đã nhập ở bước 4 vẫn còn.
3. Nhấp đúp `Mo-thu-muc-log.cmd`: Explorer mở `%LOCALAPPDATA%\Holocron\logs` và có `holocron.log`. Hai dòng đầu
   của phiên mới nhất là `Holocron <version> (<commit 7 ký tự>)` và `Log: <đường dẫn>`, khớp hai dòng cửa sổ đen in sau
   dòng "Đóng cửa sổ này…". Trước khi gửi log, mở xem: log có thể chứa đoạn văn bản tài liệu trong traceback.

## 7. Máy đã cài sẵn Python khác (môi trường E)

Mục tiêu: xác nhận app và sandbox KHÔNG lấy nhầm đường dẫn của Python đã cài trên máy, kể cả khi đặt
`PYTHONPATH`/`PYTHONHOME`. Chỉ đặt biến trong MỘT cửa sổ `cmd` (lệnh `set`); không dùng `setx`, không sửa biến hệ thống.
Ở môi trường E chạy cả mục 1–6 trong chính cửa sổ `cmd` đã đặt biến ở bước 3 (mở `Holocron.cmd` bằng cách gõ tên).

1. Ghi lại Python đã cài: `where python` và `reg query "HKCU\Software\Python\PythonCore" /s` (không có thì thử
   `HKLM\Software\Python\PythonCore`). Ghi thư mục cài (ví dụ
   `C:\Users\<tên>\AppData\Local\Programs\Python\Python313`), gọi là `<PYKHAC>`.
2. Tạo thư mục giả (nếu chưa có từ môi trường D):
   ```
   mkdir C:\gia-mao
   echo raise SystemExit("gia mao")> C:\gia-mao\fastapi.py
   ```
3. Mở `cmd` tại thư mục đã giải nén, đặt biến chỉ trong cửa sổ này (thay `<PYKHAC>` bằng đường dẫn thật):
   ```
   set PYTHONPATH=C:\gia-mao;<PYKHAC>\Lib\site-packages
   set PYTHONHOME=<PYKHAC>
   ```
4. Trong cùng cửa sổ, kiểm Python của zip:
   ```
   python\python.exe -I -X utf8 -c "import sys; print(sys.executable); print(sys.prefix); print(sys.path)"
   ```
   Đạt khi: `sys.executable` và `sys.prefix` nằm trong thư mục `python\` của zip; mọi đường dẫn in ra nằm trong thư
   mục đã giải nén; KHÔNG có `C:\gia-mao`, KHÔNG có đường dẫn nào bắt đầu bằng `<PYKHAC>`.
5. Trong cùng cửa sổ, chạy lệnh ở mục 5 (cả biến thể `import holocron_core` và `import fastapi`): phải
   `ModuleNotFoundError`, KHÔNG in "gia mao", danh sách in ra không có `C:\gia-mao` hay `<PYKHAC>`.
6. Trong cùng cửa sổ, gõ `Holocron.cmd` (không nhấp đúp, để kế thừa biến). Launcher phải chạy bình thường như mục
   2–3, rồi nhập một PDF có chữ như mục 4.1 (bước này chạy sandbox). Thấy "gia mao", lỗi import lạ,
   `Fatal Python error: init_fs_encoding` hay `No module named 'encodings'` ⇒ **HỎNG — báo orchestrator**.
7. Đóng cửa sổ `cmd` (biến mất theo cửa sổ). Nếu thư mục giả tạo ở bước 2: `rmdir /s /q C:\gia-mao`.

## Kết quả

| Mục | A | B | C | D | E | Ghi chú |
|---|---|---|---|---|---|---|
| 1. Unblock, giải nén ASCII | | | | | | |
| 1. Giải nén đường dẫn có dấu | | | | | | |
| 1.4 Không có `*._pth` | | | | | | |
| 2. Khởi động, URL hiện | | | | | | |
| 2.3 Thiếu `msvcp140.dll`/VC++ | | | | | | ghi HỎNG — báo orchestrator nếu gặp |
| 3. Giao diện và API | | | | | | |
| 4.1 PDF có chữ | | | | | | |
| 4.2 PDF scan, cross-check | | | | | | |
| 5. Sandbox chỉ thấy `python\` | | | | | | |
| 6. Tắt sạch, dữ liệu còn | | | | | | |
| 6.3 Log và `Mo-thu-muc-log.cmd` | | | | | | |
| 7. Python khác + `PYTHONPATH`/`PYTHONHOME` | – | – | – | – | | chỉ chạy ở E |

Người kiểm: ________ Ngày: ________ Phiên bản zip/SHA-256: ________
