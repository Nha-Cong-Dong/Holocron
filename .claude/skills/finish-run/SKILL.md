---
name: finish-run
description: Kiểm tra tự động khi một nhánh triển khai của Holocron đã xong, trước khi đề xuất mở PR. Chạy cổng chất lượng (CLAUDE.md §5 Cấp 2), gọi reviewer, gọi mutation-tester khi diff chạm vùng bất biến ở §9, rồi ghi report vào .claude/state/reports/.
argument-hint: "[nhánh]"
arguments: [branch]
---
# /finish-run

Nhánh cần kiểm: `$branch`. Nếu giá trị này rỗng thì dùng nhánh hiện tại (`git rev-parse --abbrev-ref HEAD`).

Quy tắc cứng:
- Không tự sửa lỗi tìm được. Không commit, push hay tạo PR.
- Không làm xanh kết quả bằng bất kỳ cách nào ở CLAUDE.md §7 (`--no-verify`, `SKIP=`, skip/xfail, hạ cấu hình hay ngưỡng, sửa test).
- Bước nào lỗi vẫn chạy các bước còn lại và ghi rõ vào report.
- Không in biến môi trường hay key.

## 1. Thu thập
- `git fetch origin`, rồi so với `origin/develop` (develop cục bộ có thể cũ):
  - `git diff --name-only origin/develop...<nhánh>`
  - `git log origin/develop..<nhánh> --oneline`
- Tìm worktree đang check out nhánh: `git worktree list`. Chạy mọi lệnh của bước 2 trong worktree đó. Nhánh không được check out ở đâu thì tạo worktree tạm ngoài repo (`git worktree add <thư mục tạm> <nhánh>`) và xoá khi xong.
- Tìm brief tương ứng trong `.claude/state/briefs/` (theo tên nhánh hoặc nội dung). Không có thì ghi "không có brief".

## 2. Cổng chất lượng (CLAUDE.md §5 Cấp 2), chạy thật
Windows luôn gọi qua `python -m …` với venv của repo (`D:/prj/Holocron/.venv/Scripts/python.exe`). Chạy trong `holocron/backend` của worktree:
- `python -m pytest --cov -q`
- `python -m ruff check .`
- `python -m ruff format --check .`
- `python -m mypy --strict holocron_core`

Nếu diff chạm `holocron/frontend/` (cần `node_modules`; thiếu thì `npm ci`), chạy trong `holocron/frontend`:
- `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`

Nếu diff chạm pipeline, schema hay template (`services/pipeline*`, `domain/`, `infrastructure/templates/`, `examples/`):
- `python -m scripts.run_walkthrough` rồi `git diff --exit-code -- examples`

Nếu diff đổi prompt LLM, bộ đọc tài liệu (`infrastructure/document_readers/`) hay luồng tổng hợp template (CLAUDE.md §5 Cấp 2 mục 6):
- chạy script đánh giá opt-in tương ứng trong `holocron/backend/scripts/` (ví dụ `eval_concept_proposal`, `eval_pdf_ocr`) khi có API key hoặc dữ liệu cục bộ ngoài repo, trong trần chi phí đã duyệt, ghi số liệu trước/sau; không chạy được thì ghi rõ "chưa đo" và lý do.

Ghi cho từng lệnh: lệnh, đạt/không đạt, output lỗi nguyên văn (rút gọn nếu dài). Ghi coverage thực tế so với `fail_under` trong `pyproject.toml`.

## 3. Review
Gọi subagent `reviewer` (Agent tool, `subagent_type: reviewer`) chỉ với: tên nhánh, đường dẫn worktree, phạm vi `origin/develop...<nhánh>`, đường dẫn brief. KHÔNG kèm nhận định, tóm tắt hay kết luận của agent chính.

## 4. Mutation
- Lập danh sách module nguồn trong diff khớp vùng bất biến ở CLAUDE.md §9: Ingestion Gate, validator trong `domain/`, `sandbox/` và ASTGuard, `services/pipeline*`, kiểm trích dẫn nguyên văn (I3), `holocron/frontend/src/lib/gate.ts`.
- Có module khớp: gọi subagent `mutation-tester` (`subagent_type: mutation-tester`) chỉ với tên nhánh và danh sách module đó. Không khớp: bỏ qua và ghi lý do.
- Không bao giờ chạy mutation trên toàn repo.

## 5. Report
Viết file mới `.claude/state/reports/<nhánh>-<YYYY-MM-DD-HHmm>.md` (thay `/` trong tên nhánh bằng `-`; không ghi đè report cũ), gồm:
1. Kết luận một dòng: `SẴN SÀNG MỞ PR` / `CẦN SỬA` / `CẦN NGƯỜI DÙNG QUYẾT`.
   - `CẦN SỬA` khi có cổng không đạt, hoặc reviewer báo BLOCKER/MAJOR, hoặc có đột biến sống sót ở vùng bất biến.
   - `CẦN NGƯỜI DÙNG QUYẾT` khi có việc thuộc nhóm "DỪNG VÀ HỎI" (CLAUDE.md §3), kể cả khi các mục khác đạt.
2. Phạm vi: nhánh, commit (HEAD và danh sách), file đổi, brief.
3. Cổng chất lượng: bảng lệnh → đạt/không đạt, coverage thực tế so với `fail_under`.
4. Review: lỗi theo mức nghiêm trọng, file:dòng, quy tắc vi phạm. Giữ nguyên nội dung reviewer trả, không làm mềm.
5. Mutation: bảng đột biến, tỉ lệ bị bắt, đột biến sống sót kèm test còn thiếu; hoặc lý do bỏ qua.
6. Việc cần người dùng quyết (mọi thứ thuộc nhóm "DỪNG VÀ HỎI" ở §3).
7. Những gì chưa chạy hoặc chưa đo được, và vì sao.

## 6. Ghi sổ
Nối thêm một dòng vào cuối `.claude/state/progress.md` (không sửa nội dung cũ): giờ, nhánh, kết luận, đường dẫn report. Báo người dùng kết luận một dòng kèm đường dẫn report.
