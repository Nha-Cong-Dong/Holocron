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
Giấy phép: LICENSE (Holocron, MIT) và THIRD_PARTY_NOTICES.txt (thành phần bên thứ ba).
