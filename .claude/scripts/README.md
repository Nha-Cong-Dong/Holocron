# `.claude/scripts/`

Script phiên Claude Code được phép chạy. Sửa file trong thư mục này và `.claude/settings.json` bị chặn bằng `deny`
(Edit/Write) — mọi thay đổi phải qua người dùng (quyết định 2026-10-29, `.claude/state/decisions-answers-2026-10-29.md`
mục (c)).

## `merge-to-develop.sh <số PR> <SHA head>`

Tự merge một PR vào `develop` (merge commit, `--match-head-commit`). Từ chối, in lý do và thoát mã 2 khi:

- tham số sai dạng (số PR chỉ chữ số; SHA đúng 40 ký tự hex thường);
- PR không OPEN, base khác `develop`, đang là nháp, head khác SHA truyền vào, hoặc không `MERGEABLE`;
- thiếu một job bắt buộc, hoặc có lượt chạy của job đó với conclusion khác `SUCCESS`. Job bắt buộc (tên đúng như
  GitHub hiển thị): `tests (ubuntu-latest, py3.12)`, `tests (ubuntu-latest, py3.13)`, `tests (windows-latest, py3.12)`,
  `frontend (node 20)`, `frontend (node 22)`, `quality`, `determinism`, `commit-lint`;
- PR đụng file cần người dùng duyệt: `holocron_core/sandbox/**`, `infrastructure/storage/migrations/**`,
  `routers/access.py` (token/Host), `routers/api.py` (gắn middleware), `routers/static_site.py` (Host trên trang tĩnh),
  `SPECIFICATION.md`, `.claude/**`, `.github/**`, `pyproject.toml`, `package.json`, `package-lock.json`,
  `requirements*.txt` — so theo danh sách file của PR (`GET /repos/{owner}/{repo}/pulls/{n}/files`), không theo nhãn;
- PR sửa, xoá hoặc đổi tên file test có sẵn (`tests/` hay `*.test.ts(x)`); thêm file test mới thì được. PR sửa test
  chuỗi giao diện (câu 12) vẫn bị từ chối ở đây và được báo người dùng merge.

Merge xong ghi một dòng vào `.claude/state/merge-log.md`: thời gian, số PR, SHA head, SHA merge, danh sách job đã kiểm.
Cần `gh` đã đăng nhập; không cần `jq` (dùng `gh --jq`).

Sau mỗi lần merge: develop đỏ thì dừng chuỗi, không merge tiếp, revert bằng PR mới và báo người dùng (câu 1).

## Lớp chặn phía server (người dùng tự bật)

GitHub → repo `Nha-Cong-Dong/Holocron` → **Settings** → **Rules** → **Rulesets** → **New ruleset** → **New branch ruleset**:

1. Tên: `develop-required-checks`; Enforcement status: **Active**.
2. **Target branches** → Add target → **Include by pattern** → `develop`.
3. Bật **Require a pull request before merging** (không cần số người duyệt nếu muốn script merge được).
4. Bật **Require status checks to pass** → **Add checks** → thêm đúng 8 tên ở trên (gõ tên, chọn nguồn GitHub Actions).
   Có thể bật thêm "Require branches to be up to date before merging".
5. Bật **Block force pushes**; giữ **Restrict deletions**.
6. **Create**.

Không thêm "Bypass list" cho tài khoản dùng cho Claude Code. Từ đó GitHub tự từ chối merge khi một job bắt buộc chưa xanh,
kể cả khi script có lỗi.
