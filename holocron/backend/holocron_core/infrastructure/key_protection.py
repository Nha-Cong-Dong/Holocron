"""Bảo vệ khoá API của nhà cung cấp người dùng khi lưu vào file cài đặt (spec §5.16 "Lưu trữ"; ADR-009 mục 12.4).

- Windows: DPAPI (`CryptProtectData`/`CryptUnprotectData`, phạm vi người dùng hiện tại, không hiện hộp thoại) qua
  `ctypes`, KHÔNG thêm dependency; giá trị lưu là base64 của blob đã mã hoá (`key_storage = "dpapi"`). DPAPI lỗi ⇒
  `KeyStorageError`, không lưu dạng rõ âm thầm.
- Unix: khoá lưu dạng rõ trong file quyền 0600 (`key_storage = "plaintext_file"`); UI hiện cảnh báo (ADR-009 OPEN 8).

Thông điệp lỗi chỉ nêu mã lỗi hệ thống, KHÔNG BAO GIỜ nêu khoá hay blob.
"""

from __future__ import annotations

import base64
import binascii
import sys
from typing import Literal, Protocol

from ..domain.ai import KeyStorageError

StoredKeyKind = Literal["dpapi", "plaintext_file"]

# Entropy phụ của DPAPI: blob của Holocron không giải mã được bằng lời gọi DPAPI không kèm đúng giá trị này.
DPAPI_ENTROPY = b"holocron-ai-provider-key-v1"
CRYPTPROTECT_UI_FORBIDDEN = 0x1


class KeyProtector(Protocol):
    @property
    def kind(self) -> StoredKeyKind: ...

    def protect(self, key: str) -> str:
        """Giá trị lưu vào file cho `key`; lỗi ⇒ `KeyStorageError` (không lưu gì)."""
        ...

    def unprotect(self, stored: str) -> str:
        """Khoá từ giá trị đã lưu; hỏng hoặc không giải mã được ⇒ `KeyStorageError`."""
        ...


class PlaintextKeyProtector:
    """Unix: khoá lưu dạng rõ; bảo vệ duy nhất là quyền 0600 của file cài đặt."""

    @property
    def kind(self) -> StoredKeyKind:
        return "plaintext_file"

    def protect(self, key: str) -> str:
        return key

    def unprotect(self, stored: str) -> str:
        return stored


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char)))

    def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
        buffer = ctypes.create_string_buffer(data, len(data))
        return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    def _dpapi(data: bytes, *, protect: bool) -> bytes:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        source, _source_buffer = _blob(data)
        entropy, _entropy_buffer = _blob(DPAPI_ENTROPY)
        output = _DataBlob()
        function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        description = ctypes.c_wchar_p("holocron") if protect else None
        ok = function(
            ctypes.byref(source),
            description,
            ctypes.byref(entropy),
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output),
        )
        if not ok:
            raise KeyStorageError(f"DPAPI lỗi (mã {ctypes.get_last_error()})")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            kernel32.LocalFree(output.pbData)

    def dpapi_protect(data: bytes) -> bytes:
        return _dpapi(data, protect=True)

    def dpapi_unprotect(data: bytes) -> bytes:
        return _dpapi(data, protect=False)

    DPAPI_AVAILABLE = True
else:

    def dpapi_protect(data: bytes) -> bytes:
        raise KeyStorageError("DPAPI chỉ có trên Windows")

    def dpapi_unprotect(data: bytes) -> bytes:
        raise KeyStorageError("DPAPI chỉ có trên Windows")

    DPAPI_AVAILABLE = False


class DpapiKeyProtector:
    """Windows: khoá mã hoá bằng DPAPI của người dùng hiện tại; giá trị lưu là base64 của blob."""

    @property
    def kind(self) -> StoredKeyKind:
        return "dpapi"

    def protect(self, key: str) -> str:
        try:
            blob = dpapi_protect(key.encode("ascii"))
        except (OSError, UnicodeEncodeError, ValueError):
            raise KeyStorageError("DPAPI không mã hoá được khoá") from None
        return base64.b64encode(blob).decode("ascii")

    def unprotect(self, stored: str) -> str:
        try:
            blob = base64.b64decode(stored.encode("ascii"), validate=True)
        except (binascii.Error, UnicodeEncodeError, ValueError):
            raise KeyStorageError("blob khoá đã lưu hỏng") from None
        try:
            return dpapi_unprotect(blob).decode("ascii")
        except (OSError, UnicodeDecodeError, ValueError):
            raise KeyStorageError("DPAPI không giải mã được khoá") from None


def default_key_protector() -> KeyProtector:
    """DPAPI trên Windows, dạng rõ (file 0600) trên Unix."""
    if DPAPI_AVAILABLE:
        return DpapiKeyProtector()
    return PlaintextKeyProtector()


def protector_for(kind: StoredKeyKind) -> KeyProtector:
    """Bộ giải tương ứng giá trị `kind` đã lưu (đọc file do nền tảng khác ghi ⇒ DPAPI báo không giải mã được)."""
    return DpapiKeyProtector() if kind == "dpapi" else PlaintextKeyProtector()
