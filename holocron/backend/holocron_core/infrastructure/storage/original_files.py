"""File gốc của tài liệu nhập qua bộ nhập PDF (SPECIFICATION §5.13, §6.5; ADR-006 mục 8).

- Đặt `HOLOCRON_DATA_DIR` ⇒ file nằm ở `HOLOCRON_DATA_DIR/files/<sha256>.pdf`, giữ qua các lần khởi động để xếp lại
  trang `pending` vào hàng đợi (§6.5).
- Không đặt ⇒ thư mục tạm riêng của tiến trình (`holocron_files_p<pid>_*` trong thư mục tạm của hệ điều hành), xoá
  khi tắt app (`close`); khớp với kho in-memory, vốn mất tài liệu đang đọc khi khởi động lại. Lần mở sau xoá các thư
  mục `holocron_files_p<pid>_*` còn sót của tiến trình đã chết (tắt đột ngột, hoặc `rmtree` lỗi trên Windows), không
  đụng thư mục của tiến trình còn sống.

**Lệch §5.13, chờ người dùng quyết:** §5.13 ghi "không đặt `HOLOCRON_DATA_DIR` thì backend dùng kho in-memory, không
ghi gì ra đĩa", nhưng worker đọc PDF chỉ mở được file trên đĩa (CLAUDE.md §2.2, ADR-008 mục 3), nên khi không đặt biến,
file PDF vẫn được ghi vào thư mục tạm nói trên trong lúc app chạy. Đây là cách tạm thời cho tới khi người dùng chọn
hướng cuối.

Tên file trên đĩa chỉ suy ra từ sha256 của nội dung, KHÔNG BAO GIỜ từ tên file người dùng gửi. Ghi nguyên tử: nội dung
được ghi vào một file tạm trong cùng thư mục (`stage`), worker mở thử file tạm đó, rồi mới đổi tên thành tên cuối
(`commit`). File là đầu vào không tin cậy: module này chỉ ghi và xoá byte, không đọc hay phân tích nội dung.
Giới hạn dung lượng lấy từ `PdfReaderConfig.max_bytes` (một nguồn duy nhất, ADR-008 mục 4).
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from ..document_readers.config import PdfReaderConfig

FILES_DIRNAME = "files"
STAGING_PREFIX = ".staging-"
STAGING_SUFFIX = ".part"
ORIGINAL_SUFFIX = ".pdf"  # bộ nhập §6.5 chỉ nhận PDF trong MVP
TEMP_DIR_PREFIX = "holocron_files_"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TEMP_DIR_NAME = re.compile(re.escape(TEMP_DIR_PREFIX) + r"p([0-9]+)_")
_LOGGER = logging.getLogger(__name__)


class OriginalFileError(ValueError):
    """Yêu cầu sai với kho file gốc (nội dung rỗng/quá lớn, sha256 sai dạng, file tạm không do kho tạo)."""


@dataclass(frozen=True)
class StagedFile:
    """File tạm đã ghi đủ nội dung, chờ `commit` hoặc `discard`."""

    path: Path
    sha256: str
    size_bytes: int


if sys.platform == "win32":

    def process_alive(pid: int) -> bool:
        """Tiến trình `pid` còn chạy không (Windows: `OpenProcess` + `GetExitCodeProcess`).

        KHÔNG dùng `os.kill(pid, 0)`: trên Windows lời gọi đó kết thúc tiến trình. Không xác định được ⇒ coi như còn
        sống, để không bao giờ xoá thư mục của tiến trình đang chạy.
        """
        import ctypes
        from ctypes import wintypes

        query_limited_information = 0x1000
        error_access_denied = 5
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(query_limited_information, False, pid)
        if not handle:
            return bool(ctypes.get_last_error() == error_access_denied)
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return bool(code.value == still_active)
        finally:
            kernel32.CloseHandle(handle)

else:

    def process_alive(pid: int) -> bool:
        """Tiến trình `pid` còn chạy không (Unix: tín hiệu 0). Không xác định được ⇒ coi như còn sống."""
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True


def remove_tree(path: Path) -> bool:
    """Xoá cả thư mục; lỗi (ví dụ Windows còn tiến trình giữ file) ⇒ ghi cảnh báo và trả `False`, không ném."""
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        return True
    except OSError as exc:
        _LOGGER.warning("không xoá được thư mục file tạm %s (%s); sẽ dọn ở lần khởi động sau", path, exc)
        return False
    return True


def remove_stale_temp_dirs(base: Path | None = None) -> list[Path]:
    """Xoá các thư mục `holocron_files_p<pid>_*` trong `base` (mặc định thư mục tạm của OS) của tiến trình đã chết.

    Thư mục của tiến trình hiện tại hoặc còn sống, và thư mục không đọc được pid từ tên, được giữ nguyên.
    Trả các thư mục đã xoá.
    """
    root = Path(tempfile.gettempdir()) if base is None else base
    removed: list[Path] = []
    for path in root.glob(f"{TEMP_DIR_PREFIX}*"):
        match = _TEMP_DIR_NAME.match(path.name)
        if match is None or not path.is_dir():
            continue
        pid = int(match.group(1))
        if pid == os.getpid() or process_alive(pid):
            continue
        if remove_tree(path):
            removed.append(path)
    return removed


class OriginalFileStore:
    """Kho file gốc trong một thư mục; mọi đường dẫn trả ra đều nằm ngay trong thư mục đó.

    `max_bytes` là None ⇒ `PdfReaderConfig().max_bytes`; app truyền đúng `max_bytes` của cấu hình bộ đọc đang dùng.
    """

    def __init__(self, root: Path, *, max_bytes: int | None = None, temporary: bool = False) -> None:
        limit = PdfReaderConfig().max_bytes if max_bytes is None else max_bytes
        if limit < 1:
            raise OriginalFileError("max_bytes phải ≥ 1")
        root.mkdir(parents=True, exist_ok=True)
        self._root = root.resolve()
        self._max_bytes = limit
        self._temporary = temporary

    @classmethod
    def open(
        cls, data_dir: Path | None, *, max_bytes: int | None = None, temp_base: Path | None = None
    ) -> OriginalFileStore:
        """`data_dir/files` khi có thư mục dữ liệu; ngược lại một thư mục tạm mới (xoá ở `close`) trong `temp_base`
        (mặc định thư mục tạm của OS), sau khi đã dọn thư mục tạm sót lại của tiến trình đã chết."""
        if data_dir is None:
            remove_stale_temp_dirs(temp_base)
            prefix = f"{TEMP_DIR_PREFIX}p{os.getpid()}_"
            directory = tempfile.mkdtemp(prefix=prefix, dir=None if temp_base is None else str(temp_base))
            return cls(Path(directory), max_bytes=max_bytes, temporary=True)
        return cls(data_dir / FILES_DIRNAME, max_bytes=max_bytes)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def temporary(self) -> bool:
        return self._temporary

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def path_for(self, sha256: str) -> Path:
        """Đường dẫn file gốc của nội dung có `sha256` (64 ký tự hex thường); sai dạng ⇒ `OriginalFileError`."""
        if _SHA256.fullmatch(sha256) is None:
            raise OriginalFileError("sha256 phải gồm 64 ký tự hex thường")
        return self._root / f"{sha256}{ORIGINAL_SUFFIX}"

    def exists(self, sha256: str) -> bool:
        return self.path_for(sha256).is_file()

    def stage(self, content: bytes) -> StagedFile:
        """Ghi `content` vào một file tạm mới trong thư mục kho (đã `fsync`); rỗng hoặc quá giới hạn ⇒ lỗi, không ghi.

        Ghi lỗi giữa chừng ⇒ file tạm bị xoá rồi mới ném lỗi.
        """
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
        """Xoá file gốc; `False` khi không có. Lỗi hệ điều hành (ví dụ Windows `PermissionError`) được ném nguyên."""
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

    def remove_orphans(self, keep: Collection[str]) -> list[str]:
        """Xoá file gốc `<sha256>.pdf` mà sha256 không thuộc `keep` (tài liệu đã xoá nhưng xoá file lỗi, hoặc tiến
        trình trước bị ngắt giữa lúc lưu file và lúc tạo bản ghi). Lỗi xoá một file ⇒ cảnh báo, giữ lại cho lần sau.
        Trả sha256 các file đã xoá."""
        removed: list[str] = []
        for path in self._root.glob(f"*{ORIGINAL_SUFFIX}"):
            sha256 = path.name.removesuffix(ORIGINAL_SUFFIX)
            if _SHA256.fullmatch(sha256) is None or sha256 in keep:
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                _LOGGER.warning("không xoá được file gốc mồ côi %s (%s)", path.name, exc)
                continue
            removed.append(sha256)
        return removed

    def close(self) -> None:
        """Thư mục tạm (không có `HOLOCRON_DATA_DIR`) bị xoá cùng mọi file; lỗi xoá ⇒ cảnh báo (`remove_tree`), lần
        khởi động sau dọn. Thư mục dữ liệu giữ nguyên."""
        if self._temporary:
            remove_tree(self._root)
