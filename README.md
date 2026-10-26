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

**Người dùng:** sinh viên tự ôn bằng tài liệu của chính mình. App chạy trên máy cá nhân; MVP-1 là bản Windows zip portable chạy offline (Python embeddable, backend phục vụ giao diện tĩnh); app desktop Tauri làm sau MVP-1 (3–6/11). Người dùng đầu tiên là chính người làm dự án, một sinh viên năm 3.

## Điểm khác biệt

Nhãn: **ĐÃ CÓ** là chạy được trên nhánh `develop` hôm nay. **SẼ LÀM** là nằm trong phạm vi MVP và đang làm. **HƯỚNG** là định hướng sau MVP, chưa có code.

| Điểm khác biệt | Trạng thái |
|---|---|
| Mọi câu hỏi và đáp án gắn với trích dẫn nguyên văn từ tài liệu của chính người dùng (bất biến I3) | **SẼ LÀM.** Bước kiểm "trích dẫn phải là chuỗi con của văn bản nguồn" đã có trong model. Knowledge Vault cung cấp trích dẫn thật thì đang làm |
| Chạy local, tài liệu không rời máy | **ĐÃ CÓ** với backend: lệnh chạy hướng dẫn chỉ nghe `127.0.0.1` (code chưa tự cưỡng chế; kiểm Host và token: **SẼ LÀM**, spec §13.1). Gói desktop Tauri: **SẼ LÀM**. Ngoại lệ: khi bật LLM, phần văn bản gửi đi rời máy (xem mục "Dữ liệu và LLM") |
| Xét duyệt trước khi tính: con người xác nhận dữ liệu đọc từ đề trước khi đề được dùng (bất biến I5) | **ĐÃ CÓ.** Ingestion Gate có màn sửa và duyệt từng item, chặn sinh câu khi còn lỗi BLOCKING; hiện dùng extractor giả lập. Hàng đợi xét duyệt theo trang cho PDF: **SẼ LÀM** |
| OCR tiếng Việt có dấu, chạy offline, đối chiếu chéo hai lượt đọc trên các con số, kèm màn xem lại theo trang | **SẼ LÀM** (ADR-008: Tesseract `vie` cùng RapidOCR so số) |
| Câu lý thuyết trắc nghiệm sinh tất định từ cặp khái niệm–trích dẫn đã xác nhận | **SẼ LÀM** (ADR-007) |
| Câu tự luận "Trình bày khái niệm X" được chấm hoàn toàn offline: báo "khớp x/y ý" cùng các ý còn thiếu, không cho điểm tuyệt đối | **SẼ LÀM** 30/10–2/11, sau MVP-1 (spec §1.5, §5.14) |
| Câu tính toán do code chạy trong sandbox tính, LLM không tính nhẩm (bất biến I1) | **ĐÃ CÓ** cơ chế sandbox, template Round Robin và SJF dùng làm fixture. Template cho môn demo (ARM, XSTK): **SẼ LÀM** ở luồng song song, không chặn MVP cơ bản; chờ quyết ADR-005 bước 3 và D6 (spec §16), có thể lùi sau MVP nếu trễ |
| Kiến trúc không gắn với một môn: dạng bài nằm trong template và dữ liệu, không nằm trong code | **SẼ LÀM.** Đây là nguyên tắc thiết kế; vẫn còn nợ gắn cứng CPU Scheduling (ARCH-001, ADR-005) |
| Mọi môn, nhiều định dạng tài liệu (DOCX, PPTX, ảnh), chấm tự luận bằng LLM (AnswerJudge), model chạy local | **HƯỚNG** |

## Dữ liệu và LLM

- LLM là **tuỳ chọn** (**SẼ LÀM**, spec §11.4). Khi các phần nhập PDF, Vault, sinh câu lý thuyết và chấm tự luận offline có code, chúng chạy được cả khi không cấu hình LLM. `GET /v1/runtime` báo từng khả năng có dùng được hay không. Phần báo theo từng khả năng đang làm.
- Khi bật LLM, văn bản tài liệu cần xử lý được gửi tới endpoint cấu hình ở `HOLOCRON_LLM_BASE_URL`. **Trong hackathon, endpoint này là gateway của ban tổ chức**, nên dữ liệu đi qua máy chủ của ban tổ chức. Endpoint đổi được sang nhà cung cấp khác hoặc model chạy local, miễn là có giao thức kiểu OpenAI.

## Trạng thái hiện tại

- **Ba môn demo của MVP** là Kỹ thuật vi xử lý (ARM Assembly), Xác suất thống kê và Triết học Mác–Lênin (`SPECIFICATION.md` §1.5); template của môn Vi xử lý chưa có. Code hiện có hai template đã kiểm chứng là Round Robin và SJF không ưu tiên (thời gian chờ trung bình); chúng chỉ còn là fixture kiểm thử và walkthrough. Nhập đề qua upload hiện chỉ hỗ trợ dạng Round Robin; SJF mới dùng được ở bước sinh câu hỏi.
- **Đọc đề vẫn là giả lập:** extractor là `MockDualPassExtractor`, trả cùng một đề Round Robin mẫu cho mọi file hợp lệ; chưa có adapter VLM thật.
- **Sinh câu hỏi:** đã có `POST /v1/blueprints/{id}/twins` và trang xem twin; chưa có `POST /v1/blueprints` nên chưa tạo được blueprint từ đề đã duyệt qua API. Chưa có adapter LLM viết template nên pipeline dùng template đã kiểm chứng làm fallback.
- **Chưa có Knowledge Vault đa môn**, chưa có ôn lý thuyết hay chấm câu trả lời.
- **Phạm vi:** spec v0.7 là contract mục tiêu đa môn; mục tiêu trước mắt là **MVP-1** (`SPECIFICATION.md` §1.5): zip portable Windows, offline, không cần API key, luồng PDF → xem lại → Vault → cặp khái niệm → câu MCQ4 lý thuyết → làm bài → đáp án kèm trích dẫn và số trang → lịch sử + ôn câu sai. Thiết kế ở ADR-005/006/007/008 (*Chấp nhận*). Contract cho bộ nhập PDF, Vault, blueprint, SQLite, essay và chế độ khả năng nằm ở `SPECIFICATION.md` §5–§6; phần lớn chưa có code.
- **Lộ trình** (`SPECIFICATION.md` §16, chốt 2026-10-26): MVP-1 26–29/10; tự luận offline và giải thích đáp án bằng LLM tuỳ chọn 30/10–2/11; Tauri và sửa lỗi 3–6/11; đóng băng code 7–8/11; hạn nộp 8/11.

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

Khi chưa đặt các biến `HOLOCRON_VLM_*`, bản hiện tại đọc đề bằng extractor giả lập với dữ liệu mẫu. Theo spec mới, LLM và VLM là tuỳ chọn và `GET /v1/runtime` sẽ báo từng khả năng (§5.12, §11.4); phần này chưa có code. Danh sách biến môi trường xem ở [`.env.example`](.env.example).

### Walkthrough end-to-end

```bash
python -m scripts.run_walkthrough
```

Sinh lại `examples/*.json` (Blueprint → template → sandbox → TwinQuestion).
