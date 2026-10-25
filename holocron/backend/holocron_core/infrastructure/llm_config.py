"""Cấu hình LLM/VLM đọc từ biến môi trường (BYOK, SPECIFICATION v0.6 §11.2).

Chỉ chứa cấu hình và kiểu dữ liệu; KHÔNG gọi mạng và KHÔNG chọn extractor (việc đó thuộc tầng router/service).
Quy tắc cứng:
  * Tên model và địa chỉ endpoint là cấu hình. **Không có nhà cung cấp mặc định** (spec v0.6 §11.2, đã duyệt
    2026-10-25): không đặt `HOLOCRON_LLM_BASE_URL` hay `HOLOCRON_LLM_MODEL_EXTRACT` thì giá trị là `None`, không trỏ
    tới nhà cung cấp nào, để khoá của người dùng không bao giờ bị gửi tới một endpoint họ không chọn.
  * LLM chỉ "đã cấu hình" khi đủ cả ba biến `HOLOCRON_LLM_BASE_URL`, `HOLOCRON_LLM_API_KEY`,
    `HOLOCRON_LLM_MODEL_EXTRACT`; `HOLOCRON_LLM_MODEL_AUTHOR` không đặt thì bằng `HOLOCRON_LLM_MODEL_EXTRACT`.
  * Khoá API được bọc trong `SecretStr`: không xuất hiện trong `repr`, `str`, `model_dump_json` hay log, và
    không bao giờ được đưa vào response trả về client. Khoá chứa khoảng trắng, xuống dòng hay ký tự điều khiển là
    `ConfigError` ngay khi nạp (giá trị đó làm thư viện HTTP ném lỗi mang nguyên giá trị header).
  * Thiếu cấu hình VLM ⇒ `demo_mode = True`; hệ thống PHẢI dùng `MockDualPassExtractor` và UI hiển thị chế độ demo.
  * Giới hạn của một việc gọi LLM (`LlmTaskLimits`) là field có giá trị mặc định, không phải biến môi trường
    (spec v0.6 §11.5).
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, SecretStr

ENV_LLM_BASE_URL = "HOLOCRON_LLM_BASE_URL"
ENV_LLM_API_KEY = "HOLOCRON_LLM_API_KEY"
ENV_LLM_MODEL_EXTRACT = "HOLOCRON_LLM_MODEL_EXTRACT"
ENV_LLM_MODEL_AUTHOR = "HOLOCRON_LLM_MODEL_AUTHOR"
ENV_LLM_TIMEOUT_S = "HOLOCRON_LLM_TIMEOUT_S"
ENV_VLM_BASE_URL = "HOLOCRON_VLM_BASE_URL"
ENV_VLM_API_KEY = "HOLOCRON_VLM_API_KEY"
ENV_VLM_MODEL = "HOLOCRON_VLM_MODEL"

DEFAULT_TIMEOUT_S = 60.0

LLM_REQUIRED_VARIABLES = (ENV_LLM_BASE_URL, ENV_LLM_API_KEY, ENV_LLM_MODEL_EXTRACT)
VLM_REQUIRED_VARIABLES = (ENV_VLM_BASE_URL, ENV_VLM_API_KEY, ENV_VLM_MODEL)

# Khoá gửi trong header `Authorization: Bearer <khoá>`: chỉ nhận ký tự ASCII in được (0x21–0x7E), không khoảng trắng.
KEY_FIRST_CHAR = 0x21
KEY_LAST_CHAR = 0x7E


class ConfigError(ValueError):
    """Biến môi trường có giá trị không hợp lệ. Thông điệp chỉ nêu TÊN biến, không bao giờ nêu giá trị."""


class LlmTaskLimits(BaseModel):
    """Giới hạn cho một việc gọi LLM (ví dụ một lần đề xuất cặp khái niệm), áp dụng ở tầng service.

    - `json_retry_max`: số lần gọi lại khi phản hồi không parse/validate được (spec v0.6 §11.5, `LLM_JSON_RETRY_MAX`).
    - `max_response_chars`: phản hồi dài hơn thì coi như JSON hỏng, không đưa vào bộ parse.
    - `max_json_depth`: độ lồng ngoặc `[`/`{` tối đa (đếm ngoài chuỗi JSON) trước khi gọi bộ parse; sâu hơn thì coi
      như JSON hỏng (bộ parse của thư viện chuẩn đệ quy, phản hồi lồng rất sâu làm nó ném `RecursionError`).
    - `max_parse_starts`: số vị trí `[` tối đa được thử parse trong một phản hồi (mỗi lần thử hỏng tốn thời gian tỉ lệ
      với độ dài phản hồi); vượt số đó mà chưa thấy mảng thì coi như JSON hỏng.
    - `deadline_s`: hạn thời gian tổng của cả việc, gồm mọi lần gọi lại; hết hạn thì việc thất bại (502).
    """

    model_config = ConfigDict(frozen=True)

    json_retry_max: int = Field(default=1, ge=0)
    max_response_chars: int = Field(default=100_000, ge=1)
    max_json_depth: int = Field(default=32, ge=1)
    max_parse_starts: int = Field(default=64, ge=1)
    deadline_s: float = Field(default=180.0, gt=0, allow_inf_nan=False)


class LlmSettings(BaseModel):
    """Endpoint tương thích OpenAI Chat Completions; trường nào `None` là biến tương ứng chưa đặt."""

    model_config = ConfigDict(frozen=True)

    base_url: str | None = None
    api_key: SecretStr | None = None
    model_extract: str | None = None
    model_author: str | None = None
    timeout_s: float = DEFAULT_TIMEOUT_S
    task_limits: LlmTaskLimits = Field(default_factory=LlmTaskLimits)

    @property
    def missing(self) -> tuple[str, ...]:
        """Tên các biến bắt buộc còn thiếu, theo thứ tự của `LLM_REQUIRED_VARIABLES`."""
        values = (self.base_url, self.api_key, self.model_extract)
        return tuple(name for name, value in zip(LLM_REQUIRED_VARIABLES, values, strict=True) if value is None)


class VlmSettings(BaseModel):
    """Model CÓ thị giác cho dual-pass (tương thích OpenAI, nhận `image_url`); cần đủ cả ba biến mới có."""

    model_config = ConfigDict(frozen=True)

    base_url: str
    api_key: SecretStr
    model: str


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    llm: LlmSettings
    vlm: VlmSettings | None = None
    vlm_missing: tuple[str, ...] = ()

    @property
    def demo_mode(self) -> bool:
        """Thiếu cấu hình VLM ⇒ phải dùng extractor giả lập (có nhãn "chế độ demo" trên UI)."""
        return self.vlm is None

    @property
    def llm_configured(self) -> bool:
        """Đủ cả ba biến `HOLOCRON_LLM_BASE_URL`, `HOLOCRON_LLM_API_KEY`, `HOLOCRON_LLM_MODEL_EXTRACT`."""
        return not self.llm.missing


def is_valid_key(value: str) -> bool:
    """Khoá chỉ gồm ký tự ASCII in được, không khoảng trắng, xuống dòng hay ký tự điều khiển."""
    return bool(value) and all(KEY_FIRST_CHAR <= ord(char) <= KEY_LAST_CHAR for char in value)


def has_control_characters(value: str) -> bool:
    """Có ký tự điều khiển C0 (kể cả CR, LF, NUL), DEL hoặc C1."""
    return any(ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in value)


def _read(env: Mapping[str, str], name: str) -> str | None:
    """Giá trị đã cắt khoảng trắng; chuỗi rỗng coi như chưa đặt."""
    value = env.get(name, "").strip()
    return value or None


def _read_plain(env: Mapping[str, str], name: str) -> str | None:
    """Như `_read`, nhưng ký tự điều khiển (kể cả xuống dòng) là `ConfigError`; thông điệp chỉ nêu tên biến."""
    value = _read(env, name)
    if value is not None and has_control_characters(value):
        raise ConfigError(f"{name} không hợp lệ: chứa xuống dòng hoặc ký tự điều khiển")
    return value


def _read_key(env: Mapping[str, str], name: str) -> SecretStr | None:
    """Khoá API hợp lệ theo `is_valid_key`; sai ⇒ `ConfigError` chỉ nêu tên biến, không mang giá trị."""
    value = _read(env, name)
    if value is None:
        return None
    if not is_valid_key(value):
        raise ConfigError(
            f"{name} không hợp lệ: chỉ được chứa ký tự ASCII in được, không có khoảng trắng, xuống dòng "
            "hay ký tự điều khiển"
        )
    return SecretStr(value)


def _timeout(env: Mapping[str, str]) -> float:
    raw = _read(env, ENV_LLM_TIMEOUT_S)
    if raw is None:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{ENV_LLM_TIMEOUT_S} không hợp lệ: cần một số dương (giây)") from None
    if not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{ENV_LLM_TIMEOUT_S} không hợp lệ: cần một số dương hữu hạn (giây)")
    return value


def load_runtime_config(env: Mapping[str, str] | None = None) -> RuntimeConfig:
    """Đọc cấu hình từ `env` (mặc định `os.environ`); `env` truyền tường minh để test không phụ thuộc máy chạy."""
    source = os.environ if env is None else env
    model_extract = _read_plain(source, ENV_LLM_MODEL_EXTRACT)
    llm = LlmSettings(
        base_url=_read_plain(source, ENV_LLM_BASE_URL),
        api_key=_read_key(source, ENV_LLM_API_KEY),
        model_extract=model_extract,
        model_author=_read_plain(source, ENV_LLM_MODEL_AUTHOR) or model_extract,
        timeout_s=_timeout(source),
    )
    vlm_base_url = _read_plain(source, ENV_VLM_BASE_URL)
    vlm_key = _read_key(source, ENV_VLM_API_KEY)
    vlm_model = _read_plain(source, ENV_VLM_MODEL)
    values = (vlm_base_url, vlm_key, vlm_model)
    missing = tuple(name for name, value in zip(VLM_REQUIRED_VARIABLES, values, strict=True) if value is None)
    if vlm_base_url is None or vlm_key is None or vlm_model is None:
        return RuntimeConfig(llm=llm, vlm=None, vlm_missing=missing)
    return RuntimeConfig(llm=llm, vlm=VlmSettings(base_url=vlm_base_url, api_key=vlm_key, model=vlm_model))
