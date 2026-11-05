# Holocron desktop (vỏ Tauri, Windows)

Bộ cài NSIS gồm vỏ Tauri v2 (`src-tauri/`), backend Python portable làm sidecar (`scripts/build_portable.py`) và giao
diện tĩnh `out/` do backend phục vụ qua `HOLOCRON_WEB_DIR`. Thiết kế theo spike (phương án B, `docs/tauri-spike-plan.md`
mục 3a). Linux/macOS chưa hỗ trợ.

## Dựng bộ cài (một lệnh, gốc repo)

Cần: Windows 10/11 x64, Rust stable MSVC (`rustup`, VS Build Tools có C++), Node/npm, Python của `.venv` (như
`build_portable.py`), mạng (Python embeddable, wheel, crate, NSIS do tauri-cli tự tải vào `%LOCALAPPDATA%\tauri`).

```powershell
.\.venv\Scripts\python.exe holocron\desktop\build_desktop.py            # --ocr full mặc định; --ocr lite nhẹ hơn
.\.venv\Scripts\python.exe holocron\desktop\build_desktop.py --portable-dir build\portable\stage\Holocron-<version>-win64
```

Kết quả: `dist\Holocron-<version>-desktop-x64-setup.exe` + `.sha256`. Version lấy từ `holocron/frontend/package.json`.
Crate ghim trong `src-tauri/Cargo.lock`, CLI ghim trong `package-lock.json` (`@tauri-apps/cli` 2.12.4).
Chỉ biên dịch vỏ: `cargo build --release` trong `src-tauri/`; test đơn vị: `cargo test` trong `src-tauri/`.
`src-tauri/target/`, `node_modules/`, `build/`, `dist/` không commit.

**Bộ cài CHƯA ký số**: Windows SmartScreen có thể cảnh báo khi chạy file tải từ mạng ("More info" > "Run anyway").
Cài theo người dùng hiện tại (không cần quyền admin), vào `%LOCALAPPDATA%\Programs\Holocron` hoặc thư mục chọn.

## Hành vi lúc chạy

- Mở app: cửa sổ hiện trang chờ trong app; vỏ chạy `portable\python\python.exe -I -X utf8 -m holocron_core.launcher
  --no-browser` (cổng ngẫu nhiên trống ở `127.0.0.1`), chờ `GET /v1/runtime` trả 200 (có token) và kiểm request không
  token bị 403, rồi mới điều hướng tới `http://127.0.0.1:<cổng>/`. Lỗi khởi động hiện trên trang chờ.
- Token `X-Holocron-Token` sinh ngẫu nhiên mỗi lần mở, chỉ ở bộ nhớ của vỏ và env của sidecar
  (`HOLOCRON_REQUIRE_TOKEN`); tầng native WebView2 gắn header cho mọi request tới đúng cổng đó. JS không giữ token;
  không ghi token ra log hay file. Điều hướng chỉ cho trang chờ và đúng cổng sidecar.
- Env của sidecar chỉ gồm biến hệ thống tối thiểu (`SYSTEMROOT`, `TEMP`, `LOCALAPPDATA`, `ProgramFiles`, `PATH`…),
  `HOLOCRON_DATA_DIR`, `HOLOCRON_WEB_DIR`, `HOLOCRON_REQUIRE_TOKEN` và `HOLOCRON_TESSERACT_CMD/TESSDATA_DIR` nếu có;
  KHÔNG chép key LLM/VLM từ env máy (cấu hình qua trang Cài đặt).
- Dữ liệu: `%LOCALAPPDATA%\vn.holocron.desktop\data` (SQLite, file đã nhập); hồ sơ WebView2 ở
  `%LOCALAPPDATA%\vn.holocron.desktop\EBWebView`; log launcher ở `%LOCALAPPDATA%\Holocron\logs\holocron.log`.
- Sidecar nằm trong Job Object `KILL_ON_JOB_CLOSE`: đóng cửa sổ hoặc app bị kill cứng ⇒ sidecar chết. Chưa tắt êm
  (không chạy `lifespan`; `instance.json` và WAL của SQLite còn lại, lần mở sau vẫn chạy bình thường).
- Chỉ một cửa sổ: mở lần hai thì focus cửa sổ đang chạy.
