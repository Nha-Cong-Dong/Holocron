# Quy trình phát hành Holocron

Tài liệu này mô tả cách đánh version, luồng nhánh Gitflow và các bước tạo một bản phát hành. Danh mục điều kiện phát hành nằm ở [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md), mẫu ghi chú phát hành ở [`RELEASE_NOTES_TEMPLATE.md`](RELEASE_NOTES_TEMPLATE.md), lịch sử thay đổi ở [`../../CHANGELOG.md`](../../CHANGELOG.md).

## 1. Đánh version

Dự án dùng [SemVer 2.0.0](https://semver.org/lang/vi/). Tag có dạng `vX.Y.Z` hoặc `vX.Y.Z-alpha|beta|rc[.n]`.

Cùng một version được viết theo hai quy ước, và `scripts/check_versions.py` kiểm hai dạng này luôn khớp nhau:

| Nơi | Quy ước | Ví dụ |
|---|---|---|
| Git tag, `holocron/frontend/package.json`, mục CHANGELOG | SemVer | `v0.1.0-alpha`, `0.2.0-beta.1`, `1.0.0-rc.2` |
| `holocron/backend/pyproject.toml`, `FastAPI(version=...)` trong `routers/api.py` | PEP 440 | `0.1.0a0`, `0.2.0b1`, `1.0.0rc2` |

Quy tắc đổi: `-alpha[.n]` → `aN`, `-beta[.n]` → `bN`, `-rc[.n]` → `rcN` (`n` mặc định 0); bản ổn định không có hậu tố.

Lộ trình dự kiến:

| Tag | Nội dung |
|---|---|
| `v0.1.0-alpha` | Deterministic Core và Ingestion Gate cơ bản |
| `v0.2.0-beta` | VLM dual-pass, Table Auditor, SJF, Twin và Gantt |
| `v1.0.0-rc` | MVP demo Hackathon |

Bản pre-release (có `-`) được tag trên `develop`. Bản ổn định (không có `-`) chỉ được tag trên commit là tổ tiên của `main`; workflow `release.yml` từ chối nếu không đúng.

## 2. Luồng nhánh (Gitflow)

| Nhánh | Vai trò |
|---|---|
| `main` | Bản phát hành. Chỉ nhận từ `develop` khi chốt milestone. |
| `develop` | Tích hợp. Mọi PR nhắm vào đây. |
| `feat/<scope>-<tên>` | Tính năng, tách từ `develop`. |
| `fix/<tên>` | Sửa lỗi, tách từ `develop`. |
| `chore/<tên>`, `docs/<tên>` | Cấu hình, tài liệu. |

Không push thẳng và không sửa lịch sử `develop`/`main`. Commit và tiêu đề PR theo Conventional Commits (`type(scope): mô tả`), được kiểm bởi hook `commit-msg` và workflow `commit-lint.yml`. Mỗi PR cập nhật mục `[Unreleased]` của CHANGELOG đúng nhóm.

## 3. Quy trình tạo một bản phát hành

Việc tạo tag và release do con người thực hiện; công cụ AI không tự tạo tag hay release.

1. Hoàn tất [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md) cho đúng commit sẽ tag.
2. Trên nhánh riêng tách từ `develop` (ví dụ `chore/release-0.1.0-alpha`):
   - Đổi version ở `package.json` (SemVer), `pyproject.toml` và `FastAPI(version=...)` (PEP 440).
   - Đổi tiêu đề `## [Unreleased]` thành `## [X.Y.Z...] - YYYY-MM-DD` và thêm mục `[Unreleased]` trống mới ở trên.
   - Chạy `python scripts/check_versions.py vX.Y.Z...` cho tới khi xanh.
3. Mở PR vào `develop`; chờ CI xanh trên Linux và Windows rồi merge.
4. Bản ổn định: merge `develop` vào `main` bằng PR khi chốt milestone.
5. Tạo tag trên commit đã merge và push tag:

   ```bash
   git tag -a vX.Y.Z-alpha -m "vX.Y.Z-alpha"
   git push origin vX.Y.Z-alpha
   ```

6. Workflow `release.yml` chạy: kiểm version khớp tag, kiểm tổ tiên `main` (bản ổn định), `pytest`, `scripts/release_bench.py`, rồi tạo GitHub Release với ghi chú trích từ CHANGELOG (`scripts/extract_release_notes.py`), `--prerelease` khi tag có `-`, đính kèm `bench.json`.
7. Kiểm tra release vừa tạo; bổ sung số liệu OCR/VLM và liên kết video demo vào ghi chú theo [`RELEASE_NOTES_TEMPLATE.md`](RELEASE_NOTES_TEMPLATE.md).

## 4. Công cụ

| Lệnh | Việc |
|---|---|
| `python scripts/check_versions.py [tag]` | Version nhất quán (và khớp tag nếu truyền) |
| `python scripts/extract_release_notes.py <tag>` | In mục CHANGELOG của tag |
| `python scripts/release_bench.py [--json file]` | Đo các ngưỡng hiệu năng và độ ổn định |
| `python scripts/check_commit_msg.py --self-test` | Tự kiểm quy tắc commit message |
