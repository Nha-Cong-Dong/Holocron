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
Bước 2, 3 và 4 chạy ĐỒNG THỜI: chạy cổng ở nền (`run_in_background`), cùng lượt gọi `reviewer` và (khi bước 4 yêu cầu) `mutation-tester` ở nền. Report (bước 5) viết sau khi cả ba xong; mọi bước vẫn bắt buộc.
Điều kiện khi chạy đồng thời: `reviewer` và `mutation-tester` KHÔNG ĐƯỢC chạy test hay build trong cùng thư mục với cổng đang chạy (tránh tranh `.pytest_cache`, coverage, `.next`). Chúng chỉ đọc mà không chạy test, hoặc chạy trong worktree detach riêng dưới `D:/prj/holocron-wt/` (tạo bằng `git worktree add --detach`, gỡ bằng `git -c core.longpaths=true worktree remove` khi xong). Brief giao cho chúng ghi rõ worktree được chạy test.
Luôn gọi qua `python -m …` với venv ở gốc repo chính (Windows: `<repo>/.venv/Scripts/python.exe`; Linux: `<repo>/.venv/bin/python`). Chạy trong `holocron/backend` của worktree, dạng có lọc (CLAUDE.md §10). Mỗi lệnh Bash mở đầu bằng `set -o pipefail;` để mã thoát là của lệnh kiểm, không phải của `tail`:
- `python -m pytest --cov -q --tb=short 2>&1 | tail -n 40`
- `python -m ruff check . 2>&1 | tail -n 30`
- `python -m ruff format --check . 2>&1 | tail -n 30`
- `python -m mypy --strict holocron_core 2>&1 | tail -n 30`

Nếu diff chạm `holocron/frontend/` (cần `node_modules`; thiếu thì `npm ci`), chạy trong `holocron/frontend`, cùng dạng có lọc:
- `npm run lint 2>&1 | tail -n 30`, `npm run typecheck 2>&1 | tail -n 30`, `npm test 2>&1 | tail -n 30`, `npm run build 2>&1 | tail -n 30`

Test hỏng cần thêm chi tiết: chạy lại đúng test đó với `python -m pytest <nodeid> -x --tb=long 2>&1 | tail -n 60`, không chạy lại toàn bộ.

Nếu diff chạm pipeline, schema, template hay bộ sinh câu (`services/pipeline*`, `services/theory_generator.py`, `services/twin_batch.py`, `domain/`, `infrastructure/templates/`, `examples/`, `scripts/run_walkthrough.py`):
- `python -m scripts.run_walkthrough` rồi `git diff --exit-code -- examples`

Nếu diff đổi prompt LLM, bộ đọc tài liệu (`infrastructure/document_readers/`) hay luồng tổng hợp template (CLAUDE.md §5 Cấp 2 mục 6):
- chạy script đánh giá opt-in tương ứng trong `holocron/backend/scripts/` (ví dụ `eval_concept_proposal`, `eval_pdf_ocr`) khi có API key hoặc dữ liệu cục bộ ngoài repo, trong trần chi phí đã duyệt, ghi số liệu trước/sau; không chạy được thì ghi rõ "chưa đo" và lý do.

Ghi cho từng lệnh: lệnh, đạt/không đạt, tối đa 10 dòng lỗi nguyên văn (các dòng lỗi gốc, không phải dòng tóm tắt), và lệnh tái hiện (đúng nodeid hoặc file:dòng). Ghi coverage thực tế (dòng `TOTAL`) so với `fail_under` trong `pyproject.toml`.

## 3. Review
Gọi subagent `reviewer` (Agent tool, `subagent_type: reviewer`) chỉ với: tên nhánh, đường dẫn worktree, phạm vi `origin/develop...<nhánh>`, đường dẫn brief. KHÔNG kèm nhận định, tóm tắt hay kết luận của agent chính.
- Brief theo khung CLAUDE.md §10, chỉ gồm dữ kiện:
  - `GOAL`: review diff `origin/develop...<nhánh>` theo tiêu chí của agent reviewer; DONE khi đã trả FINDINGS theo hợp đồng đầu ra.
  - `FILES`: read — đường dẫn worktree, danh sách file đổi ở bước 1 (`git diff --name-only`), đường dẫn brief; không có edit/create.
  - `CONTRACT`: các mục `SPECIFICATION.md`, ADR và CLAUDE.md mà brief của nhánh dẫn tới (chỉ đường dẫn và số mục, không tóm tắt).
  - `CHECKS`: lệnh test mà reviewer được chạy (Cấp 1 cho file trong diff).
- Model: diff chạm vùng bất biến ở bước 4 (§9), `sandbox/`/ASTGuard hay bảo mật, hoặc diff rất lớn → `model: opus`, `effort: high`; còn lại `model: sonnet`, `effort: high`.

## 4. Mutation
- Lập danh sách module nguồn trong diff khớp vùng bất biến ở CLAUDE.md §9: Ingestion Gate, validator trong `domain/`, `sandbox/` và ASTGuard, `services/pipeline*`, `services/twin_batch.py`, `services/theory_generator.py` (I2/I4), template trong `infrastructure/templates/` (I4), kiểm trích dẫn nguyên văn (I3), bộ nhập PDF (`services/cross_check_gate.py`, `infrastructure/document_readers/`; I5), chỗ cưỡng chế I5 ở router (`routers/ingestion.py` nhánh approve/khoá sửa, `routers/blueprints.py` điều kiện nhận item, `routers/documents.py`), `holocron/frontend/src/lib/gate.ts`.
- Ngoài danh sách trên, mọi module nguồn trong diff được import (trực tiếp hoặc gián tiếp qua `holocron_core`) bởi một file test có tên trong bảng CLAUDE.md §9 cũng thuộc danh sách. Cách kiểm: với từng module trong diff, grep tên module trong các file test của bảng §9 và trong các module mà các test đó import; có khớp thì đưa vào. Không chắc thì đưa vào.
- Có module khớp: gọi subagent `mutation-tester` (`subagent_type: mutation-tester`, `model: opus`, `effort: high`) chỉ với tên nhánh và danh sách module đó. Không khớp: bỏ qua và ghi lý do.
- Không bao giờ chạy mutation trên toàn repo.

## 5. Report
Viết file mới `.claude/state/reports/<nhánh>-<YYYY-MM-DD-HHmm>.md` (thay `/` trong tên nhánh bằng `-`; không ghi đè report cũ), gồm:
1. Kết luận một dòng: `SẴN SÀNG MỞ PR` / `CẦN SỬA` / `CẦN NGƯỜI DÙNG QUYẾT`.
   - `CẦN SỬA` khi có cổng không đạt, hoặc reviewer báo BLOCKER/MAJOR, hoặc có đột biến sống sót ở vùng bất biến.
   - `CẦN NGƯỜI DÙNG QUYẾT` khi có việc thuộc nhóm "DỪNG VÀ HỎI" (CLAUDE.md §3), kể cả khi các mục khác đạt.
2. Phạm vi: nhánh, commit (HEAD và danh sách), file đổi, brief.
3. Cổng chất lượng: bảng lệnh → đạt/không đạt, coverage thực tế so với `fail_under`; lệnh không đạt kèm ≤ 10 dòng lỗi và lệnh tái hiện.
4. Review: lỗi theo mức nghiêm trọng, file:dòng, quy tắc vi phạm. Giữ nguyên nội dung reviewer trả, không làm mềm.
5. Mutation: bảng đột biến, tỉ lệ bị bắt, đột biến sống sót kèm test còn thiếu; hoặc lý do bỏ qua.
6. Việc cần người dùng quyết (mọi thứ thuộc nhóm "DỪNG VÀ HỎI" ở §3).
7. Những gì chưa chạy hoặc chưa đo được, và vì sao.

## 6. Ghi sổ
Nối thêm một dòng vào cuối `.claude/state/progress.md` (không sửa nội dung cũ): giờ, nhánh, kết luận, đường dẫn report. Báo người dùng kết luận một dòng kèm đường dẫn report.
