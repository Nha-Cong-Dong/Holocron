"""Cấu hình LLM/VLM đọc từ biến môi trường (BYOK, SPECIFICATION §11.2).

Chỉ chứa cấu hình và kiểu dữ liệu; KHÔNG gọi mạng và KHÔNG chọn extractor (việc đó thuộc tầng router/service).
Quy tắc cứng:
  * Tên model và địa chỉ endpoint là cấu hình; giá trị mặc định chỉ được khai báo ở đây, không hard-code ở service.
  * Khoá API được bọc trong `SecretStr`: không xuất hiện trong `repr`, `str`, `model_dump_json` hay log, và
    không bao giờ được đưa vào response trả về client.
  * Thiếu cấu hình VLM ⇒ `demo_mode = True`; hệ thống PHẢI dùng `MockDualPassExtractor` và UI hiển thị chế độ demo.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, SecretStr

ENV_LLM_BASE_URL = "HOLOCRON_LLM_BASE_URL"
ENV_LLM_API_KEY = "HOLOCRON_LLM_API_KEY"
ENV_LLM_MODEL_EXTRACT = "HOLOCRON_LLM_MODEL_EXTRACT"
ENV_LLM_MODEL_AUTHOR = "HOLOCRON_LLM_MODEL_AUTHOR"
ENV_LLM_TIMEOUT_S = "HOLOCRON_LLM_TIMEOUT_S"
ENV_VLM_BASE_URL = "HOLOCRON_VLM_BASE_URL"
ENV_VLM_API_KEY = "HOLOCRON_VLM_API_KEY"
ENV_VLM_MODEL = "HOLOCRON_VLM_MODEL"

DEFAULT_LLM_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_LLM_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT_S = 60.0

VLM_REQUIRED_VARIABLES = (ENV_VLM_BASE_URL, ENV_VLM_API_KEY, ENV_VLM_MODEL)


class ConfigError(ValueError):
    """Biến môi trường có giá trị không hợp lệ. Thông điệp chỉ nêu TÊN biến, không bao giờ nêu giá trị."""


class LlmSettings(BaseModel):
    """Endpoint tương thích OpenAI Chat Completions dùng để phân loại topic và viết template."""

    model_config = ConfigDict(frozen=True)

    base_url: str = DEFAULT_LLM_BASE_URL
    api_key: SecretStr | None = None
    model_extract: str = DEFAULT_LLM_MODEL
    model_author: str = DEFAULT_LLM_MODEL
    timeout_s: float = DEFAULT_TIMEOUT_S


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
        return self.llm.api_key is not None


def _read(env: Mapping[str, str], name: str) -> str | None:
    """Giá trị đã cắt khoảng trắng; chuỗi rỗng coi như chưa đặt."""
    value = env.get(name, "").strip()
    return value or None


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
    api_key = _read(source, ENV_LLM_API_KEY)
    llm = LlmSettings(
        base_url=_read(source, ENV_LLM_BASE_URL) or DEFAULT_LLM_BASE_URL,
        api_key=SecretStr(api_key) if api_key is not None else None,
        model_extract=_read(source, ENV_LLM_MODEL_EXTRACT) or DEFAULT_LLM_MODEL,
        model_author=_read(source, ENV_LLM_MODEL_AUTHOR) or DEFAULT_LLM_MODEL,
        timeout_s=_timeout(source),
    )
    vlm_values = {name: _read(source, name) for name in VLM_REQUIRED_VARIABLES}
    missing = tuple(name for name, value in vlm_values.items() if value is None)
    base_url, vlm_key, model = (vlm_values[name] for name in VLM_REQUIRED_VARIABLES)
    if base_url is None or vlm_key is None or model is None:
        return RuntimeConfig(llm=llm, vlm=None, vlm_missing=missing)
    return RuntimeConfig(llm=llm, vlm=VlmSettings(base_url=base_url, api_key=SecretStr(vlm_key), model=model))
