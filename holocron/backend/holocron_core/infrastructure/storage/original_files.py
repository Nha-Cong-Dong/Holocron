"""File gốc của tài liệu nhập qua bộ nhập PDF (SPECIFICATION §5.13, §6.5; ADR-006 mục 8).

- Đặt `HOLOCRON_DATA_DIR` ⇒ file nằm ở `HOLOCRON_DATA_DIR/files/<sha256>.pdf`, giữ qua các lần khởi động để xếp lại
  trang `pending` vào hàng đợi (§6.5).
- Không đặt ⇒ thư mục tạm riêng của tiến trình, xoá khi tắt app (`close`); khớp với kho in-memory, vốn mất tài liệu
  đang đọc khi khởi động lại.

Tên file trên đĩa chỉ suy ra từ sha256 của nội dung, KHÔNG BAO GIỜ từ tên file người dùng gửi. Ghi nguyên tử: nội dung
được ghi vào một file tạm trong cùng thư mục (`stage`), worker mở thử file tạm đó, rồi mới đổi tên thành tên cuối
(`commit`). File là đầu vào không tin cậy: module này chỉ ghi và xoá byte, không đọc hay phân tích nội dung.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

FILES_DIRNAME = "files"
STAGING_PREFIX = ".staging-"
STAGING_SUFFIX = ".part"
ORIGINAL_SUFFIX = ".pdf"  # bộ nhập §6.5 chỉ nhận PDF trong MVP
DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # §6.5, ADR-008 mục 4
_SHA256 = re.compile(r"[0-9a-f]{64}")


class OriginalFileError(ValueError):
    """Yêu cầu sai với kho file gốc (nội dung rỗng/quá lớn, sha256 sai dạng, file tạm không do kho tạo)."""


@dataclass(frozen=True)
class StagedFile:
    """File tạm đã ghi đủ nội dung, chờ `commit` hoặc `discard`."""

    path: Path
    sha256: str
    size_bytes: int


class OriginalFileStore:
    """Kho file gốc trong một thư mục; mọi đường dẫn trả ra đều nằm ngay trong thư mục đó."""

    def __init__(self, root: Path, *, max_bytes: int = DEFAULT_MAX_BYTES, temporary: bool = False) -> None:
        if max_bytes < 1:
            raise OriginalFileError("max_bytes phải ≥ 1")
        root.mkdir(parents=True, exist_ok=True)
        self._root = root.resolve()
        self._max_bytes = max_bytes
        self._temporary = temporary

    @classmethod
    def open(cls, data_dir: Path | None, *, max_bytes: int = DEFAULT_MAX_BYTES) -> OriginalFileStore:
        """`data_dir/files` khi có thư mục dữ liệu; ngược lại một thư mục tạm mới, xoá ở `close`."""
        if data_dir is None:
            return cls(Path(tempfile.mkdtemp(prefix="holocron_files_")), max_bytes=max_bytes, temporary=True)
        return cls(data_dir / FILES_DIRNAME, max_bytes=max_bytes)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def temporary(self) -> bool:
        return self._temporary

    def path_for(self, sha256: str) -> Path:
        """Đường dẫn file gốc của nội dung có `sha256` (64 ký tự hex thường); sai dạng ⇒ `OriginalFileError`."""
        if _SHA256.fullmatch(sha256) is None:
            raise OriginalFileError("sha256 phải gồm 64 ký tự hex thường")
        return self._root / f"{sha256}{ORIGINAL_SUFFIX}"

    def exists(self, sha256: str) -> bool:
        return self.path_for(sha256).is_file()

    def stage(self, content: bytes) -> StagedFile:
        """Ghi `content` vào một file tạm mới trong thư mục kho (đã `fsync`); rỗng hoặc quá giới hạn ⇒ lỗi, không ghi."""
        if not content:
            raise OriginalFileError("file rỗng")
        if len(content) > self._max_bytes:
            raise OriginalFileError(f"file vượt {self._max_bytes} bytes")
        descriptor, name = tempfile.mkstemp(prefix=STAGING_PREFIX, suffix=STAGING_SUFFIX, dir=self._root)
        path = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return StagedFile(path=path, sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content))

    def _check_staged(self, staged: StagedFile) -> Path:
        """File tạm phải do kho này tạo: nằm ngay trong thư mục kho, đúng tiền tố và hậu tố."""
        path = staged.path.resolve()
        name = path.name
        if path.parent != self._root or not (name.startswith(STAGING_PREFIX) and name.endswith(STAGING_SUFFIX)):
            raise OriginalFileError("file tạm không thuộc kho file gốc")
        return path

    def commit(self, staged: StagedFile) -> Path:
        """Đổi tên file tạm thành `<sha256>.pdf` (nguyên tử trong cùng thư mục) và trả đường dẫn cuối.

        Đã có file cùng sha256 thì nội dung trùng nhau: giữ file cũ (có thể worker đang đọc), bỏ file tạm.
        """
        source = self._check_staged(staged)
        target = self.path_for(staged.sha256)
        if target.is_file():
            source.unlink(missing_ok=True)
            return target
        os.replace(source, target)
        return target

    def discard(self, staged: StagedFile) -> None:
        self._check_staged(staged).unlink(missing_ok=True)

    def delete(self, sha256: str) -> bool:
        """Xoá file gốc; `False` khi không có."""
        path = self.path_for(sha256)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def clean_staging(self) -> int:
        """Xoá file tạm còn sót (tiến trình trước bị ngắt giữa `stage` và `commit`); trả số file đã xoá."""
        removed = 0
        for path in self._root.glob(f"{STAGING_PREFIX}*{STAGING_SUFFIX}"):
            with contextlib.suppress(OSError):
                path.unlink()
                removed += 1
        return removed

    def close(self) -> None:
        """Thư mục tạm (không có `HOLOCRON_DATA_DIR`) bị xoá cùng mọi file; thư mục dữ liệu giữ nguyên."""
        if self._temporary:
            shutil.rmtree(self._root, ignore_errors=True)
