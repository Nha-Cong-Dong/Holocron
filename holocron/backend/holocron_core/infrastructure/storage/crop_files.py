"""File PNG của ảnh cắt trên đĩa (SPECIFICATION §5.13 "Ảnh cắt"; ADR-010 mục 19).

Bố cục: `<root>/<document_id>/<crop_ref>.png`, `root` = `HOLOCRON_DATA_DIR/crops`. Đường dẫn CHỈ dựng từ
`document_id` khớp `^doc_[0-9a-f]{16}$` và `crop_ref` khớp `^[0-9a-f]{64}$` (khớp toàn chuỗi), rồi kiểm lại sau khi
phân giải liên kết là vẫn nằm trong `root`; sai ⇒ `ValueError`, không đụng tới đĩa.

Ghi: file tạm trong CÙNG thư mục rồi `os.replace` (nguyên tử); file đích đã có thì giữ (khoá nội dung: cùng tên ⇒
cùng byte). Module không biết gì về hàng `crop_images`: kho SQLite quyết khi nào ghi, xoá và dọn mồ côi.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from pathlib import Path

__all__ = ["CROP_SUFFIX", "CropFiles"]

CROP_SUFFIX = ".png"
_DOCUMENT_ID = re.compile(r"doc_[0-9a-f]{16}")
_CROP_REF = re.compile(r"[0-9a-f]{64}")


def _check_ids(document_id: str, crop_ref: str | None = None) -> None:
    if _DOCUMENT_ID.fullmatch(document_id) is None:
        raise ValueError("document_id không đúng mẫu doc_<16 hex>")
    if crop_ref is not None and _CROP_REF.fullmatch(crop_ref) is None:
        raise ValueError("crop_ref không đúng mẫu <64 hex>")


def _is_link(path: Path) -> bool:
    """Liên kết tượng trưng hoặc junction (Windows): KHÔNG BAO GIỜ được duyệt hay xoá xuyên qua."""
    return path.is_symlink() or path.is_junction()


def _unlink_quietly(path: Path) -> None:
    """Gỡ một mục KHÔNG đi theo liên kết (file, liên kết, junction); lỗi hệ điều hành bị bỏ qua (dọn kiểu best-effort)."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


class CropFiles:
    """Thư mục ảnh cắt của MỘT kho SQLite."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def _inside(self, path: Path) -> Path:
        """`path` sau khi phân giải liên kết phải nằm trong `root` đã phân giải; ngược lại `ValueError`."""
        resolved = path.resolve()
        if not resolved.is_relative_to(self._root.resolve()):
            raise ValueError("đường dẫn ảnh cắt thoát khỏi thư mục dữ liệu")
        return resolved

    def document_dir(self, document_id: str) -> Path:
        _check_ids(document_id)
        return self._inside(self._root / document_id)

    def path_for(self, document_id: str, crop_ref: str) -> Path:
        """Đường dẫn file của `(document_id, crop_ref)`, luôn nằm trong `root`."""
        _check_ids(document_id, crop_ref)
        return self._inside(self._root / document_id / f"{crop_ref}{CROP_SUFFIX}")

    def write(self, document_id: str, crop_ref: str, data: bytes) -> None:
        """Ghi file (tạm cùng thư mục rồi đổi tên); file đích đã có thì giữ nguyên."""
        target = self.path_for(document_id, crop_ref)
        if target.is_file():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        self._inside(target.parent)
        handle, temp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{crop_ref}.", suffix=".tmp")
        temp = Path(temp_name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            os.replace(temp, target)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    def read(self, document_id: str, crop_ref: str) -> bytes | None:
        """Byte của file; file không có ⇒ `None`."""
        path = self.path_for(document_id, crop_ref)
        try:
            return path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, PermissionError):
            return None

    def delete(self, document_id: str, crop_ref: str) -> None:
        self.path_for(document_id, crop_ref).unlink(missing_ok=True)

    def delete_document(self, document_id: str) -> None:
        """Xoá thư mục `<root>/<document_id>/` (nếu có)."""
        _check_ids(document_id)
        path = self._root / document_id
        self._remove(path)  # liên kết ⇒ chỉ gỡ liên kết; không raise (gọi sau commit)

    def _remove(self, path: Path) -> None:
        """Xoá một mục trong thư mục ảnh: liên kết/junction, mục phân giải ra ngoài `root` và file ⇒ chỉ gỡ chính mục
        đó; thư mục thật ⇒ xoá từng con theo cùng luật rồi xoá thư mục. Không bao giờ đi xuyên liên kết hay junction."""
        if _is_link(path) or not path.is_dir() or not self._is_inside(path):
            _unlink_quietly(path)
            return
        try:
            children = list(path.iterdir())
        except OSError:
            return
        for child in children:
            self._remove(child)
        try:
            path.rmdir()
        except OSError:
            return

    def _is_inside(self, path: Path) -> bool:
        try:
            self._inside(path)
        except (ValueError, OSError):
            return False
        return True

    def sweep_orphans(self, keep: Mapping[str, AbstractSet[str]]) -> None:
        """Dọn mồ côi lúc mở kho: trong `root` chỉ giữ thư mục của tài liệu có trong `keep` và trong đó chỉ giữ file
        `<crop_ref>.png` với `crop_ref ∈ keep[document_id]`. Mọi mục khác (file tạm, file lạ, thư mục con, liên kết)
        bị xoá; liên kết/junction chỉ bị gỡ, không bao giờ duyệt vào. `root` chưa có ⇒ không làm gì."""
        if not self._root.is_dir():
            return
        for entry in self._root.iterdir():
            refs = keep.get(entry.name)
            if _is_link(entry) or not self._is_inside(entry):
                _unlink_quietly(entry)
                continue
            if refs is None or not entry.is_dir():
                self._remove(entry)
                continue
            for item in entry.iterdir():
                name = item.name
                crop_ref = name.removesuffix(CROP_SUFFIX)
                if _is_link(item) or not item.is_file() or not name.endswith(CROP_SUFFIX) or crop_ref not in refs:
                    self._remove(item)
