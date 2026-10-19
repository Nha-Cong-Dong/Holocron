# Project Guidelines for Claude Code

## 1. Tổng quan dự án

- **Tên sản phẩm:** Holocron
- **Bối cảnh:** Nhà Cộng Đồng Hackathon 2026
- **Ý tưởng cốt lõi:** hai tầng tách biệt — **Deterministic Core** (mọi con số do code chạy trong sandbox tính ra, LLM không bao giờ tính nhẩm) và **Knowledge Vault UI**. Repo hiện tập trung vào Core (Golden Path: Hệ Điều Hành — CPU Scheduling) + Ingestion Gate.
- **Tài liệu gốc:** `SPECIFICATION.md` (bất biến I1… ở mục 0 PHẢI luôn đúng — đọc trước khi đổi hành vi Core).

### Cấu trúc (Python backend, `holocron/backend/`)

| Thành phần | Vai trò |
|---|---|
| `holocron_core/schemas.py` | Domain model Pydantic v2; validator chặt ở biên hệ thống |
| `holocron_core/ingestion_gate.py` | Phát hiện VLM đọc sai bảng/LaTeX (5 tín hiệu), cho người dùng sửa |
| `holocron_core/pipeline.py` | Điều phối LLM template → Sandbox → TwinQuestion, retry budget & fallback |
| `holocron_core/sandbox.py`, `_sandbox_child.py` | Sandbox 3 lớp: ASTGuard, runtime tiến trình con, OS limits (RLIMIT, timeout) |
| `holocron_core/template_library.py`, `templates/` | Template pre-computed đã kiểm chứng (fallback khi hết retry) |
| `holocron_core/api.py` | FastAPI `/v1/ingestion/items…` (hiện là in-memory store, Tuần 4 thay bằng SQL repository) |
| `scripts/run_walkthrough.py` | Walkthrough end-to-end, sinh JSON mẫu vào `examples/` |
| `tests/` | pytest: sandbox security, schemas & gate, RR template |

## 2. Lệnh thường dùng

Chạy từ `holocron/backend/` (Python >= 3.12).

```bash
# Cài đặt (editable + dev deps)
pip install -e ".[dev]"

# Test
pytest                                   # toàn bộ
pytest tests/test_sandbox_security.py    # một file
pytest -k "rr" -x                        # lọc theo tên, dừng ở lỗi đầu tiên

# Chạy server (FastAPI, docs tại http://127.0.0.1:8000/docs)
# Lưu ý: `uvicorn` chưa nằm trong dependencies — cài thêm: pip install uvicorn
uvicorn holocron_core.api:app --reload

# Walkthrough end-to-end
python -m scripts.run_walkthrough
```

**Lint/format:** dự án chưa cấu hình linter. Chuẩn đề xuất là `ruff` (lint + format) và `mypy --strict`:

```bash
ruff check . && ruff format .
mypy --strict holocron_core
```

Nếu chưa cài thì `pip install ruff mypy`; khi thêm vào repo hãy khai báo trong `[project.optional-dependencies].dev` của `pyproject.toml`. Không tự ý bỏ qua lỗi — sửa hoặc báo lại.

## 3. Code conventions

- **Type hinting nghiêm ngặt:** mọi hàm/method có annotation đầy đủ cho tham số và kiểu trả về; dùng cú pháp hiện đại (`list[str]`, `X | None`), `from __future__ import annotations`. Không dùng `Any` trừ khi có lý do ghi chú. Dữ liệu qua ranh giới (API, DB, giữa các bước pipeline) phải là model Pydantic, không phải `dict` trần.
- **Clean architecture:** tách rõ schema/domain → logic nghiệp vụ (`pipeline`, `ingestion_gate`, `sandbox`) → tầng giao tiếp (`api`). Domain không import từ API; route chỉ validate, gọi logic, trả kết quả. Phụ thuộc bên ngoài (LLM, VLM, storage) đi qua interface/tham số truyền vào để dễ thay và dễ test.
- **Không dùng global state bừa bãi:** tránh biến module mutable. Store in-memory hiện có trong `api.py` là tạm thời — khi mở rộng, đưa vào repository/dependency (`Depends`) thay vì thêm global mới. Hàm ưu tiên thuần (pure), seed ngẫu nhiên truyền tường minh để tái lập được.
- **Bảo mật sandbox:** không nới whitelist import/builtins hoặc OS limits nếu chưa có test bảo mật tương ứng trong `tests/test_sandbox_security.py`.
- **Phong cách:** theo code xung quanh (docstring/comment tiếng Việt, tên định danh tiếng Anh); không thêm comment thừa.

## 4. Quy tắc Git & hoàn thành task

- **Conventional Commits:** `type(scope): mô tả ngắn` — type: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `perf`, `ci`. Ví dụ: `feat(core): implement ingestion pipeline`. Mỗi commit một thay đổi logic.
- **Luôn chạy test trước khi hoàn thành task:** chạy `pytest` (và lint/type-check nếu đã cài). Chỉ báo "xong" khi test xanh; nếu test fail hoặc bị bỏ qua, nêu rõ kèm output.
- Làm việc trên feature branch (hiện tại `feat/week2-develop`), PR vào `main`. Chỉ commit/push khi được yêu cầu.

## 5. Tự động kiểm tra & ghi lưu file

- Sau mỗi lần sửa file: tự kiểm tra ngay (đúng cú pháp, `pytest` liên quan, lint/type-check nếu có) rồi **ghi đè lưu lại** file đã sửa — không để thay đổi dở dang hoặc chỉ nằm trong đề xuất.
- Nếu kiểm tra thất bại: sửa và kiểm tra lại tới khi qua, không lưu trạng thái hỏng rồi bỏ đó.
- Trước khi ghi đè file có sẵn, đọc nội dung hiện tại và giữ lại phần còn hợp lệ; không xoá nội dung của người dùng khi chưa được yêu cầu.
- Test sinh ra file mẫu (`examples/*.json`) thì chạy lại walkthrough và kiểm tra diff trước khi lưu.
