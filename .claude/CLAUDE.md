# Project Guidelines for Claude Code — Holocron

Đọc toàn bộ file này trước khi sửa code. Cách cài đặt và chạy: `README.md`. Quy trình phát hành: `docs/releases/README.md`. Sổ nợ kỹ thuật: `docs/adr/004-engineering-discipline.md`. Phạm vi sản phẩm và contract nằm ở `SPECIFICATION.md` (nguồn chân lý về hành vi sản phẩm); thiết kế đa môn ở ADR-005 (pipeline câu hỏi không phụ thuộc topic), ADR-006 (Knowledge Vault đa môn), ADR-007 (ôn lý thuyết và chấm câu trả lời) và ADR-008 (bộ nhập PDF theo tầng).

**Giai đoạn chuyển tiếp:** ADR chưa có trong `docs/adr/` hoặc chưa ở trạng thái *Chấp nhận* thì chưa có hiệu lực. Khi `SPECIFICATION.md` còn mô tả phạm vi hoặc contract hẹp hơn file này (ví dụ chỉ một môn), spec thắng cho phần contract: code mới vẫn tuân thủ mọi quy tắc của file này (không rẽ nhánh theo môn, trích dẫn nguyên văn, điểm do code tính), nhưng thay đổi contract để mở rộng phạm vi phải chờ ADR tương ứng được chấp nhận và spec được cập nhật.

## 1. Tổng quan

- **Sản phẩm:** Holocron — Nhà Cộng Đồng Hackathon 2026. Nhập tài liệu của **môn học bất kỳ** (slide, giáo trình, đề thi cũ; ảnh hoặc tài liệu) → phân tách thành kho tri thức có cấu trúc (Knowledge Vault) → sinh câu hỏi song sinh có kiểm chứng. Dạng bài mới được học qua template do LLM viết và hệ thống kiểm chứng, không qua code viết tay cho từng môn. Phạm vi milestone hiện tại xem `SPECIFICATION.md` §1.
- **Không có "Golden Path" theo môn.** Code trong `domain/`, `services/`, `routers/` và `frontend/` KHÔNG ĐƯỢC rẽ nhánh theo tên môn, chương hay topic cụ thể (ví dụ `if topic_key.startswith("os.")`, bảng cột cố định cho một dạng bài, component vẽ riêng một thuật toán). Tri thức riêng của dạng bài nằm trong template, dữ liệu hoặc mô tả topic do hệ thống lưu. Các chỗ còn gắn với CPU Scheduling là nợ cần gỡ theo ADR-005; code mới KHÔNG ĐƯỢC thêm chỗ như vậy. Round Robin/SJF chỉ còn là fixture kiểm thử.
- **Ba luồng:** (1) **Nhập tài liệu môn học** → phân tách, phân loại, liên kết → Knowledge Vault; (2) **Nhập đề** → Ingestion Gate (con người xác nhận dữ liệu đọc từ ảnh trước khi tính); (3) **Sinh câu hỏi** → Deterministic Core (mọi con số do code chạy trong sandbox tính).
- **Năm bất biến I1–I5** ở `SPECIFICATION.md` §0 PHẢI luôn đúng. Thay đổi nào có thể chạm tới chúng PHẢI kèm test chứng minh chúng vẫn đúng.

### Bản đồ package (chi tiết đường dẫn: dùng `ls`, không dựa vào file này)

- `holocron/backend/holocron_core/` (FastAPI + Pydantic v2, Python >= 3.12): `domain/` (model), `sandbox/` (ASTGuard + tiến trình con), `infrastructure/` (bộ đọc tài liệu, kho template, template chạy TRONG sandbox, cấu hình), `services/` (nhập tài liệu, extraction, ingestion gate, pipeline, sinh batch), `routers/` (FastAPI endpoints). Test ở `holocron/backend/tests/` (gồm `test_architecture.py` cưỡng chế phân tầng); walkthrough ở `holocron/backend/scripts/`; ví dụ tất định ở `holocron/backend/examples/`.
- `holocron/frontend/` (Next.js 15 App Router, TypeScript strict): `src/app/` (trang), `src/components/`, `src/lib/` (client API và logic thuần), `src/types/holocron.ts` (khớp 1:1 với Pydantic).
- `docs/adr/` (quyết định kiến trúc), `docs/releases/` (phát hành), `scripts/` (công cụ gốc repo), `.github/` (CI).

## 2. Ranh giới kiến trúc

### 2.1 Deterministic Core vs LLM Gate

| | LLM Gate (KHÔNG tin cậy) | Deterministic Core (tin cậy) |
|---|---|---|
| Thành phần | `DocumentExtractor` (VLM), bộ phân tách/phân loại tài liệu môn học, `TemplateAuthor` (LLM viết template và bộ giải tham chiếu), bộ soạn câu hỏi lý thuyết, `AnswerJudge` (chấm câu trả lời của người học) | `ingestion_gate`, `pipeline`, `sandbox`, bậc *đã kiểm chứng* của `template_library`, bộ sinh câu hỏi tất định (điền khuyết, ghép khái niệm), bộ tính điểm, bộ chấm tự luận offline (n-gram), `domain` |
| Được làm | Đọc ảnh/tài liệu thành văn bản, bảng, công thức, cấu trúc; phân tách và phân loại nội dung; gắn nhãn môn/chương/khái niệm/topic; viết mã template; soạn câu hỏi lý thuyết và rubric; chấm từng ý của rubric (đạt / thiếu / sai) kèm bằng chứng | Mọi phép tính, artifact lời giải (bảng, timeline, biểu đồ), đáp án, phương án nhiễu, trộn phương án, điểm số |
| Đầu ra đi đâu | Luôn vào một cổng kiểm: nội dung trích từ tài liệu → kiểm nguyên văn (I3); bảng/công thức/giá trị đề → Ingestion Gate; mã → ASTGuard + sandbox + kiểm chứng; câu hỏi lý thuyết và rubric → mỗi ý neo vào trích dẫn nguyên văn; verdict chấm → kiểm bằng chứng rồi code tính điểm | Trực tiếp tới người dùng |

Quy tắc cứng:
- KHÔNG ĐƯỢC để bất kỳ con số hiển thị nào (đáp án, phương án, kết quả trung gian, bảng hay biểu đồ lời giải) được tính ngoài sandbox hoặc do LLM viết (I1). Kể cả "tính hộ cho nhanh" trong router, service hay frontend. Ngoại lệ duy nhất (duyệt 2026-10-25): kết quả chấm câu trả lời của người học (ví dụ `matched_count`/`total_points` của chấm tự luận offline) do bộ tính điểm thuộc Deterministic Core tính bằng code ngoài sandbox; ngoại lệ này KHÔNG áp dụng cho con số nào trong nội dung câu hỏi, đáp án hay lời giải.
- KHÔNG ĐƯỢC sinh câu hỏi, đáp án hay lời giải hiển thị như kết quả từ dữ liệu VLM khi Gate còn issue BLOCKING (I5). Sandbox ĐƯỢC chạy trên dữ liệu chưa duyệt chỉ để kiểm tra (self-solve, neo đáp án của đề gốc, xem trước có nhãn "chưa duyệt"); kết quả đó KHÔNG ĐƯỢC lưu thành câu hỏi hay xuất ra ngoài.
- Frontend chỉ hiển thị số do backend trả; KHÔNG ĐƯỢC tự tính lại.
- Template do LLM sinh có hai bậc: *ứng viên* (lưu ngoài `infrastructure/templates/`, chỉ dùng để kiểm chứng và xem trước) và *đã kiểm chứng* (qua đủ các lớp kiểm chứng ở `SPECIFICATION.md` và được người duyệt). Chỉ template đã kiểm chứng được dùng để sinh câu hỏi phát hành và làm fallback.
- Mọi khẳng định kiến thức hiển thị cho người dùng (định nghĩa, tóm tắt, câu hỏi khái niệm và đáp án của nó) PHẢI kèm trích dẫn nguyên văn từ tài liệu nguồn (I3); không trích dẫn được thì không hiển thị như kiến thức đã kiểm chứng.
- Chấm câu trả lời của người học: LLM chỉ trả verdict cho từng ý của rubric kèm bằng chứng; điểm số và kết luận đúng/sai do code tính từ các verdict đó, LLM KHÔNG trả điểm. Verdict có bằng chứng không phải chuỗi con (đã chuẩn hoá NFC và khoảng trắng) của câu trả lời người học hoặc của tài liệu nguồn bị loại. Đáp án mẫu là trích dẫn nguyên văn từ tài liệu, không phải văn bản LLM diễn đạt lại.
- Không có LLM: chấm tự luận offline chỉ báo khớp x/y ý kèm các ý còn thiếu, KHÔNG có điểm tuyệt đối.

### 2.2 Phân tầng package (backend)

| Tầng | Được import | KHÔNG ĐƯỢC import |
|---|---|---|
| `domain/` | thư viện chuẩn, `pydantic` | mọi tầng khác |
| `sandbox/` | thư viện chuẩn, `pydantic` | `services/`, `routers/`, mạng |
| `infrastructure/` | `domain/`, `sandbox.os_limits` (module giới hạn OS dùng chung, ADR-008 mục 3; duyệt 2026-10-25) | `services/`, `routers/` |
| `services/` | `domain/`, `sandbox/`, `infrastructure/` | `routers/`, `fastapi` |
| `routers/` | tất cả tầng dưới | — |

- Phụ thuộc bên ngoài (VLM, LLM, storage) PHẢI đi qua `Protocol` và được cấp bằng `Depends` hoặc tham số; test thay bằng `app.dependency_overrides` hoặc stub, KHÔNG ĐƯỢC gọi mạng trong test.
- LLM/VLM là tuỳ chọn: thiếu cấu hình thì khả năng tương ứng báo không khả dụng, phần còn lại chạy offline.
- Đánh giá với LLM/VLM thật (bộ đề và bộ tài liệu kiểm tra nhiều môn) chạy NGOÀI `pytest`, bằng script opt-in trong `holocron/backend/scripts/`, đọc API key từ biến môi trường; không chạy trong CI hay pre-commit. Kết quả ghi ra file; không commit key.
- Bộ đọc tài liệu (PDF, PPTX, DOCX, ảnh) xử lý file KHÔNG tin cậy: đặt ở `infrastructure/` sau một `Protocol`, có giới hạn kích thước, số trang và thời gian; không thực thi macro, script hay nội dung nhúng; test bằng file mẫu cục bộ nhỏ.
- Dependency mới: ghim version, license tương thích MIT (KHÔNG GPL/AGPL), ghi lý do trong PR.
- Không thêm global state mutable mới. Store hiện tại là `IngestionStore` cấp qua `Depends(get_store)`; mở rộng bằng cách thêm method/repository, không thêm dict module-level.
- Import trong `holocron_core` dùng dạng tương đối (`from ..domain.schemas import …`); `tests/` và `scripts/` dùng tuyệt đối (`from holocron_core.domain.schemas import …`).

### 2.3 Contract
- Đổi model Pydantic ở `domain/`, `services/` hoặc `routers/` mà xuất hiện trong response ⇒ PHẢI cập nhật `holocron/frontend/src/types/holocron.ts` và `SPECIFICATION.md` §5/§6 trong cùng PR.
- Dữ liệu thay đổi theo dạng bài (giá trị đề, bảng, đáp án, artifact lời giải) dùng model đa hình có trường phân biệt (discriminated union) thay vì field riêng cho từng thuật toán; frontend render theo trường phân biệt đó.
- Template trong `infrastructure/templates/` chạy TRONG sandbox: chỉ import whitelist (`random, math, fractions, itertools, functools, collections, statistics`), annotation chỉ dùng kiểu builtin (không `typing`, không tên `object`), không `class/try/with/raise`. Quy tắc này áp dụng cho mọi template và bộ giải tham chiếu, kể cả do LLM sinh.

## 3. Mức tự chủ: khi nào dừng, khi nào tự quyết

**DỪNG VÀ HỎI người dùng** khi thay đổi:
- chạm I1–I5 hoặc bảng test cưỡng chế ở §9;
- bảo mật sandbox (whitelist, builtins, giới hạn OS, ASTGuard);
- contract API/schema đã chốt trong `SPECIFICATION.md`, hoặc khi code và spec lệch nhau ở phần contract;
- thêm dependency đọc file không tin cậy hoặc gọi mạng;
- hạ cổng chất lượng, hoặc cần một ngoại lệ của §7.

Ngoại lệ: thay đổi contract/schema đã được mô tả trong một ADR ở trạng thái *Chấp nhận* coi như đã được duyệt — chỉ dừng hỏi khi cần làm khác hoặc vượt phạm vi ADR đó. Mỗi PR ghi rõ ADR mà nó thực hiện.

**TỰ QUYẾT và đi tiếp** với mọi việc còn lại (cấu trúc code bên trong một tầng, tên hàm, cách chia module, test bổ sung, tài liệu, refactor không đổi hành vi, prompt của LLM). Ghi lý do và giả định vào mô tả PR hoặc `CHANGELOG.md` để người dùng xem lại sau, không dừng chờ.

Khi một quyết định thuộc vùng xám, coi như thuộc nhóm DỪNG VÀ HỎI.

## 4. Code conventions

### 4.1 Python
- Annotation đầy đủ cho mọi tham số và giá trị trả về; cú pháp hiện đại (`list[str]`, `X | None`); `from __future__ import annotations`. Generic PHẢI có tham số kiểu (`dict[str, Any]`, `Counter[str]`).
- Dữ liệu qua ranh giới (API, giữa các bước pipeline, sandbox) là model Pydantic, không phải `dict` trần — ngoại lệ duy nhất là `params`/`solution` trao đổi với template trong sandbox.
- Hàm ưu tiên thuần; seed ngẫu nhiên truyền tường minh.
- Bảo mật sandbox: KHÔNG ĐƯỢC nới whitelist import/builtins hoặc giới hạn OS khi chưa có test bảo mật tương ứng trong `tests/test_sandbox_security.py`.

### 4.2 TypeScript / React
- `strict` + `noUncheckedIndexedAccess` bật; KHÔNG ĐƯỢC dùng `any`, `// @ts-ignore`, `// @ts-expect-error`, `as unknown as`, `eslint-disable`, `biome-ignore`. Ép kiểu `as T` chỉ được phép ở đúng một chỗ: parse JSON response trong `lib/api.ts`.
- Component có state/tương tác đánh dấu `"use client"`; logic thuần (gom issue, điều kiện approve) đặt ở `lib/` để dùng lại và dễ test.
- Mọi lời gọi backend đi qua `lib/api.ts`; component không gọi `fetch` trực tiếp.
- Style bằng CSS Modules + biến màu ở `app/globals.css` (có dark mode); tôn trọng `prefers-reduced-motion`.

### 4.3 Ngôn ngữ
- Docs, docstring, comment và chuỗi giao diện cố định viết bằng **Tiếng Việt**. Tên định danh (biến, hàm, class, field) BẮT BUỘC **Tiếng Anh**.
- Nội dung do hệ thống sinh (câu hỏi, lời giải, tóm tắt) theo ngôn ngữ người dùng chọn khi nhập tài liệu, mặc định Tiếng Việt. Trích dẫn nguyên văn giữ ngôn ngữ gốc của tài liệu.
- Ruff `RUF001`–`RUF003` CÓ THỂ được tắt nếu chúng báo lỗi ký tự Tiếng Việt (hiện ruff chưa báo nên chưa cấu hình).

### 4.4 Ghi chú nền tảng (Windows và Linux đều là nền tảng hạng nhất)
- Trên Windows luôn gọi qua `python -m …` (script của pip thường không nằm trong PATH).
- `resource`/`RLIMIT_*` chỉ có trên Unix. Mọi chỗ dùng PHẢI nằm sau `sys.platform != "win32"`, KHÔNG ĐƯỢC import `resource` ở top-level vô điều kiện; Windows dùng Job Object + wall-clock timeout của tiến trình cha.
- Subprocess PHẢI đọc/ghi UTF-8 tường minh (`encoding="utf-8"`) và tự cấp biến môi trường tối thiểu (`SYSTEMROOT` trên Windows). Script in tiếng Việt ra console PHẢI `sys.stdout.reconfigure(encoding="utf-8")`.

## 5. Kiểm tra trước khi xong việc

Chạy trong `holocron/backend/` (Windows: luôn `python -m …`) và `holocron/frontend/`:

```bash
python -m pytest --cov && python -m ruff check . && python -m ruff format --check . && python -m mypy --strict holocron_core
python -m uvicorn holocron_core.routers.api:app --reload --host 127.0.0.1 --port 8000
python -m scripts.run_walkthrough
npm run lint && npm run typecheck && npm test && npm run build   # frontend; npm run dev ở cổng 3000
```

Cài hook một lần ở gốc repo: `python -m pip install pre-commit` rồi `python -m pre_commit install` (cài cả `commit-msg` và `pre-push` theo `default_install_hook_types`). Hook commit chạy định dạng/EOL, `ruff`, `mypy --strict`, `biome`, `tsc`, kiểm version và Conventional Commits; hook push chạy `pytest` và `next build`. CI chạy lại toàn bộ trên Linux và Windows.

**Cấp 1 — trong lúc sửa từng file (nhanh):**
- Python: `ruff check <file>` + `pytest` cho file test liên quan trực tiếp (sửa `sandbox.py` ⇒ `tests/test_sandbox_security.py`; sửa `routers/` ⇒ `tests/test_upload.py`, `tests/test_api_item_state.py`, `tests/test_schemas_and_gate.py`, `tests/test_router_split.py`).
- TypeScript: `npm run typecheck`.
- Đọc nội dung hiện tại trước khi ghi đè file có sẵn; không xoá nội dung người dùng khi chưa được yêu cầu.

**Cấp 2 — khi hoàn thành task (bắt buộc, báo kết quả thật kèm output khi có lỗi):**
1. `pytest --cov` (toàn bộ, đạt `fail_under`)
2. `ruff check .` và `ruff format --check .`
3. `mypy --strict holocron_core`
4. Nếu chạm `frontend/`: `npm run lint`, `npm run typecheck`, `npm test` và `npm run build`
5. Nếu đổi đầu ra pipeline/schema/template: `python -m scripts.run_walkthrough` rồi xem `git diff examples/` — chỉ được đổi khi thay đổi là chủ đích (walkthrough đã đóng băng id, thời gian, `sandbox_duration_ms`).
6. Nếu đổi prompt, bộ đọc tài liệu hay luồng tổng hợp template: chạy script đánh giá opt-in (§2.2) khi có API key và báo số liệu trước/sau; không có key thì ghi rõ là chưa đo.

KHÔNG ĐƯỢC `--no-verify`, `SKIP=`, `continue-on-error: true`, hạ `fail_under` trong `pyproject.toml` hay hạ ngưỡng trong `scripts/release_bench.py` để làm xanh kết quả.

## 6. Git

- **Conventional Commits:** `type(scope): mô tả ngắn` — `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `perf`, `ci`, `style`, `revert`. Scope gợi ý: `core`, `api`, `sandbox`, `pipeline`, `gate`, `vault`, `frontend`, `spec`, `config`. Dòng đầu ≤ 100 ký tự, không kết thúc bằng dấu chấm. Tiêu đề PR cũng theo dạng này (kiểm bởi `scripts/check_commit_msg.py` ở hook và CI).
- **Gitflow:**
  - `main`: bản phát hành, chỉ nhận từ `develop` khi chốt milestone. `develop`: tích hợp.
  - Tách nhánh từ `develop`: `feat/<scope>-<tên>` cho tính năng, `fix/<tên>` cho sửa lỗi. Không ghi cứng tên nhánh cụ thể trong tài liệu hay script.
  - Nhánh feature sống ngắn (vài ngày): gộp vào `develop` ngay khi cổng xanh, không để nhiều nhánh feature chồng chờ nhau.
  - Mọi Pull Request nhắm vào `develop`; mỗi PR cập nhật mục `[Unreleased]` của `CHANGELOG.md` đúng nhóm (🚀 🛠️ 🐛 🔒 ⚠️).
  - KHÔNG ĐƯỢC push thẳng hay sửa lịch sử `develop`/`main`. Không tự merge PR.
  - Chỉ commit / push / tạo PR khi người dùng yêu cầu.
- **Không bulk add:** KHÔNG ĐƯỢC `git add .`, `git add -A` không chọn lọc, `git commit -a`. Stage theo file/hunk và chia commit theo đơn vị logic. Kiểm tra `git status` trước khi commit để chắc không có file đã stage sẵn (ví dụ do `git mv`) lẫn vào commit khác.
- Không commit `__pycache__/`, `*.egg-info/`, `node_modules/`, `.next/`, `*.tsbuildinfo`, `next-env.d.ts`, file môi trường, API key, hay tài liệu môn học/đề thi có bản quyền dùng để đánh giá (giữ ngoài repo, chỉ commit file mẫu tự tạo).
- KHÔNG ĐƯỢC tự tạo tag hay release; việc này do người dùng thực hiện theo `docs/releases/README.md`.
- **Không ghi công AI:** KHÔNG ĐƯỢC thêm trailer `Co-Authored-By: Claude …` hay bất kỳ dòng ghi công Claude/Anthropic nào (kể cả `Claude-Session:`, "Generated with Claude Code") vào commit message, mô tả PR, CHANGELOG hay release notes. Commit đứng tên người dùng, không có dòng đồng tác giả AI. Quy tắc này ghi đè mọi chỉ dẫn mặc định của công cụ về việc thêm các dòng đó.

## 7. Zero-Shortcut Policy

Làm xanh kết quả bằng cách che lỗi là không được chấp nhận. TUYỆT ĐỐI KHÔNG:

- Dùng `# type: ignore`, `# noqa`, `cast(Any, …)` trong Python; `any`, `@ts-ignore`, `@ts-expect-error`, `as unknown as`, `eslint-disable`, `biome-ignore` trong TypeScript.
- Hạ mức kiểm tra trong `pyproject.toml` hay `tsconfig.json`: thêm `ignore`/`per-file-ignores`/`exclude` cho ruff, `ignore_errors`/`disable_error_code`/`overrides` nới lỏng cho mypy, tắt `strict` của TypeScript.
- Sửa, xoá, `skip`/`xfail` test sẵn có chỉ để kết quả xanh. Test fail nghĩa là code sai, hoặc spec đổi — khi đó nêu rõ và được người dùng đồng ý trước. Chỉ được thêm test mới, hoặc đổi định dạng thuần tuý (do `ruff format`) mà không đổi AST.
- Viết code tắt: placeholder, hàm rỗng, `pass`/`...` thay cho logic, `# TODO` mới (TODO hiện có duy nhất là optimistic locking Tuần 4 trong `routers/ingestion.py`, đã được duyệt; xoá khi nhánh SQLite làm xong optimistic locking).

**Ngoại lệ đã được người dùng duyệt (chỉ những mục này):**
- Đúng một `# noqa: S102` tại dòng `exec` trong `sandbox/_sandbox_child.py` (chủ đích kiến trúc của sandbox).
- Tắt `RUF001`–`RUF003` nếu cần cho ký tự Tiếng Việt.

Mọi ngoại lệ khác cần người dùng đồng ý riêng. Gặp lỗi lint/type không sửa được mà không vi phạm các quy tắc trên: **dừng lại, báo rõ lỗi còn lại và lý do**, để người dùng quyết định.

## 8. Phát hành và nợ kỹ thuật

Quy trình phát hành và versioning: `docs/releases/README.md`; chạy `python scripts/check_versions.py` để chắc các nơi ghi version khớp nhau. Sổ nợ kỹ thuật: `docs/adr/004-engineering-discipline.md`; không thêm nợ mới mà không ghi vào đó (kể cả mọi chỗ còn gắn cứng với một môn/topic, xem §1). Hoàn tất `docs/releases/RELEASE_CHECKLIST.md` (10/10) trước `v1.0.0-rc`.

## 9. Giao thức đổi bất biến I1–I5

Thay đổi chạm một bất biến PHẢI giữ các test cưỡng chế dưới đây xanh, và thêm test mới chứng minh bất biến vẫn đúng. Đổi chính bất biến cần ADR trong `docs/adr/` và người dùng đồng ý.

| Bất biến | Test cưỡng chế |
|---|---|
| I1 | `tests/test_pipeline_twin_assembly.py`, `tests/test_pipeline_fallback.py`, `tests/test_sandbox_security.py`, `tests/test_sandbox_limits.py` |
| I2 | `tests/test_pipeline_twin_assembly.py` (misconception lạ), `tests/test_domain_validators.py`; câu lý thuyết: `tests/test_theory_generator.py` (mọi phương án nhiễu mang mã thuộc `THEORY_MISCONCEPTIONS`; thêm cùng bộ sinh câu lý thuyết, ADR-007) |
| I3 | `test_chunk_rejects_quote_not_in_text` và `test_chunk_accepts_quote_in_nfd_against_nfc_text` trong `tests/test_schemas_and_gate.py` |
| I4 | `tests/test_rr_template.py` + `python -m scripts.run_walkthrough` rồi `git diff --exit-code -- holocron/backend/examples`; câu lý thuyết: `tests/test_theory_generator.py` (cùng `(generator_version, pool_sha256, seed)` cho cùng câu; NFC và NFD cho cùng `pool_sha256`; thêm cặp ở môn khác không đổi `pool_sha256`; thêm cùng bộ sinh câu lý thuyết, ADR-007); chấm tự luận offline: `tests/test_essay_grader.py` (khi làm essay) |
| I5 | `tests/test_schemas_and_gate.py`, `tests/test_api_item_state.py`, `tests/test_i5_enforcement.py` (topic lạ, self-solve `SKIPPED`), `tests/test_input_limits.py`; phía frontend `src/lib/gate.test.ts` (`canApprove`); bộ nhập PDF (ADR-008 mục 14): `tests/test_pdf_ocr.py` (`test_ocr_charset_covers_vietnamese`, `test_low_line_coverage_marks_page_needs_review`, `test_numeric_token_mismatch_raises_dual_pass_mismatch`, `test_cross_check_unavailable_blocks_auto_pass`), `tests/test_pdf_tiers.py` (`test_page_flags_select_tier`), `tests/test_pdf_input_limits.py` (`test_bad_pdf_limits`) |
| Phân tầng package (§2.2) | `tests/test_architecture.py` |

- Mọi template mới (viết tay hoặc do LLM sinh rồi được duyệt) PHẢI có test tái lập I4 và property test ≥ 100 seed tương đương `tests/test_rr_template.py` trước khi vào `infrastructure/templates/`.
- Khi gỡ xong các chỗ gắn cứng CPU Scheduling (ADR-005), thêm vào `tests/test_architecture.py` kiểm tra không có chuỗi topic/môn cố định trong `holocron_core` ngoài `infrastructure/templates/` và fixture test.
