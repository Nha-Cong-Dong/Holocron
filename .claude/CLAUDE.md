# Project Guidelines for Claude Code — Holocron

Đọc toàn bộ file này trước khi sửa code. Nguồn chân lý về hành vi sản phẩm là `SPECIFICATION.md` (v0.3, đã chốt Tuần 2); khi code và spec lệch nhau, dừng lại và hỏi người dùng thay vì tự chọn.

## 1. Tổng quan

- **Sản phẩm:** Holocron — Nhà Cộng Đồng Hackathon 2026. Sinh Twin Questions cho bài tính toán môn Hệ điều hành (Golden Path: CPU Scheduling — Round Robin, SJF) từ ảnh đề thi cũ.
- **Hai tầng:** **Deterministic Core** (mọi con số do code chạy trong sandbox tính) và **Knowledge Vault UI + Ingestion Gate** (con người xác nhận dữ liệu đọc từ ảnh trước khi tính).
- **Năm bất biến I1–I5** ở `SPECIFICATION.md` §0 PHẢI luôn đúng. Thay đổi nào có thể chạm tới chúng PHẢI kèm test chứng minh chúng vẫn đúng.

### Cấu trúc repo

```text
Holocron/
├── .claude/CLAUDE.md                 # file này
├── SPECIFICATION.md                  # spec + API/data contracts (chốt Tuần 2)
├── docs/adr/                         # Architecture Decision Records
└── holocron/
    ├── backend/                      # FastAPI + Pydantic v2, Python >= 3.12
    │   ├── pyproject.toml
    │   ├── holocron_core/
    │   │   ├── domain/schemas.py             # model domain (ProcessItem, CPUSchedulingProblem, TwinQuestion…)
    │   │   ├── services/extraction.py        # DocumentExtractor (VLM, không tin cậy) + MockDualPassExtractor
    │   │   ├── services/ingestion_gate.py    # 5 tín hiệu, GateReport, apply_patches, self-solve
    │   │   ├── services/pipeline.py          # template → sandbox → TwinQuestion, retry & fallback
    │   │   ├── sandbox/sandbox.py            # ASTGuard + chạy tiến trình con
    │   │   ├── sandbox/_sandbox_child.py     # builtins rút gọn, RLIMIT (Unix) / Job Object (Windows)
    │   │   ├── infrastructure/template_library.py, templates/   # template đã kiểm chứng (chạy TRONG sandbox)
    │   │   └── routers/api.py                # FastAPI endpoints, IngestionStore qua Depends
    │   ├── scripts/run_walkthrough.py        # sinh examples/*.json tất định
    │   ├── examples/
    │   └── tests/
    └── frontend/                     # Next.js 15 App Router, TypeScript strict
        ├── next.config.ts                    # rewrite /api/holocron/* → FastAPI
        └── src/
            ├── app/                          # page.tsx (upload), ingestion/[itemId]/page.tsx
            ├── components/ingestion/         # UploadForm, TableAuditor (+ .module.css)
            ├── lib/api.ts, lib/gate.ts       # client API, helper Gate thuần
            └── types/holocron.ts             # khớp 1:1 với Pydantic
```

## 2. Ranh giới kiến trúc

### 2.1 Deterministic Core vs LLM Gate

| | LLM Gate (KHÔNG tin cậy) | Deterministic Core (tin cậy) |
|---|---|---|
| Thành phần | `DocumentExtractor` (VLM), `TemplateAuthor` (LLM viết template) | `ingestion_gate`, `pipeline`, `sandbox`, `template_library`, `domain` |
| Được làm | Đọc ảnh thành bảng/stem; viết mã template; gắn nhãn topic | Mọi phép tính, Gantt, đáp án, phương án nhiễu, trộn phương án |
| Đầu ra đi đâu | Luôn vào một cổng kiểm: bảng → Ingestion Gate; mã → ASTGuard + sandbox + validator | Trực tiếp tới người dùng |

Quy tắc cứng:
- KHÔNG ĐƯỢC để bất kỳ con số hiển thị nào (đáp án, phương án, WT/TAT, Gantt) được tính ngoài sandbox hoặc do LLM viết (I1). Kể cả "tính hộ cho nhanh" trong router, service hay frontend.
- KHÔNG ĐƯỢC cho dữ liệu VLM vào sandbox khi Gate còn issue BLOCKING (I5).
- Frontend chỉ hiển thị số do backend trả; KHÔNG ĐƯỢC tự tính lại.

### 2.2 Phân tầng package (backend)

| Tầng | Được import | KHÔNG ĐƯỢC import |
|---|---|---|
| `domain/` | thư viện chuẩn, `pydantic` | mọi tầng khác |
| `sandbox/` | thư viện chuẩn, `pydantic` | `services/`, `routers/`, mạng |
| `infrastructure/` | `domain/` | `services/`, `routers/` |
| `services/` | `domain/`, `sandbox/`, `infrastructure/` | `routers/`, `fastapi` |
| `routers/` | tất cả tầng dưới | — |

- Phụ thuộc bên ngoài (VLM, LLM, storage) PHẢI đi qua `Protocol` và được cấp bằng `Depends` hoặc tham số; test thay bằng `app.dependency_overrides` hoặc stub, KHÔNG ĐƯỢC gọi mạng trong test.
- Không thêm global state mutable mới. Store hiện tại là `IngestionStore` cấp qua `Depends(get_store)`; mở rộng bằng cách thêm method/repository, không thêm dict module-level.
- Import trong `holocron_core` dùng dạng tương đối (`from ..domain.schemas import …`); `tests/` và `scripts/` dùng tuyệt đối (`from holocron_core.domain.schemas import …`).

### 2.3 Contract
- Đổi model Pydantic ở `domain/`, `services/` hoặc `routers/` mà xuất hiện trong response ⇒ PHẢI cập nhật `holocron/frontend/src/types/holocron.ts` và `SPECIFICATION.md` §5/§6 trong cùng PR.
- Template trong `infrastructure/templates/` chạy TRONG sandbox: chỉ import whitelist (`random, math, fractions, itertools, functools, collections, statistics`), annotation chỉ dùng kiểu builtin (không `typing`, không tên `object`), không `class/try/with/raise`.

## 3. Lệnh thường dùng

### 3.1 Backend — chạy trong `holocron/backend/`

**Windows (PowerShell):**

```powershell
python -m pip install -e ".[dev]"
$env:PYTHONIOENCODING = "utf-8"          # console mặc định cp1252 không in được tiếng Việt
python -m pytest
python -m pytest tests/test_upload.py -x
python -m ruff check .
python -m ruff format --check .           # tự sửa: python -m ruff format .
python -m mypy --strict holocron_core
python -m uvicorn holocron_core.routers.api:app --reload --host 127.0.0.1 --port 8000
python -m scripts.run_walkthrough
```

**Linux / macOS (bash):**

```bash
python3 -m pip install -e ".[dev]"
pytest
pytest tests/test_upload.py -x
ruff check . && ruff format --check .
mypy --strict holocron_core
uvicorn holocron_core.routers.api:app --reload --host 127.0.0.1 --port 8000
python3 -m scripts.run_walkthrough
```

Ghi chú nền tảng:
- Trên Windows luôn gọi qua `python -m …` (script của pip thường không nằm trong PATH).
- `resource`/`RLIMIT_*` chỉ có trên Unix. Mọi chỗ dùng PHẢI nằm sau `sys.platform != "win32"`, KHÔNG ĐƯỢC import `resource` ở top-level vô điều kiện; Windows dùng Job Object + wall-clock timeout của tiến trình cha.
- Subprocess PHẢI đọc/ghi UTF-8 tường minh (`encoding="utf-8"`) và tự cấp biến môi trường tối thiểu (`SYSTEMROOT` trên Windows). Script in tiếng Việt ra console PHẢI `sys.stdout.reconfigure(encoding="utf-8")`.
- Test sandbox trên Windows chậm hơn Linux (~2–3 lần) vì chi phí spawn tiến trình; đó không phải lỗi.

### 3.2 Frontend — chạy trong `holocron/frontend/` (Node ≥ 20)

```bash
npm install
npm run dev          # http://localhost:3000, cần backend chạy ở 127.0.0.1:8000
npm run typecheck    # tsc --noEmit
npm run build        # next build — kiểm tra type + build production
```

Đổi địa chỉ backend bằng biến `HOLOCRON_API_URL` (PowerShell: `$env:HOLOCRON_API_URL = "http://127.0.0.1:8001"`).

## 4. Code conventions

### 4.1 Python
- Annotation đầy đủ cho mọi tham số và giá trị trả về; cú pháp hiện đại (`list[str]`, `X | None`); `from __future__ import annotations`. Generic PHẢI có tham số kiểu (`dict[str, Any]`, `Counter[str]`).
- Dữ liệu qua ranh giới (API, giữa các bước pipeline, sandbox) là model Pydantic, không phải `dict` trần — ngoại lệ duy nhất là `params`/`solution` trao đổi với template trong sandbox.
- Hàm ưu tiên thuần; seed ngẫu nhiên truyền tường minh.
- Bảo mật sandbox: KHÔNG ĐƯỢC nới whitelist import/builtins hoặc giới hạn OS khi chưa có test bảo mật tương ứng trong `tests/test_sandbox_security.py`.

### 4.2 TypeScript / React
- `strict` + `noUncheckedIndexedAccess` bật; KHÔNG ĐƯỢC dùng `any`, `// @ts-ignore`, `// @ts-expect-error`, `as unknown as`, `eslint-disable`. Ép kiểu `as T` chỉ được phép ở đúng một chỗ: parse JSON response trong `lib/api.ts`.
- Component có state/tương tác đánh dấu `"use client"`; logic thuần (gom issue, điều kiện approve) đặt ở `lib/` để dùng lại và dễ test.
- Mọi lời gọi backend đi qua `lib/api.ts`; component không gọi `fetch` trực tiếp.
- Style bằng CSS Modules + biến màu ở `app/globals.css` (có dark mode); tôn trọng `prefers-reduced-motion`.

### 4.3 Ngôn ngữ
- Docs, docstring, comment và chuỗi hiển thị cho người dùng viết bằng **Tiếng Việt**. Tên định danh (biến, hàm, class, field) BẮT BUỘC **Tiếng Anh**.
- Ruff `RUF001`–`RUF003` CÓ THỂ được tắt nếu chúng báo lỗi ký tự Tiếng Việt (hiện ruff chưa báo nên chưa cấu hình).

## 5. Kiểm tra trước khi xong việc

**Cấp 1 — trong lúc sửa từng file (nhanh):**
- Python: `ruff check <file>` + `pytest` cho file test liên quan trực tiếp (sửa `sandbox.py` ⇒ `tests/test_sandbox_security.py`; sửa `routers/api.py` ⇒ `tests/test_upload.py`, `tests/test_api_item_state.py`, `tests/test_schemas_and_gate.py`).
- TypeScript: `npm run typecheck`.
- Đọc nội dung hiện tại trước khi ghi đè file có sẵn; không xoá nội dung người dùng khi chưa được yêu cầu.

**Cấp 2 — khi hoàn thành task (bắt buộc, báo kết quả thật kèm output khi có lỗi):**
1. `pytest` (toàn bộ)
2. `ruff check .` và `ruff format --check .`
3. `mypy --strict holocron_core`
4. Nếu chạm `frontend/`: `npm run typecheck` và `npm run build`
5. Nếu đổi đầu ra pipeline/schema/template: `python -m scripts.run_walkthrough` rồi xem `git diff examples/` — chỉ được đổi khi thay đổi là chủ đích (walkthrough đã đóng băng id, thời gian, `sandbox_duration_ms`).

## 6. Git

- **Conventional Commits:** `type(scope): mô tả ngắn` — `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `perf`, `ci`, `style`. Scope gợi ý: `core`, `api`, `sandbox`, `pipeline`, `gate`, `frontend`, `spec`, `config`.
- **Gitflow:**
  - Tách nhánh từ `develop`: `feat/<tên>` cho tính năng, `fix/<tên>` cho sửa lỗi. Không ghi cứng tên nhánh trong tài liệu hay script.
  - Mọi Pull Request nhắm vào `develop`. Chỉ merge `develop` → `main` khi chốt milestone.
  - KHÔNG ĐƯỢC push thẳng hay sửa lịch sử `develop`/`main`. Không tự merge PR.
  - Chỉ commit / push / tạo PR khi người dùng yêu cầu.
- **Không bulk add:** KHÔNG ĐƯỢC `git add .`, `git add -A` không chọn lọc, `git commit -a`. Stage theo file/hunk và chia commit theo đơn vị logic. Kiểm tra `git status` trước khi commit để chắc không có file đã stage sẵn (ví dụ do `git mv`) lẫn vào commit khác.
- Không commit `__pycache__/`, `*.egg-info/`, `node_modules/`, `.next/`, `*.tsbuildinfo`, `next-env.d.ts`, file môi trường hay API key.

## 7. Zero-Shortcut Policy

Làm xanh kết quả bằng cách che lỗi là không được chấp nhận. TUYỆT ĐỐI KHÔNG:

- Dùng `# type: ignore`, `# noqa`, `cast(Any, …)` trong Python; `any`, `@ts-ignore`, `@ts-expect-error`, `as unknown as`, `eslint-disable` trong TypeScript.
- Hạ mức kiểm tra trong `pyproject.toml` hay `tsconfig.json`: thêm `ignore`/`per-file-ignores`/`exclude` cho ruff, `ignore_errors`/`disable_error_code`/`overrides` nới lỏng cho mypy, tắt `strict` của TypeScript.
- Sửa, xoá, `skip`/`xfail` test sẵn có chỉ để kết quả xanh. Test fail nghĩa là code sai, hoặc spec đổi — khi đó nêu rõ và được người dùng đồng ý trước. Chỉ được thêm test mới, hoặc đổi định dạng thuần tuý (do `ruff format`) mà không đổi AST.
- Viết code tắt: placeholder, hàm rỗng, `pass`/`...` thay cho logic, `# TODO` mới (TODO hiện có duy nhất là optimistic locking Tuần 4 trong `routers/api.py`, đã được duyệt).

**Ngoại lệ đã được người dùng duyệt (chỉ những mục này):**
- Đúng một `# noqa: S102` tại dòng `exec` trong `sandbox/_sandbox_child.py` (chủ đích kiến trúc của sandbox).
- Tắt `RUF001`–`RUF003` nếu cần cho ký tự Tiếng Việt.

Mọi ngoại lệ khác cần người dùng đồng ý riêng. Gặp lỗi lint/type không sửa được mà không vi phạm các quy tắc trên: **dừng lại, báo rõ lỗi còn lại và lý do**, để người dùng quyết định.
