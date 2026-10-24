# Holocron

![Python](https://img.shields.io/badge/Python-%E2%89%A53.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white)
![pytest](https://img.shields.io/badge/tested%20with-pytest-0A9EDC?logo=pytest&logoColor=white)
![Ruff](https://img.shields.io/badge/lint-ruff-D7FF64?logo=ruff&logoColor=black)
![mypy](https://img.shields.io/badge/types-mypy%20strict-2A6DB2)

## Giới thiệu

**Bối cảnh:** dự án tham gia **Nhà Cộng Đồng Hackathon 2026**.

**Ý tưởng cốt lõi:** nhập tài liệu của một môn học bất kỳ (slide, giáo trình, đề thi cũ) và sinh câu hỏi song sinh có kiểm chứng. LLM chỉ đọc tài liệu và viết template, **không bao giờ tính nhẩm**: mọi con số trong câu hỏi do code chạy trong sandbox tính ra. Hệ thống gồm ba luồng:

| Luồng | Vai trò |
|---|---|
| **Nhập tài liệu môn học → Knowledge Vault** | Phân tách, phân loại và liên kết tài liệu thành kho tri thức có cấu trúc; mọi khẳng định kiến thức hiển thị cho người dùng kèm trích dẫn nguyên văn từ tài liệu nguồn. |
| **Nhập đề → Ingestion Gate** | Đọc đề từ ảnh/PDF, bắt lỗi đọc sai bảng/LaTeX; con người xác nhận dữ liệu trước khi đề được đưa vào tính toán. |
| **Sinh câu hỏi → Deterministic Core** | LLM viết template, template chạy trong sandbox để tính đáp án, phương án nhiễu và lời giải; có retry budget và fallback về template đã kiểm chứng. |

Đặc tả và contract: [`SPECIFICATION.md`](SPECIFICATION.md). Quy ước phát triển: [`.claude/CLAUDE.md`](.claude/CLAUDE.md).

## Trạng thái hiện tại

- **Bộ chứng minh đầu tiên là môn Hệ điều hành:** có hai template đã kiểm chứng là Round Robin và SJF không ưu tiên (thời gian chờ trung bình). Nhập đề qua upload hiện chỉ hỗ trợ dạng Round Robin; SJF mới dùng được ở bước sinh câu hỏi.
- **Đọc đề vẫn là giả lập:** extractor là `MockDualPassExtractor`, trả cùng một đề Round Robin mẫu cho mọi file hợp lệ; chưa có adapter VLM thật.
- **Sinh câu hỏi:** đã có `POST /v1/blueprints/{id}/twins` và trang xem twin; chưa có `POST /v1/blueprints` nên chưa tạo được blueprint từ đề đã duyệt qua API. Chưa có adapter LLM viết template nên pipeline dùng template đã kiểm chứng làm fallback.
- **Chưa có Knowledge Vault đa môn**, chưa có ôn lý thuyết hay chấm câu trả lời.
- **Phạm vi MVP là đa môn** (`SPECIFICATION.md` §1, v0.4): desktop app Tauri cho sinh viên tự ôn luyện, ba môn demo. Thiết kế ở ADR-005/006/007/008 (*Chấp nhận*); contract hiện hành trong `SPECIFICATION.md` §5–§12 vẫn có hiệu lực cho tới khi được cập nhật theo các ADR này.

## Cấu trúc dự án

```text
Holocron/
├── .claude/CLAUDE.md          # Quy ước phát triển
├── SPECIFICATION.md           # Đặc tả kỹ thuật + API/Data contracts
├── CHANGELOG.md
├── docs/
│   ├── adr/                   # Architecture Decision Records
│   └── releases/              # Quy trình phát hành
├── scripts/                   # Công cụ gốc repo (kiểm version, commit message, phát hành)
└── holocron/
    ├── frontend/              # Next.js 15 (App Router)
    │   └── src/
    │       ├── app/           # Trang
    │       ├── components/    # Component giao diện
    │       ├── lib/           # Client API và logic thuần
    │       └── types/         # Kiểu TypeScript khớp 1:1 với Pydantic
    └── backend/
        ├── holocron_core/
        │   ├── domain/            # Model Pydantic v2
        │   ├── sandbox/           # ASTGuard + chạy template trong tiến trình con
        │   ├── infrastructure/    # Cấu hình, kho template, template chạy trong sandbox
        │   ├── services/          # Extraction, Ingestion Gate, pipeline, sinh batch
        │   └── routers/           # FastAPI endpoints
        ├── scripts/               # Walkthrough end-to-end
        ├── examples/              # JSON mẫu tất định do walkthrough sinh
        └── tests/
```

## Cài đặt

**Yêu cầu:** Python **>= 3.12**; Node.js **>= 20** cho frontend.

```bash
git clone https://github.com/Nha-Cong-Dong/Holocron.git
cd Holocron/holocron/backend
pip install -e ".[dev]"
```

Lệnh trên cài cả dependencies chạy (`pydantic`, `fastapi`, `uvicorn`, `python-multipart`) và công cụ dev (`pytest`, `pytest-cov`, `httpx`, `ruff`, `mypy`).

```bash
cd ../frontend
npm install
```

Trên Windows, gọi công cụ Python qua `python -m` (ví dụ `python -m pytest`) và đặt `$env:PYTHONIOENCODING = "utf-8"` để console in được tiếng Việt.

Cài hook kiểm tra cục bộ một lần, chạy ở gốc repo (cài cả hook `commit-msg` và `pre-push`):

```bash
python -m pip install pre-commit
python -m pre_commit install
```

## Sử dụng

Các lệnh backend chạy trong `holocron/backend/`; lệnh frontend chạy trong `holocron/frontend/`.

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
uvicorn holocron_core.routers.api:app --reload --host 127.0.0.1 --port 8000
```

Tài liệu API tương tác tại <http://127.0.0.1:8000/docs>.

### Chạy frontend

```bash
npm run dev          # http://localhost:3000 — gọi backend qua /api/holocron/* (biến HOLOCRON_API_URL)
npm run typecheck
npm run build
```

Khi chưa đặt các biến `HOLOCRON_VLM_*`, hệ thống chạy chế độ demo với dữ liệu mẫu.

### Walkthrough end-to-end

```bash
python -m scripts.run_walkthrough
```

Sinh lại `examples/*.json` (Blueprint → template → sandbox → TwinQuestion).
