# ADR 004: Kỷ luật kỹ thuật và quản trị phát hành

- Trạng thái: Chấp nhận
- Ngày: 2026-10-19

## Bối cảnh

Dự án có năm bất biến cần giữ qua nhiều tuần phát triển và nhiều người đóng góp, kể cả công cụ AI. Trước quyết định này, các cổng chất lượng chỉ là lệnh thủ công trong `.claude/CLAUDE.md`, version ba nơi lệch nhau (`pyproject` 0.2.0, `package.json` 0.1.0, FastAPI 0.3.0), và không có quy trình phát hành. Đợt rà soát phát hiện thêm hai lỗ hổng I5 (topic lạ và self-solve `SKIPPED` đều có thể AUTO_PASS) và hai lỗi biên (tên file dài trả 500; bảng > 10 tiến trình bị gắn nhầm mã), đều đã sửa kèm test.

## Quyết định

1. **Một bộ cổng, chạy hai nơi.** Cùng các kiểm tra chạy ở pre-commit (cục bộ) và CI (`.github/workflows/`): `ruff check`, `ruff format --check`, `mypy --strict holocron_core`, `pytest` (Linux và Windows, Python 3.12; Linux thêm 3.13), `biome ci`, `tsc`, `next build`, kiểm Conventional Commits, kiểm version, và kiểm tái lập I4 (`run_walkthrough` rồi `git diff --exit-code` trên `examples/`). Không được `--no-verify`, `SKIP=`, `continue-on-error: true`, hạ `fail_under` hay hạ ngưỡng ở `scripts/release_bench.py`.
2. **Coverage có ngưỡng.** `pytest-cov` đo cả nhánh với `fail_under = 94` (đo được 99,6 %). `sandbox/_sandbox_child.py` và `infrastructure/templates/*` được loại khỏi phép đo vì chỉ chạy trong tiến trình con (ADR 002); hành vi của chúng được kiểm bằng test hành vi.
3. **Kiến trúc được cưỡng chế bằng test** (`tests/test_architecture.py`), không chỉ bằng tài liệu (ADR 001).
4. **Frontend dùng Biome 2.5.15** (ghim chính xác) với preset `recommended` (khai báo `"preset": "recommended"`, thay trường `recommended` đã deprecated) cộng các quy tắc nghiêm hơn ở `holocron/frontend/biome.jsonc`; `vitest` cho logic thuần ở `src/lib/`. Mọi quy tắc khai báo riêng đều là `error`, gồm `noExplicitAny`, `noTsIgnore`, `noNonNullAssertion`, `noArrayIndexKey`, `noAutofocus`, `useSemanticElements` (nợ FE-001 đã trả, xem dưới); không hạ quy tắc nào xuống `warn` nếu chưa có ADR.
5. **Version một nguồn sự thật dạng SemVer**, quy đổi sang PEP 440 cho Python; `scripts/check_versions.py` bắt lệch. Pre-release tag trên `develop`, bản ổn định chỉ tag trên tổ tiên của `main`. Chi tiết ở `docs/releases/README.md`.
6. **CHANGELOG** theo Keep a Changelog với đúng 5 nhóm cố định; release notes sinh từ CHANGELOG, không viết tay.
7. **Cấm lối tắt (Zero-Shortcut)** giữ nguyên: không `# type: ignore`, `# noqa`, `biome-ignore`, `skip/xfail`; không hạ cấu hình kiểm tra; không sửa/xoá test sẵn có. Ngoại lệ duy nhất đã duyệt: một `# noqa: S102` tại `exec` trong sandbox.
8. **Ruleset ruff mở rộng hoãn lại.** Thử bộ `E,F,W,I,B,UP,S,RUF,C4,SIM,PT` (bỏ `S101`): đo ngày 2026-10-19 có 27 vi phạm ở `holocron_core` + `scripts` và 45 nếu tính cả `tests/` (nhiều nhất `PT018` 17 và `UP042` 12). Quá nhiều để gộp vào PR này mà không trộn lẫn thay đổi hành vi; sẽ xử lý ở PR riêng (nợ BE-002), không dùng `# noqa` hay `ignore`.

## Hệ quả

- Lỗi bị chặn sớm ở máy người đóng góp và ở CI cho cả hai hệ điều hành; commit message và version luôn nhất quán.
- Chi phí: pre-commit và CI chậm hơn (nhất là `tests` trên Windows vì spawn tiến trình), và người đóng góp phải cài `pre-commit`.
- Hook `pytest` và `next build` chạy ở giai đoạn `pre-push`, không ở `pre-commit`, để commit không bị chậm.
- Sổ nợ kỹ thuật được ghi công khai ngay trong ADR này thay vì giấu trong cấu hình:

| Mã | Nợ | Ghi chú |
|---|---|---|
| FE-001 | 7 cảnh báo Biome ở `TableAuditor.tsx` (`noAutofocus`, `useSemanticElements`, `noArrayIndexKey`) | **Đã trả 2026-10-23:** key React ổn định từ `lib/gate.ts` (có test), `fieldset` thay `role="group"`, focus bằng callback ref khi mở ô; ba quy tắc nâng lên `error` |
| BE-001 | `tests/` và `scripts/` chưa gõ kiểu: 29 lỗi `mypy --strict` | Không nằm trong cổng mypy hiện tại |
| BE-002 | Ruleset ruff mở rộng: 27 (core + scripts) / 45 (cả tests) vi phạm | PR riêng |
| BE-003 | Optimistic locking nguyên tử | Tuần 4, TODO đã duyệt trong `routers/api.py` |
| BE-004 | Spawn tiến trình cho mỗi lần chạy sandbox | Pool tiến trình dựng sẵn và chạy song song seed |

## Phương án đã loại

- **Chỉ dựa vào CI, không pre-commit:** phản hồi chậm và dễ đẩy commit sai chuẩn lên nhánh chung.
- **Chỉ dựa vào pre-commit:** có thể bị bỏ qua cục bộ; CI là cổng bắt buộc cuối.
- **ESLint + Prettier cho frontend:** hai công cụ và nhiều cấu hình hơn; Biome gộp lint và format trong một công cụ nhanh, đủ cho phạm vi hiện tại.
- **Tag pre-release trên `main`:** làm `main` lẫn bản chưa ổn định; `main` chỉ chứa bản đã chốt milestone.
