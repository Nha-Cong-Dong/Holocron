# Holocron

![Python](https://img.shields.io/badge/Python-%E2%89%A53.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white)
![pytest](https://img.shields.io/badge/tested%20with-pytest-0A9EDC?logo=pytest&logoColor=white)
![Ruff](https://img.shields.io/badge/lint-ruff-D7FF64?logo=ruff&logoColor=black)
![mypy](https://img.shields.io/badge/types-mypy%20strict-2A6DB2)

## Giới thiệu

**Bối cảnh:** dự án tham gia **Nhà Cộng Đồng Hackathon 2026**.

**Ý tưởng cốt lõi:** Holocron tách thành hai tầng độc lập:

| Tầng | Vai trò |
|---|---|
| **Deterministic Core** | Mọi con số trong câu hỏi do code chạy trong sandbox tính ra — LLM chỉ viết template, **không bao giờ tính nhẩm**. Gồm Ingestion Gate (bắt lỗi VLM đọc sai bảng/LaTeX), Sandbox Runtime, Pipeline retry & fallback. |
| **Knowledge Vault UI** | Quản trị tài liệu đa môn, trích dẫn 1-click. *(chưa nằm trong repo này)* |

Repo hiện tập trung vào Deterministic Core cho Golden Path **Hệ Điều Hành — CPU Scheduling**. Đặc tả đầy đủ: [`SPECIFICATION.md`](SPECIFICATION.md).

## Cấu trúc dự án

```text
Holocron/
├── .claude/CLAUDE.md          # Quy ước cho Claude Code
├── SPECIFICATION.md           # Đặc tả kỹ thuật
└── holocron/backend/
    ├── pyproject.toml         # Dependencies & cấu hình pytest/ruff
    ├── holocron_core/
    │   ├── domain/
    │   │   └── schemas.py             # Domain model Pydantic v2 (Blueprint, TwinQuestion, ...)
    │   ├── services/
    │   │   ├── ingestion_gate.py      # Phát hiện VLM đọc sai bảng/LaTeX, vá ô, self-solve lại
    │   │   └── pipeline.py            # LLM template → Sandbox → TwinQuestion, retry budget & fallback
    │   ├── sandbox/
    │   │   ├── sandbox.py             # ASTGuard + chạy template trong tiến trình con
    │   │   └── _sandbox_child.py      # Tiến trình con: builtins rút gọn, RLIMIT (Unix) / Job Object (Windows)
    │   ├── infrastructure/
    │   │   ├── template_library.py    # Kho template pre-computed đã kiểm chứng (fallback)
    │   │   └── templates/             # Mã template chạy trong sandbox (vd. RR avg waiting time)
    │   └── routers/
    │       └── api.py                 # FastAPI: /v1/ingestion/items...
    ├── scripts/run_walkthrough.py # Demo end-to-end, sinh JSON mẫu vào examples/
    ├── examples/                  # Blueprint & TwinQuestion mẫu
    └── tests/                     # pytest: sandbox security, schemas & gate, RR template
```

## Cài đặt

**Yêu cầu:** Python **>= 3.12**.

```bash
git clone https://github.com/Nha-Cong-Dong/Holocron.git
cd Holocron/holocron/backend
pip install -e ".[dev]"
```

Lệnh trên cài cả dependencies chạy (`pydantic`, `fastapi`, `uvicorn`) và công cụ dev (`pytest`, `httpx`, `ruff`, `mypy`).

## Sử dụng

Chạy các lệnh dưới đây trong `holocron/backend/`.

### Chạy test

```bash
pytest                                   # toàn bộ
pytest tests/test_sandbox_security.py    # một file
pytest -k "rr" -x                        # lọc theo tên, dừng ở lỗi đầu tiên
```

### Lint, format & type-check

```bash
ruff check .
ruff format --check .      # tự sửa: ruff format .
mypy --strict holocron_core
```

### Chạy FastAPI server

```bash
uvicorn holocron_core.routers.api:app --reload
```

Tài liệu API tương tác tại <http://127.0.0.1:8000/docs>.

### Walkthrough end-to-end

```bash
python -m scripts.run_walkthrough
```

Sinh lại `examples/*.json` (Blueprint → template → sandbox → TwinQuestion).
