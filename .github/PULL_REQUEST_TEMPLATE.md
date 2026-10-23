## Mô tả

<!-- Thay đổi gì và vì sao. Tiêu đề PR theo Conventional Commits: `type(scope): mô tả`. -->

## Bất biến (đánh dấu những mục bị chạm; mục bị chạm PHẢI có test chứng minh còn đúng)

- [ ] I1 — không số hiển thị nào được tính ngoài sandbox hoặc do LLM/frontend viết
- [ ] I2 — mỗi phương án sai ánh xạ đúng 1 `misconception_code`
- [ ] I3 — `verbatim_quote` là chuỗi con của `raw_text`
- [ ] I4 — `(template_sha256, seed)` tái lập hoàn toàn (`run_walkthrough` và `git diff --exit-code examples` sạch)
- [ ] I5 — còn issue BLOCKING thì không vào pipeline

## Cổng chất lượng

- [ ] `pytest` (toàn bộ) và coverage ≥ ngưỡng `fail_under`
- [ ] `ruff check .` và `ruff format --check .`
- [ ] `mypy --strict holocron_core`
- [ ] Nếu chạm `frontend/`: `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`
- [ ] Không dùng `# type: ignore`, `# noqa`, `biome-ignore`, `skip`/`xfail`; không hạ cấu hình kiểm tra; không sửa/xoá test sẵn có
- [ ] Không thêm code rẽ nhánh theo môn/topic cụ thể (CLAUDE.md §1)
- [ ] Dependency mới: đã ghim version, license tương thích MIT

## Contract và tài liệu

- [ ] Đổi model Pydantic xuất hiện trong response ⇒ đã cập nhật `holocron/frontend/src/types/holocron.ts` và `SPECIFICATION.md` §5/§6
- [ ] Đã thêm mục vào `CHANGELOG.md` phần `[Unreleased]` đúng nhóm (🚀 🛠️ 🐛 🔒 ⚠️)
- [ ] Quyết định kiến trúc mới (nếu có) đã có ADR trong `docs/adr/`
- [ ] Ghi ADR mà PR này thực hiện (nếu đổi contract)
- [ ] Đổi prompt/bộ đọc tài liệu: đã chạy script đánh giá hoặc ghi rõ chưa đo

## Commit

- [ ] Không có trailer Co-Authored-By/ghi công AI trong commit và mô tả PR
