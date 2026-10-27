# Review tiến độ tuần 19–23/10/2026

- Ngày lập: 2026-10-23, cập nhật 2026-10-23 sau khi người làm dự án chốt các quyết định A1–A8.
- Phạm vi: bốn hạng mục của tuần (chốt specification, SPEC/PRD chi tiết, khung repo, học Claude Code).
- Mốc đánh giá: `develop` = `903b954` (sau PR #30). Hoạt động trong tuần: 70 commit từ 19/10 tới 23/10, PR #1 tới #30 đã merge.
- Thang: ✅ Hoàn thành · 🟡 Làm dở · ❌ Chưa làm. Mục có quyết định nhưng chưa ghi vào tài liệu được tính là 🟡.

## 1. Tổng quan

| Hạng mục | Ước lượng | Nhận định |
|---|---|---|
| 1. Chốt specification | ~75% | Đã chốt persona, nền tảng, hướng đa môn và tiêu chí chọn stack, nhưng `SPECIFICATION.md` §1 vẫn mô tả MVP một môn và chưa ghi các quyết định này |
| 2. SPEC/PRD chi tiết đến schema và endpoint | ~55% | Contract của Ingestion Gate và sinh twin chi tiết; thiếu database schema, `POST /v1/blueprints`, Vault và luồng nhập tài liệu |
| 3. Repo và khung dự án | ~90% | Cấu hình lint/format/CI/pre-commit đầy đủ, CLAUDE.md kỹ; thiếu `.env.example` |
| 4. Học Claude và Claude Code | 100% | Theo xác nhận của người làm |

Tuần này khung kỹ thuật tiến nhanh và chắc: năm bất biến I1–I5 có test cưỡng chế, cổng kiểm tra chạy cả cục bộ và CI trên Linux lẫn Windows. Điểm yếu nằm ở tầng sản phẩm. Spec chưa theo kịp quyết định chuyển sang đa môn, và luồng chính chưa chạy trọn được vì thiếu `POST /v1/blueprints` và nguồn trích dẫn.

## 2. Bảng chi tiết

| Hạng mục con | TT | Bằng chứng | Ghi chú |
|---|---|---|---|
| 1.1 Bài toán | ✅ | `SPECIFICATION.md` §1.1 | Viết cho CPU Scheduling; cần mở rộng cho đa môn (F4) |
| 1.2 Giải pháp | ✅ | Spec §0 (I1–I5), §3; ADR-001/002/003 *Chấp nhận* | |
| 1.3 Người dùng mục tiêu | 🟡 | Quyết định A2: chỉ sinh viên, tự học và ôn luyện | Đã chốt, chưa ghi vào spec (F3) |
| 1.4 Tech stack và lý do | 🟡 | Quyết định A5: nhà cung cấp LLM do ban tổ chức cấp, phần còn lại chọn theo tiêu chí tốt nhất | Đã chốt, chưa ghi vào spec; chưa ghi vấn đề nhà cung cấp LLM do ban tổ chức cấp không có model thị giác (R4, §11.2) (F3) |
| 1.5 Phạm vi | 🟡 | Spec §1.2–1.4 | Xem mục 3 (F4, F5, F6) |
| 2.1 User flow | 🟡 | Spec §4 | Bước 5 chưa có endpoint, bước 7 cần Vault; sửa sau theo A8 |
| 2.2 Database schema | 🟡 | Spec §5 (model Pydantic); store in-memory | Chưa có bảng, khoá, quan hệ, index (F14); §5 thiếu nhiều model (F13) |
| 2.3 API contracts | 🟡 | Spec §6; 9 route trong `routers/api.py` khớp §6 | Thiếu `POST /v1/blueprints` (F11), GET blueprint/batch (F18), Vault (F12); auth chỉ ngầm định (F9) |
| 2.4 Tính năng cốt lõi khớp schema/API | 🟡 | Ingestion Gate khớp; sinh twin khớp một phần | Trích dẫn chưa có nguồn nên mọi slot vào `review_tickets` (F5, F12) |
| 3.1 Khung repo | 🟡 | `ruff.toml`, `biome.jsonc`, `.pre-commit-config.yaml`, 4 workflow CI, LICENSE, CHANGELOG, README | Thiếu `.env.example` (F15) |
| 3.2 CLAUDE.md | ✅ | `.claude/CLAUDE.md` (đã merge qua PR #30) | Dẫn tới ADR-005/006/007 chưa có file (F7) |
| 4. Học Claude Code | ✅ | Theo xác nhận của người làm | Repo chỉ có CLAUDE.md và memory làm dấu vết sử dụng; không dùng làm căn cứ đánh giá |

## 3. Phạm vi (1.5): vấn đề cụ thể

Bản review đầu nói README và CLAUDE.md "phủ định một phần" phạm vi trong spec. Cách nói đó không chính xác: CLAUDE.md có điều khoản chuyển tiếp (spec thắng cho phần contract) và dẫn ngược về spec §1 cho phạm vi milestone, nên ba tài liệu không mâu thuẫn chính thức. Bằng chứng về nền tảng cũng đặt sai chỗ, vì spec §1.4 là mục *Ràng buộc*, không phải câu tuyên bố nền tảng. Các vấn đề thật:

1. **Spec chỉ mô tả MVP một môn (F4).** `SPECIFICATION.md:38` ghi "bài tính toán môn Hệ điều hành"; `README.md:14` và `CLAUDE.md:9` ghi "môn học bất kỳ". Theo quyết định A4, đa môn là phạm vi MVP và CPU Scheduling chỉ là môn mẫu đầu tiên, nên §1 cần viết lại chứ không chỉ thêm mục tầm nhìn.
2. **Chưa có câu tuyên bố nền tảng (F4).** Spec chỉ có "chạy trên máy người dùng; sẵn sàng đóng gói Tauri" (`:69`) và "đóng gói Tauri" ở Tuần 4 (`:688`). Theo A3: desktop app local đóng gói Tauri, webapp localhost là bước trung gian, BYOK.
3. **Dòng tiêu đề Phạm vi đã cũ (F4).** `:6` ghi "Frontend Ingestion UI" trong khi Twin Viewer đã có (PR #26).
4. **G1 hứa có trích dẫn slide nhưng không có luồng nhập tài liệu (F5).** `:57` kết thúc bằng "trích dẫn slide"; §12 chỉ mô tả cách cắt chunk, không có endpoint nhập tài liệu. Hệ quả: `NoCitationProvider` đẩy mọi slot vào `review_tickets` (§6.3).
5. **Phi mục tiêu lệch với kiến trúc (F6).** `:64` loại "tự luận, chấm tự luận"; `CLAUDE.md:26` đã đưa `AnswerJudge` vào LLM Gate. Spec cần nói rõ chấm câu trả lời thuộc MVP hay sau MVP.

## 4. Danh sách lỗi và thiếu sót

| ID | Vấn đề | Giai đoạn | Trạng thái |
|---|---|---|---|
| F1 | 7 file sửa dở trên nhánh `docs/sync-docs-multi-subject` chưa commit | 0 | Đóng: commit `d3df0c7`…`928f9b8`, merge PR #30 (`903b954`) |
| F2 | Cổng kiểm tra chưa được chạy lại để xác nhận | 0 | Đóng: xem mục 5 |
| F3 | Spec §1 thiếu persona; 1.4 thiếu lý do chọn stack và vấn đề VLM | 1 | Mở |
| F4 | Spec §1 chỉ mô tả MVP một môn; chưa có câu tuyên bố nền tảng; dòng tiêu đề `:6` cũ | 1 | Mở |
| F5 | G1 hứa trích dẫn slide nhưng không có luồng nhập tài liệu vào Vault | 1, 3, 4 | Mở |
| F6 | Phi mục tiêu `:64` lệch với `AnswerJudge` ở `CLAUDE.md:26` | 1 | Mở |
| F7 | ADR-005/006/007 được nhắc nhưng chưa có file; nợ ARCH-001 | 1 | Mở |
| F8 | Chưa có ADR cho bộ nhập PDF (dependency đọc file không tin cậy) | 1 | Mở |
| F9 | Auth chỉ ngầm định; chưa ghi mô hình bảo mật cho desktop app local | 1 | Mở |
| F10 | R6: seed twin trùng, 90 twin chỉ có 45 câu khác nhau | 2, 4 | Mở |
| F11 | Chưa có contract `POST /v1/blueprints`; `subject_code`, `chapter_code`, `cognitive_level`, `mapping_confidence` chưa có nguồn | 3, 4 | Mở |
| F12 | Vault chưa có schema, endpoint hay `CitationProvider` thật | 3, 4 | Mở |
| F13 | §5 thiếu model: `ExamBlueprint`, `KnowledgeChunk`, `Citation`, `ReviewTicket`, `QuestionTemplate`, `PatchAudit`, `GenerationTrace`, `NumericalAnswer`… | 3 | Mở |
| F14 | Chưa có thiết kế DB SQLite (bảng, khoá, quan hệ, index, cột version cho BE-003) | 3 | Mở |
| F15 | Thiếu `.env.example` | 3 | Mở |
| F16 | SJF có template nhưng thiếu trong `TABLE_SCHEMAS`/`TOPIC_ALGORITHMS` | 4 | Mở |
| F17 | Frontend chưa đọc `demo_mode` | 4 | Mở |
| F18 | Thiếu endpoint GET cho blueprint và batch | 3, 4 | Mở |

Các chỗ còn dẫn tới ADR chưa có file (gỡ khi F7 đóng): `SPECIFICATION.md:7`, `README.md:30`, `.claude/CLAUDE.md:3`, `:10`, `:175`, `docs/adr/004-engineering-discipline.md:35`.

## 5. Kết quả cổng kiểm tra

Chạy ngày 2026-10-23 trên `903b954`, Windows 11, Python 3.14.7, Node 24.22.0.

| Cổng | Lệnh | Kết quả |
|---|---|---|
| Test backend | `python -m pytest --cov` (trong `holocron/backend/`) | 191 passed, coverage 99.68% (`fail_under` 94) |
| Lint backend | `python -m ruff check .` | Sạch |
| Định dạng backend | `python -m ruff format --check .` | 41 file đã đúng định dạng |
| Kiểu backend | `python -m mypy --strict holocron_core` | Không lỗi (19 file) |
| Tái lập I4 | `python -m scripts.run_walkthrough` rồi `git diff --exit-code -- examples` | `examples/` không đổi |
| Version | `python scripts/check_versions.py` | Nhất quán: 0.1.0-alpha |
| Lint frontend | `npm run lint` (Biome) | Sạch, 24 file |
| Kiểu frontend | `npm run typecheck` | Không lỗi |
| Test frontend | `npm test` (Vitest) | 37 passed (`gate.test.ts` 19, `twin.test.ts` 18) |
| Build frontend | `npm run build` | Thành công, 4 route |

Ghi chú về môi trường:
- Lần chạy đầu bằng `.venv/` ở gốc repo bị đỏ: 8 file test lỗi khi collect vì `.venv/` thiếu `httpx` (dev dependency đã khai báo trong `pyproject.toml`), nên `starlette.testclient` không import được. Python hệ thống, cũng là Python mà `python -m …` và hook pre-push gọi tới, có đủ gói và cho kết quả như bảng trên. Muốn dùng `.venv/` thì cài lại bằng `pip install -e ".[dev]"`.
- pytest có 1 cảnh báo: Starlette 1.9 báo việc dùng `httpx` với `starlette.testclient` đã deprecated và khuyên chuyển sang `httpx2`. Các dependency backend chỉ ghi cận dưới (`fastapi>=0.110`…), nên một bản Starlette sau này bỏ hẳn `httpx` sẽ làm test đỏ mà code không đổi.

## 6. Lưu ý

- Mục 4 được đánh giá theo xác nhận của người làm, không theo bằng chứng trong repo.
- Kế hoạch 24–25/10 nằm trong giao việc riêng (giai đoạn 0–5); file này chỉ ghi hiện trạng và danh sách F1–F18.
