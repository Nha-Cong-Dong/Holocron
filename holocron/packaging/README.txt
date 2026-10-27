Holocron - bản portable cho Windows 10/11 64-bit (chạy offline, không cần cài Python).

1. Trước khi giải nén: nhấp phải file .zip > Properties > tích "Unblock" > OK
   (nếu bỏ qua, Windows có thể chặn Holocron.cmd vì file tải từ mạng).
2. Giải nén vào thư mục có đường dẫn ngắn, chỉ gồm chữ không dấu, ví dụ C:\Holocron\
   (tránh thư mục có dấu tiếng Việt, tránh thư mục đồng bộ OneDrive).
3. Nhấp đúp Holocron.cmd. Cửa sổ đen hiện địa chỉ http://127.0.0.1:<cổng>/ và trình duyệt tự mở.
4. Muốn tắt: đóng cửa sổ đen hoặc nhấn Ctrl+C trong cửa sổ đó.

Dữ liệu (tài liệu đã nhập, lịch sử làm bài) nằm ở %LOCALAPPDATA%\Holocron\data, nên giải nén
bản mới không mất dữ liệu. Muốn mang dữ liệu theo USB: Holocron.cmd --data-dir <thư mục>.
Đọc PDF scan (ảnh chụp) cần Tesseract: xem HUONG-DAN-TESSERACT.txt. PDF có sẵn chữ thì không cần.
Log: %LOCALAPPDATA%\Holocron\logs\holocron.log (mở nhanh: Mo-thu-muc-log.cmd), kể cả khi dùng --data-dir: hai
cửa sổ chạy hai --data-dir khác nhau ghi chung file này. Log có thể chứa đoạn văn bản tài liệu: xem trước khi gửi.
Giấy phép: LICENSE (Holocron, MIT) và THIRD_PARTY_NOTICES.txt (thành phần bên thứ ba).
