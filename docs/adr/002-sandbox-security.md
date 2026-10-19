# ADR 002: Sandbox tiến trình con thay vì WASM; Job Object trên Windows

- Trạng thái: Chấp nhận (lớp L4 hoãn tới Tuần 4)
- Ngày: 2026-10-19

## Bối cảnh

Template do LLM viết là mã Python tuỳ ý và phải chạy để sinh số liệu (I1). Mã này không tin cậy: prompt injection trong ảnh có thể khiến LLM sinh mã độc, và mã sai có thể lặp vô hạn hoặc ngốn bộ nhớ. Sản phẩm chạy local-first trên Windows và Linux, nên cơ chế cô lập phải chạy được trên cả hai.

## Quyết định

Chạy template trong **một tiến trình Python con cho mỗi lần chạy**, với phòng thủ nhiều lớp (SPECIFICATION §9.1):

- **L1 ASTGuard** (`sandbox/sandbox.py`): whitelist import (`random, math, fractions, itertools, functools, collections, statistics`); cấm builtins nguy hiểm, thuộc tính `_*` và tiền tố frame, `.format/.format_map/.mro`; cấm `class/try/with/raise/global/delete/async`; số mũ hằng 0–64; literal ≤ 2 000 ký tự; ≤ 6 000 node; mã ≤ 20 000 byte.
- **L2 Runtime** (`sandbox/_sandbox_child.py`): tiến trình `python -S -s -P`; builtins rút gọn; `__import__` chỉ trả proxy module đã nạp sẵn, không lộ module con.
- **L3 Giới hạn OS:** Unix dùng `RLIMIT_AS` 256 MiB, `RLIMIT_CPU` 2 s, `RLIMIT_FSIZE` 0, `RLIMIT_CORE` 0; **Windows dùng Job Object** (giới hạn bộ nhớ, CPU user-time, `KILL_ON_JOB_CLOSE`). Cả hai: wall-clock 2 s ở tiến trình cha, môi trường tối thiểu, thư mục làm việc tạm. `resource` chỉ được dùng sau kiểm tra `sys.platform != "win32"`.
- **Hợp đồng đầu ra kiểm cả kiểu:** `value` là số (không phải bool), các trường chuỗi là `str`; sai thì `contract_error`.
- **L4 (Tuần 4, đóng gói desktop):** nsjail/seccomp trên Linux, AppContainer trên Windows, không mạng. Chưa triển khai.

Dòng `exec` duy nhất trong `_sandbox_child.py` mang `# noqa: S102` đã được người dùng duyệt (ngoại lệ trong `.claude/CLAUDE.md` §7). Nới whitelist hoặc giới hạn OS chỉ được làm khi đã có test bảo mật tương ứng trong `tests/test_sandbox_security.py`.

## Hệ quả

- Chạy được trên Windows và Linux với cùng một đường mã; kết quả có trạng thái rõ ràng (`ok | ast_rejected | assertion_failed | runtime_error | contract_error | timeout | memory_exceeded | crashed`).
- Chi phí spawn mỗi lần chạy (~40–60 ms Linux, ~120–150 ms Windows). Đây là nợ BE-004; hướng giảm là pool tiến trình dựng sẵn và chạy song song các seed.
- Cô lập chưa tuyệt đối: L1–L3 không chặn được mọi kiểu tấn công ở mức nhân hệ điều hành; phần còn lại thuộc L4. Vì vậy ASTGuard là lớp chính và template `precomputed_verified` luôn là phương án dự phòng.
- File `_sandbox_child.py` và template RR chạy trong tiến trình con nên không đo được coverage; chúng được loại khỏi phép đo (`pyproject.toml`) và kiểm bằng hành vi ở `test_sandbox_security.py`, `test_sandbox_limits.py`, `test_rr_template.py`.

## Phương án đã loại

- **WASM (ví dụ Pyodide):** cô lập mạnh nhưng nặng khi đóng gói và khởi động, và làm phức tạp việc đảm bảo kết quả tất định và đồng nhất giữa các nền tảng cho một MVP chạy local. Chưa loại hẳn cho các phiên bản sau.
- **`exec` trực tiếp trong tiến trình backend sau ASTGuard:** không có giới hạn tài nguyên; một vòng lặp vô hạn hay memory bomb làm sập cả API.
- **Container Docker cho mỗi lần chạy:** thêm phụ thuộc nặng cho người dùng cuối và chậm hơn nhiều so với tiến trình con.
- **Chỉ dùng RLIMIT:** `resource` không có trên Windows, nên cần Job Object để có giới hạn tương đương.
