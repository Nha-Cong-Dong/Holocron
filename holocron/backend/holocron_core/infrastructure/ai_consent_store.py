"""Kho giá trị công tắc đồng ý gửi AI (spec §5.16 "Lưu trữ"; ADR-009 mục 7, 12.4).

- `FileAiConsentStore`: field `ai_consent_enabled` trong `HOLOCRON_DATA_DIR/ai_settings.json`. Ghi nguyên tử dưới khoá
  của instance: tệp tạm tạo bằng `os.open(…, 0o600)` rồi `os.replace`; giữ nguyên các field khác của file (V04 đầy đủ
  thêm nhà cung cấp vào cùng file). File hỏng, quá lớn hoặc không đọc được ⇒ `load` trả `None` (coi như tắt), chỉ log
  một cảnh báo không kèm nội dung hay đường dẫn.
- `UnpersistedAiConsentStore`: không có `HOLOCRON_DATA_DIR`; không lưu được (`persistent = False`).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..domain.ai import SettingsNotPersistentError

AI_SETTINGS_FILENAME = "ai_settings.json"
CONSENT_FIELD = "ai_consent_enabled"
# File cài đặt chỉ chứa vài field nhỏ; lớn hơn trần này coi như hỏng (không đọc vào RAM).
MAX_SETTINGS_BYTES = 1024 * 1024
_FILE_MODE = 0o600
_DIR_MODE = 0o700

logger = logging.getLogger(__name__)


class UnpersistedAiConsentStore:
    """Không có thư mục dữ liệu: công tắc chỉ đặt được bằng `HOLOCRON_AI_CONSENT`."""

    @property
    def persistent(self) -> bool:
        return False

    def load(self) -> bool | None:
        return None

    def save(self, enabled: bool) -> None:
        raise SettingsNotPersistentError


class AiSettingsFile:
    """File `<data_dir>/ai_settings.json` dùng chung cho công tắc đồng ý và nhà cung cấp người dùng.

    Mọi đọc/ghi dưới MỘT khoá của instance (deps cấp một instance cho mỗi thư mục dữ liệu), nên ghi công tắc và ghi
    nhà cung cấp không đè mất field của nhau. Ghi nguyên tử: tệp tạm `os.open(…, 0o600)` rồi `os.replace`.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._path = data_dir / AI_SETTINGS_FILENAME
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> dict[str, Any] | None:
        """Nội dung file dạng object JSON; chưa có ⇒ `None`; hỏng ⇒ `None` kèm một cảnh báo không lộ nội dung."""
        with self._lock:
            return self._read_settings()

    def update(self, mutate: Callable[[dict[str, Any]], None]) -> None:
        """Đọc, sửa bằng `mutate`, ghi nguyên tử, cùng một đoạn khoá. File hỏng ⇒ bắt đầu từ object rỗng.
        `mutate` ném lỗi ⇒ không ghi gì."""
        with self._lock:
            settings = self._read_settings() or {}
            mutate(settings)
            self._write_atomic(json.dumps(settings, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))

    def _read_settings(self) -> dict[str, Any] | None:
        try:
            with self._path.open("rb") as handle:
                raw = handle.read(MAX_SETTINGS_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("không đọc được file cài đặt AI (%s), coi như chưa lưu", type(exc).__name__)
            return None
        if len(raw) > MAX_SETTINGS_BYTES:
            logger.warning("file cài đặt AI quá lớn, coi như hỏng")
            return None
        try:
            parsed: object = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            logger.warning("file cài đặt AI hỏng (không phải JSON UTF-8), coi như chưa lưu")
            return None
        if not isinstance(parsed, dict):
            logger.warning("file cài đặt AI hỏng (không phải object JSON), coi như chưa lưu")
            return None
        return {str(key): value for key, value in parsed.items()}

    def _write_atomic(self, payload: bytes) -> None:
        self._data_dir.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
        temp = self._data_dir / f".{AI_SETTINGS_FILENAME}.{secrets.token_hex(8)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        descriptor = os.open(temp, flags, _FILE_MODE)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self._path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise


class FileAiConsentStore:
    """Công tắc lưu ở field `ai_consent_enabled` của `<data_dir>/ai_settings.json` (file dùng chung)."""

    def __init__(self, data_dir: Path, settings_file: AiSettingsFile | None = None) -> None:
        self._file = AiSettingsFile(data_dir) if settings_file is None else settings_file

    @property
    def persistent(self) -> bool:
        return True

    @property
    def path(self) -> Path:
        return self._file.path

    def load(self) -> bool | None:
        settings = self._file.read()
        if settings is None:
            return None
        value = settings.get(CONSENT_FIELD)
        if value is None:
            return None
        if not isinstance(value, bool):
            logger.warning("file cài đặt AI có công tắc đồng ý không hợp lệ, coi như tắt")
            return None
        return value

    def save(self, enabled: bool) -> None:
        def mutate(settings: dict[str, Any]) -> None:
            settings[CONSENT_FIELD] = enabled

        self._file.update(mutate)
