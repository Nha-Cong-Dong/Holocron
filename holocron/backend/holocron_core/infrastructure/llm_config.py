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
  * Base URL (`HOLOCRON_LLM_BASE_URL`, `HOLOCRON_VLM_BASE_URL`) chỉ nhận `https://`, hoặc `http://` khi máy chủ là
    loopback (`127.0.0.1`, `::1`, `localhost`): khoá không bao giờ đi qua mạng dưới dạng rõ. Sai ⇒ `ConfigError` chỉ
    nêu tên biến.
  * Thiếu cấu hình VLM ⇒ `demo_mode = True`; hệ thống PHẢI dùng `MockDualPassExtractor` và UI hiển thị chế độ demo.
  * VLM (ADR-009 mục 4, 6, 7): "đã cấu hình" khi có `HOLOCRON_VLM_BASE_URL` và `HOLOCRON_VLM_MODEL`, cộng
    `HOLOCRON_VLM_API_KEY` khi endpoint không phải loopback (loopback được để trống khoá). Cloud mà trống khoá ⇒ VLM
    chưa cấu hình (`vlm_missing` có tên biến khoá), không phải `ConfigError`. `HOLOCRON_VLM_MODE` (`off` mặc định |
    `images_only`) lưu riêng ở `RuntimeConfig.vlm_mode`, không đổi việc VLM đã cấu hình hay chưa. Các biến
    `HOLOCRON_VLM_TIMEOUT_S`, `HOLOCRON_VLM_CONCURRENCY` (mặc định theo loopback/cloud), `HOLOCRON_VLM_MAX_EDGE_PX`,
    `HOLOCRON_VLM_MAX_IMAGE_BYTES`, `HOLOCRON_VLM_DATA_RECIPIENT` sai ⇒ `ConfigError` chỉ nêu tên biến, không kẹp giá trị.
  * Giới hạn của một việc gọi LLM (`LlmTaskLimits`) và của tầng HTTP (`LlmHttpLimits`) là field có giá trị mặc định,
    không phải biến môi trường (spec v0.6 §11.5).
  * `load_runtime_config` ném `ConfigError` khi một biến sai (dùng cho script và test). Tầng router dùng
    `load_runtime_config_lenient`: nhóm LLM hay VLM có biến sai thì coi như nhóm đó chưa cấu hình, để `GET /v1/runtime`
    không thành 500 và endpoint cần LLM trả 409 `CapabilityError`.
"""

from __future__ import annotations

import logging
import math
import os
import re
import urllib.parse
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

ENV_LLM_BASE_URL = "HOLOCRON_LLM_BASE_URL"
ENV_LLM_API_KEY = "HOLOCRON_LLM_API_KEY"
ENV_LLM_MODEL_EXTRACT = "HOLOCRON_LLM_MODEL_EXTRACT"
ENV_LLM_MODEL_AUTHOR = "HOLOCRON_LLM_MODEL_AUTHOR"
ENV_LLM_TIMEOUT_S = "HOLOCRON_LLM_TIMEOUT_S"
ENV_VLM_BASE_URL = "HOLOCRON_VLM_BASE_URL"
ENV_VLM_API_KEY = "HOLOCRON_VLM_API_KEY"
ENV_VLM_MODEL = "HOLOCRON_VLM_MODEL"
ENV_VLM_MODE = "HOLOCRON_VLM_MODE"
ENV_VLM_TIMEOUT_S = "HOLOCRON_VLM_TIMEOUT_S"
ENV_VLM_CONCURRENCY = "HOLOCRON_VLM_CONCURRENCY"
ENV_VLM_MAX_EDGE_PX = "HOLOCRON_VLM_MAX_EDGE_PX"
ENV_VLM_MAX_IMAGE_BYTES = "HOLOCRON_VLM_MAX_IMAGE_BYTES"
ENV_VLM_DATA_RECIPIENT = "HOLOCRON_VLM_DATA_RECIPIENT"

DEFAULT_TIMEOUT_S = 60.0

# Mặc định của VLM theo loại endpoint (ADR-009 mục 6, người dùng chốt Q13).
VLM_TIMEOUT_LOOPBACK_S = 120.0
VLM_TIMEOUT_CLOUD_S = 60.0
VLM_CONCURRENCY_LOOPBACK = 1
VLM_CONCURRENCY_CLOUD = 2
VLM_CONCURRENCY_MIN = 1
VLM_CONCURRENCY_MAX = 8
DEFAULT_VLM_MAX_EDGE_PX = 2048
DEFAULT_VLM_MAX_IMAGE_BYTES = 4 * 1024 * 1024
VLM_DATA_RECIPIENT_MAX_CHARS = 80

VlmMode = Literal["off", "images_only"]
VLM_MODES: tuple[VlmMode, ...] = ("off", "images_only")
DEFAULT_VLM_MODE: VlmMode = "off"

# Số nguyên thập phân chỉ gồm chữ số ASCII (`int()` còn nhận dấu, `_` và chữ số Unicode khác).
ASCII_DIGITS = re.compile(r"[0-9]+")
# Nhãn bên nhận dạng tên miền: không khoảng trắng và có dấu chấm nằm giữa hai ký tự khác dấu chấm (`api.example.com`).
DOMAIN_LIKE = re.compile(r"\S*[^\s.]\.[^\s.]\S*")

LLM_REQUIRED_VARIABLES = (ENV_LLM_BASE_URL, ENV_LLM_API_KEY, ENV_LLM_MODEL_EXTRACT)
VLM_REQUIRED_VARIABLES = (ENV_VLM_BASE_URL, ENV_VLM_API_KEY, ENV_VLM_MODEL)

# Máy chủ được dùng `http://` (không mã hoá): chỉ địa chỉ loopback, ví dụ model chạy local.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

logger = logging.getLogger(__name__)

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
    - `deadline_s`: hạn thời gian tổng của cả việc, gồm mọi lần gọi lại; hết hạn thì việc thất bại (502). Client dựng
      bằng `OpenAiChatClient.from_settings` cũng dùng hạn này làm cận tổng cho mọi request HTTP của nó.
    - `max_tokens`: `max_tokens` gửi trong mỗi lần gọi.
    - `max_proposals_per_chunk`: số phần tử tối đa được xét trong mảng đề xuất, tính cho mỗi chunk của lần gọi (trần
      của cả mảng = giá trị này × số chunk). Phần tử vượt trần bị bỏ trước khi kiểm nguyên văn và được đếm là bị loại.
      Mặc định gấp đôi số cặp tối đa mỗi chunk nêu trong prompt, để đề xuất hỏng hay trùng không chiếm hết chỗ.
    """

    model_config = ConfigDict(frozen=True)

    json_retry_max: int = Field(default=1, ge=0)
    max_response_chars: int = Field(default=100_000, ge=1)
    max_json_depth: int = Field(default=32, ge=1)
    max_parse_starts: int = Field(default=64, ge=1)
    deadline_s: float = Field(default=180.0, gt=0, allow_inf_nan=False)
    max_tokens: int = Field(default=4096, ge=1)
    max_proposals_per_chunk: int = Field(default=10, ge=1)


class LlmHttpLimits(BaseModel):
    """Giới hạn của adapter HTTP (`infrastructure/llm_client.py`), field có mặc định, không phải biến môi trường.

    - `retry_max`: số lần thử lại tối đa khi gặp 429, 5xx hoặc lỗi mạng (tổng số request = `retry_max + 1`).
    - `backoff_base_s`: thời gian chờ trước lần thử lại thứ k là `backoff_base_s × 2^(k-1)` (0,5 s rồi 1 s).
    - `max_response_bytes`: body phản hồi dài hơn thì thất bại, không parse.
    - `read_chunk_bytes`: body được đọc theo khối tối đa cỡ này; trước mỗi khối adapter so đồng hồ với hạn còn lại.
    """

    model_config = ConfigDict(frozen=True)

    retry_max: int = Field(default=2, ge=0)
    backoff_base_s: float = Field(default=0.5, ge=0, allow_inf_nan=False)
    max_response_bytes: int = Field(default=4 * 1024 * 1024, ge=1)
    read_chunk_bytes: int = Field(default=64 * 1024, ge=1)


class LlmSettings(BaseModel):
    """Endpoint tương thích OpenAI Chat Completions; trường nào `None` là biến tương ứng chưa đặt."""

    model_config = ConfigDict(frozen=True)

    base_url: str | None = None
    api_key: SecretStr | None = None
    model_extract: str | None = None
    model_author: str | None = None
    timeout_s: float = DEFAULT_TIMEOUT_S
    task_limits: LlmTaskLimits = Field(default_factory=LlmTaskLimits)
    http_limits: LlmHttpLimits = Field(default_factory=LlmHttpLimits)

    @property
    def missing(self) -> tuple[str, ...]:
        """Tên các biến bắt buộc còn thiếu, theo thứ tự của `LLM_REQUIRED_VARIABLES`."""
        values = (self.base_url, self.api_key, self.model_extract)
        return tuple(name for name, value in zip(LLM_REQUIRED_VARIABLES, values, strict=True) if value is None)


class VlmSettings(BaseModel):
    """Model CÓ thị giác (tương thích OpenAI, nhận `image_url`) cho bộ đọc trang VLM (ADR-009).

    Chỉ có khi đủ `HOLOCRON_VLM_BASE_URL` và `HOLOCRON_VLM_MODEL`, cộng `HOLOCRON_VLM_API_KEY` khi endpoint không phải
    loopback; với loopback khoá được trống (`api_key = None`, adapter không gửi `Authorization`). `timeout_s` là cận
    tổng của một lần gọi VLM; `timeout_s` và `concurrency` không đặt thì lấy mặc định theo loopback/cloud.
    Địa chỉ endpoint và tên model không có trong `repr`.
    """

    model_config = ConfigDict(frozen=True)

    base_url: str = Field(repr=False)
    api_key: SecretStr | None = None
    model: str = Field(repr=False)
    timeout_s: float = Field(default=VLM_TIMEOUT_CLOUD_S, gt=0, allow_inf_nan=False)
    concurrency: int = Field(default=VLM_CONCURRENCY_CLOUD, ge=VLM_CONCURRENCY_MIN, le=VLM_CONCURRENCY_MAX)
    max_edge_px: int = Field(default=DEFAULT_VLM_MAX_EDGE_PX, ge=1)
    max_image_bytes: int = Field(default=DEFAULT_VLM_MAX_IMAGE_BYTES, ge=1)
    data_recipient: str | None = None
    http_limits: LlmHttpLimits = Field(default_factory=LlmHttpLimits)

    @property
    def local(self) -> bool:
        """Tên máy của `base_url` thuộc `LOOPBACK_HOSTS`: ảnh không rời máy."""
        return is_loopback_url(self.base_url)


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    llm: LlmSettings
    vlm: VlmSettings | None = None
    vlm_missing: tuple[str, ...] = ()
    vlm_mode: VlmMode = DEFAULT_VLM_MODE

    @property
    def demo_mode(self) -> bool:
        """Thiếu cấu hình VLM ⇒ phải dùng extractor giả lập (có nhãn "chế độ demo" trên UI)."""
        return self.vlm is None

    @property
    def llm_configured(self) -> bool:
        """Đủ cả ba biến `HOLOCRON_LLM_BASE_URL`, `HOLOCRON_LLM_API_KEY`, `HOLOCRON_LLM_MODEL_EXTRACT`."""
        return not self.llm.missing

    @property
    def vlm_local(self) -> bool:
        """VLM đã cấu hình và endpoint là loopback (ADR-009 mục 7)."""
        return self.vlm is not None and self.vlm.local

    @property
    def vlm_data_recipient(self) -> str | None:
        """Nhãn bên nhận ảnh (`HOLOCRON_VLM_DATA_RECIPIENT`) khi VLM đã cấu hình; ngược lại `None`."""
        return None if self.vlm is None else self.vlm.data_recipient


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


def check_base_url(value: str, name: str) -> None:
    """`https://<host>…`, hoặc `http://` khi host là loopback; sai ⇒ `ConfigError` chỉ nêu tên biến `name`."""
    host: str | None = None
    scheme = ""
    if not has_control_characters(value):
        try:
            parts = urllib.parse.urlsplit(value)
            scheme, host = parts.scheme.lower(), parts.hostname
        except ValueError:
            host = None
    allowed = scheme == "https" or (scheme == "http" and host in LOOPBACK_HOSTS)
    if not host or not allowed:
        raise ConfigError(
            f"{name} không hợp lệ: cần bắt đầu bằng https://, hoặc http:// khi máy chủ là 127.0.0.1, ::1 hay localhost"
        )


def is_loopback_url(value: str) -> bool:
    """Tên máy của `value` thuộc `LOOPBACK_HOSTS`; URL không phân tích được thì `False`."""
    try:
        host = urllib.parse.urlsplit(value).hostname
    except ValueError:
        return False
    return host in LOOPBACK_HOSTS


def _read_base_url(env: Mapping[str, str], name: str) -> str | None:
    """Như `_read_plain`, cộng kiểm scheme và máy chủ của `check_base_url`."""
    value = _read_plain(env, name)
    if value is not None:
        check_base_url(value, name)
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


def _read_seconds(env: Mapping[str, str], name: str) -> float | None:
    """Số giây dương hữu hạn, hoặc `None` khi chưa đặt; sai ⇒ `ConfigError` chỉ nêu tên biến."""
    raw = _read(env, name)
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} không hợp lệ: cần một số dương (giây)") from None
    if not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{name} không hợp lệ: cần một số dương hữu hạn (giây)")
    return value


def _timeout(env: Mapping[str, str]) -> float:
    value = _read_seconds(env, ENV_LLM_TIMEOUT_S)
    return DEFAULT_TIMEOUT_S if value is None else value


def _read_int(env: Mapping[str, str], name: str, *, low: int, high: int | None = None) -> int | None:
    """Số nguyên thập phân (chỉ chữ số ASCII) trong `[low, high]`, hoặc `None` khi chưa đặt.

    Sai dạng hay ngoài khoảng ⇒ `ConfigError` chỉ nêu tên biến (không kẹp giá trị, ADR-009 mục 6).
    """
    raw = _read(env, name)
    if raw is None:
        return None
    bounds = f"từ {low} đến {high}" if high is not None else f"từ {low} trở lên"
    if ASCII_DIGITS.fullmatch(raw) is None:
        raise ConfigError(f"{name} không hợp lệ: cần một số nguyên {bounds}")
    value = int(raw)
    if value < low or (high is not None and value > high):
        raise ConfigError(f"{name} không hợp lệ: cần một số nguyên {bounds}")
    return value


def _read_vlm_mode(env: Mapping[str, str]) -> VlmMode:
    """`HOLOCRON_VLM_MODE`: `off` (mặc định) hoặc `images_only`; giá trị khác ⇒ `ConfigError` chỉ nêu tên biến."""
    raw = _read(env, ENV_VLM_MODE)
    if raw is None:
        return DEFAULT_VLM_MODE
    for mode in VLM_MODES:
        if raw == mode:
            return mode
    raise ConfigError(f"{ENV_VLM_MODE} không hợp lệ: chỉ nhận off hoặc images_only")


def is_url_like(value: str) -> bool:
    """Chuỗi trông như URL hay tên miền: có `://`, bắt đầu bằng `www.`, hoặc không có khoảng trắng mà có dấu chấm
    nằm giữa hai ký tự khác dấu chấm (`api.example.com`, `127.0.0.1`). Luật cố ý rộng: nhãn hợp lệ như "OpenAI" hay
    "Máy chủ trường" không bị ảnh hưởng; nhãn có số phiên bản liền như "v1.5" bị từ chối, viết "v1 5" hay thêm
    khoảng trắng nếu cần."""
    lowered = value.lower()
    return "://" in lowered or lowered.startswith("www.") or DOMAIN_LIKE.fullmatch(value) is not None


def _read_data_recipient(env: Mapping[str, str], api_key: SecretStr | None) -> str | None:
    """`HOLOCRON_VLM_DATA_RECIPIENT`: chữ thuần ≤ 80 ký tự, không dạng URL, không chứa khoá VLM.

    Sai ⇒ `ConfigError` chỉ nêu tên biến (không nêu giá trị).
    """
    value = _read_plain(env, ENV_VLM_DATA_RECIPIENT)
    if value is None:
        return None
    if len(value) > VLM_DATA_RECIPIENT_MAX_CHARS:
        raise ConfigError(f"{ENV_VLM_DATA_RECIPIENT} không hợp lệ: tối đa {VLM_DATA_RECIPIENT_MAX_CHARS} ký tự")
    if is_url_like(value):
        raise ConfigError(f"{ENV_VLM_DATA_RECIPIENT} không hợp lệ: là nhãn chữ thuần, không phải địa chỉ hay URL")
    if api_key is not None and api_key.get_secret_value() in value:
        raise ConfigError(f"{ENV_VLM_DATA_RECIPIENT} không hợp lệ: không được chứa khoá")
    return value


def _load_llm(source: Mapping[str, str]) -> LlmSettings:
    model_extract = _read_plain(source, ENV_LLM_MODEL_EXTRACT)
    return LlmSettings(
        base_url=_read_base_url(source, ENV_LLM_BASE_URL),
        api_key=_read_key(source, ENV_LLM_API_KEY),
        model_extract=model_extract,
        model_author=_read_plain(source, ENV_LLM_MODEL_AUTHOR) or model_extract,
        timeout_s=_timeout(source),
    )


def _load_vlm(source: Mapping[str, str]) -> tuple[VlmSettings | None, tuple[str, ...], VlmMode]:
    """`(cấu hình VLM, tên các biến còn thiếu, HOLOCRON_VLM_MODE)`; thiếu biến bắt buộc thì cấu hình là `None`.

    Khoá là bắt buộc trừ khi `HOLOCRON_VLM_BASE_URL` là loopback; chưa có base URL thì chưa biết loại endpoint nên
    khoá vẫn tính là bắt buộc. Mọi biến VLM được kiểm cả khi VLM chưa đủ cấu hình. `HOLOCRON_VLM_MODE` không ảnh
    hưởng việc VLM đã cấu hình hay chưa.
    """
    vlm_base_url = _read_base_url(source, ENV_VLM_BASE_URL)
    vlm_key = _read_key(source, ENV_VLM_API_KEY)
    vlm_model = _read_plain(source, ENV_VLM_MODEL)
    mode = _read_vlm_mode(source)
    timeout_s = _read_seconds(source, ENV_VLM_TIMEOUT_S)
    concurrency = _read_int(source, ENV_VLM_CONCURRENCY, low=VLM_CONCURRENCY_MIN, high=VLM_CONCURRENCY_MAX)
    max_edge_px = _read_int(source, ENV_VLM_MAX_EDGE_PX, low=1)
    max_image_bytes = _read_int(source, ENV_VLM_MAX_IMAGE_BYTES, low=1)
    data_recipient = _read_data_recipient(source, vlm_key)
    local = vlm_base_url is not None and is_loopback_url(vlm_base_url)
    values = (vlm_base_url, vlm_key, vlm_model)
    required = (True, not local, True)
    missing = tuple(
        name
        for name, value, needed in zip(VLM_REQUIRED_VARIABLES, values, required, strict=True)
        if needed and value is None
    )
    if missing or vlm_base_url is None or vlm_model is None:
        return None, missing, mode
    settings = VlmSettings(
        base_url=vlm_base_url,
        api_key=vlm_key,
        model=vlm_model,
        timeout_s=timeout_s if timeout_s is not None else (VLM_TIMEOUT_LOOPBACK_S if local else VLM_TIMEOUT_CLOUD_S),
        concurrency=concurrency
        if concurrency is not None
        else (VLM_CONCURRENCY_LOOPBACK if local else VLM_CONCURRENCY_CLOUD),
        max_edge_px=DEFAULT_VLM_MAX_EDGE_PX if max_edge_px is None else max_edge_px,
        max_image_bytes=DEFAULT_VLM_MAX_IMAGE_BYTES if max_image_bytes is None else max_image_bytes,
        data_recipient=data_recipient,
    )
    return settings, (), mode


def load_runtime_config(env: Mapping[str, str] | None = None) -> RuntimeConfig:
    """Đọc cấu hình từ `env` (mặc định `os.environ`); `env` truyền tường minh để test không phụ thuộc máy chạy.

    Biến có giá trị sai ⇒ `ConfigError` (chỉ nêu tên biến).
    """
    source = os.environ if env is None else env
    llm = _load_llm(source)
    vlm, missing, mode = _load_vlm(source)
    return RuntimeConfig(llm=llm, vlm=vlm, vlm_missing=missing, vlm_mode=mode)


def load_runtime_config_lenient(env: Mapping[str, str] | None = None) -> RuntimeConfig:
    """Như `load_runtime_config`, nhưng nhóm LLM hay VLM có biến sai thì coi như nhóm đó chưa cấu hình thay vì ném lỗi.

    Nhóm LLM sai ⇒ `LlmSettings()` (mọi biến coi như chưa đặt, `llm_configured = false`); nhóm VLM sai ⇒ `vlm = None`
    và `vlm_missing` gồm cả ba biến VLM, `vlm_mode = "off"` (mọi biến `HOLOCRON_VLM_*`, kể cả biến mới của ADR-009, thuộc
    nhóm VLM). Log một cảnh báo chỉ gồm thông điệp của `ConfigError` (tên biến, không bao giờ có giá trị).
    """
    source = os.environ if env is None else env
    try:
        llm = _load_llm(source)
    except ConfigError as exc:
        logger.warning("cấu hình LLM không hợp lệ, coi như chưa cấu hình: %s", exc)
        llm = LlmSettings()
    mode: VlmMode = DEFAULT_VLM_MODE
    try:
        vlm, missing, mode = _load_vlm(source)
    except ConfigError as exc:
        logger.warning("cấu hình VLM không hợp lệ, coi như chưa cấu hình: %s", exc)
        vlm, missing = None, VLM_REQUIRED_VARIABLES
    return RuntimeConfig(llm=llm, vlm=vlm, vlm_missing=missing, vlm_mode=mode)
