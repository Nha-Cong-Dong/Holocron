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
| `holocron_core/sandbox.py`, `_sandbox_child.py` | Sandbox 3 lớp: ASTGuard, runtime tiến trình con, giới hạn OS (RLIMIT / Job Object, timeout) |
| `holocron_core/template_library.py`, `templates/` | Template pre-computed đã kiểm chứng (fallback khi hết retry); là mã chạy **trong sandbox** nên chỉ được dùng kiểu builtin + `fractions`/`random`… trong whitelist (không `typing`, không tên `object`) |
| `holocron_core/api.py` | FastAPI `/v1/ingestion/items…` (hiện là in-memory store, Tuần 4 thay bằng SQL repository) |
| `scripts/run_walkthrough.py` | Walkthrough end-to-end, sinh JSON mẫu vào `examples/` |
| `tests/` | pytest: sandbox security, schemas & gate, RR template |

## 2. Lệnh thường dùng

Chạy từ `holocron/backend/` (Python >= 3.12). Trên Windows dùng `python -m ...` nếu script không nằm trong PATH.

```bash
# Cài đặt (editable + dev deps: pytest, httpx, ruff, mypy)
pip install -e ".[dev]"

# Test
pytest                                   # toàn bộ
pytest tests/test_sandbox_security.py    # một file
pytest -k "rr" -x                        # lọc theo tên, dừng ở lỗi đầu tiên

# Chạy server (FastAPI, docs tại http://127.0.0.1:8000/docs)
uvicorn holocron_core.api:app --reload

# Walkthrough end-to-end (sinh lại examples/*.json)
python -m scripts.run_walkthrough
```

**Lint / format / type-check là tiêu chuẩn BẮT BUỘC** — phải xanh trước khi hoàn thành task:

```bash
ruff check .
ruff format --check .      # tự sửa format: ruff format .
mypy --strict holocron_core
```

Cấu hình ruff nằm trong `pyproject.toml` (`line-length = 120`).

## 3. Code conventions

- **Type hinting nghiêm ngặt:** mọi hàm/method có annotation đầy đủ cho tham số và kiểu trả về; dùng cú pháp hiện đại (`list[str]`, `X | None`), `from __future__ import annotations`. Generic phải có tham số kiểu (`dict[str, Any]`, `Counter[str]`, không để `dict`/`Counter` trần). Dữ liệu qua ranh giới (API, DB, giữa các bước pipeline) phải là model Pydantic, không phải `dict` trần.
- **Clean architecture:** tách rõ schema/domain → logic nghiệp vụ (`pipeline`, `ingestion_gate`, `sandbox`) → tầng giao tiếp (`api`). Domain không import từ API; route chỉ validate, gọi logic, trả kết quả. Phụ thuộc bên ngoài (LLM, VLM, storage) đi qua interface/tham số truyền vào để dễ thay và dễ test.
- **Không dùng global state bừa bãi:** tránh biến module mutable. Store in-memory hiện có trong `api.py` là tạm thời — khi mở rộng, đưa vào repository/dependency (`Depends`) thay vì thêm global mới. Hàm ưu tiên thuần (pure), seed ngẫu nhiên truyền tường minh để tái lập được.
- **Bảo mật sandbox:** không nới whitelist import/builtins hoặc giới hạn OS nếu chưa có test bảo mật tương ứng trong `tests/test_sandbox_security.py`.
- **Cross-platform (dev có dùng Windows, `win32`):** module `resource` và mọi `RLIMIT_*` là Unix-only. Mọi giới hạn OS dùng chúng bắt buộc nằm sau kiểm tra `sys.platform != "win32"` (không import `resource` ở top-level vô điều kiện) và phải có cơ chế thay thế/timeout an toàn trên Windows (hiện tại: Job Object + wall-clock timeout của tiến trình cha). Tuyệt đối không để pytest crash trên Windows; subprocess phải đọc/ghi UTF-8 tường minh và tự cấp biến môi trường tối thiểu mà Windows cần (`SYSTEMROOT`).
- **Phong cách:** theo code xung quanh (docstring/comment tiếng Việt, tên định danh tiếng Anh); không thêm comment thừa.

## 4. Quy tắc Git & hoàn thành task

- **Conventional Commits:** `type(scope): mô tả ngắn` — type: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `perf`, `ci`. Ví dụ: `feat(core): implement ingestion pipeline`.
- **Mô hình nhánh (Gitflow):**
  - Không ghi cứng tên nhánh làm việc. Luôn tách nhánh từ `develop`: `feat/<tên>` cho tính năng, `fix/<tên>` cho sửa lỗi.
  - Mọi Pull Request đều nhắm vào nhánh đích là `develop`. Chỉ merge `develop` → `main` khi chốt milestone.
  - Chỉ commit/push/tạo PR khi được yêu cầu.
- **Không bulk add:** tuyệt đối không `git add .` / `git add -A` không chọn lọc. Stage từng file hoặc từng hunk (`git add <file>`, `git add -p`) và chia commit nhỏ theo đúng đơn vị logic (mỗi commit một thay đổi có nghĩa, tự nó build/test được). Không commit `__pycache__/`, `*.egg-info/`, file môi trường/bí mật.
- **Luôn chạy test trước khi hoàn thành task** (xem mục 5 — checklist hoàn thành). Chỉ báo "xong" khi tất cả xanh; nếu có gì fail hoặc bị bỏ qua, nêu rõ kèm output.

## 5. Tự động kiểm tra & ghi lưu file

Tách hai cấp để giữ tốc độ khi chạy auto mode:

**Cấp 1 — trong lúc đang sửa từng file** (nhanh):
- `ruff check <file>` và `pytest` chỉ cho file test liên quan trực tiếp (ví dụ sửa `sandbox.py` → `pytest tests/test_sandbox_security.py`).
- Sau đó ghi đè lưu file; không để thay đổi dở dang. Nếu kiểm tra fail thì sửa tiếp, không lưu trạng thái hỏng rồi bỏ đó.
- Trước khi ghi đè file có sẵn, đọc nội dung hiện tại và giữ lại phần còn hợp lệ; không xoá nội dung của người dùng khi chưa được yêu cầu.

**Cấp 2 — khi hoàn thành toàn bộ task** (đầy đủ, bắt buộc):
1. `pytest` (full)
2. `ruff check .` và `ruff format --check .`
3. `mypy --strict holocron_core`
4. Chỉ khi thay đổi liên quan tới đầu ra pipeline/schema/template: chạy `python -m scripts.run_walkthrough` để đồng bộ `examples/*.json`, rồi xem `git diff examples/` để chắc thay đổi là chủ đích.

## 6. QUY TẮC TRUNG THỰC (Zero-Shortcut Policy)

Làm xanh kết quả bằng cách che lỗi là không được chấp nhận. Cụ thể, TUYỆT ĐỐI KHÔNG:

- Dùng `# type: ignore`, `# noqa`, hoặc `cast(Any, ...)` để qua mặt mypy/ruff.
- Thêm rule ignore/exclude/override nới lỏng vào `pyproject.toml` (`[tool.ruff] ignore`/`per-file-ignores`/`exclude`, `[tool.mypy]` `ignore_errors`/`disable_error_code`/`overrides` hạ mức nghiêm ngặt…).
- Sửa, xoá, `skip`/`xfail` test sẵn có trong `tests/` chỉ để kết quả xanh. Test fail nghĩa là code sai (hoặc spec đổi — khi đó nêu rõ với người dùng và được họ đồng ý trước). Chỉ được thêm test mới, hoặc đổi định dạng thuần tuý (do `ruff format`) mà không đổi AST.

Gặp lỗi lint/type: sửa kiểu dữ liệu/logic thực chất. Nếu thực sự không thể sửa mà không vi phạm các quy tắc trên, **dừng lại, báo rõ lỗi còn lại và lý do**, để người dùng quyết định — đừng im lặng lách.
