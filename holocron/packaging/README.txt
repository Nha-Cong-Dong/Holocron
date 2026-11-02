Holocron - bản portable cho Windows 10/11 64-bit (chạy offline, không cần cài Python).
1. Trước khi giải nén: nhấp phải file .zip > Properties > tích "Unblock" > OK
   (nếu bỏ qua, Windows có thể chặn Holocron.cmd vì file tải từ mạng).
2. Giải nén vào thư mục đường dẫn ngắn, không dấu, ngoài OneDrive, ví dụ C:\Holocron\
3. Nhấp đúp Holocron.cmd. Cửa sổ đen hiện địa chỉ http://127.0.0.1:<cổng>/ và trình duyệt tự mở.
4. Muốn tắt: đóng cửa sổ đen hoặc nhấn Ctrl+C trong cửa sổ đó.
Dữ liệu (tài liệu đã nhập, lịch sử làm bài) nằm ở %LOCALAPPDATA%\Holocron\data, nên giải nén
bản mới không mất dữ liệu. Muốn mang dữ liệu theo USB: Holocron.cmd --data-dir <thư mục>.
Đọc PDF scan (ảnh chụp) cần Tesseract: xem HUONG-DAN-TESSERACT.txt. PDF có sẵn chữ thì không cần.
Log: %LOCALAPPDATA%\Holocron\logs\holocron.log (mở nhanh: Mo-thu-muc-log.cmd), dùng chung cho mọi --data-dir.
Chạy hai cửa sổ Holocron cùng lúc (hai --data-dir khác nhau): khi log vượt 5 MB, bước xoay vòng có thể lỗi, mất
dòng log và cửa sổ đen in "Logging error". Log có thể chứa đoạn văn bản tài liệu: xem trước khi gửi cho người khác.
Chế độ app: có Chrome hoặc Brave thì mở cửa sổ riêng (hồ sơ ở %LOCALAPPDATA%\Holocron\browser-profile), không thì
trình duyệt mặc định. Chọn: Holocron.cmd --browser auto | chrome | brave | default (mặc định: tự tìm Chrome, rồi Brave).
Giấy phép: LICENSE (Holocron, MIT) và THIRD_PARTY_NOTICES.txt (thành phần bên thứ ba).
