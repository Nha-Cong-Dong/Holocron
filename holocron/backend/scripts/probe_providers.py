"""Đo thực tế các nhà cung cấp tương thích OpenAI để chọn model cho vai VLM (đọc ảnh) và LLM (cấu trúc văn bản ngắn).

Chạy NGOÀI pytest, opt-in (CLAUDE.md §2.2); không đổi code sản phẩm, không thêm dependency. Mặc định `--dry-run`: chỉ
in kế hoạch request, KHÔNG gọi mạng; chỉ gửi khi có `--live`.

Nhà cung cấp đọc từ biến môi trường theo tiền tố P ∈ {HOLOCRON_LLM, HOLOCRON_VLM, HOLOCRON_PROBE_GEMINI,
HOLOCRON_PROBE_CUSTOM}: `<P>_BASE_URL`, `<P>_API_KEY`, `<P>_MODELS` (tuỳ chọn, phân tách dấu phẩy). Với HOLOCRON_LLM
model lấy từ `HOLOCRON_LLM_MODELS`, không có thì `HOLOCRON_LLM_MODEL_EXTRACT` + `HOLOCRON_LLM_MODEL_AUTHOR`; với
HOLOCRON_VLM từ `HOLOCRON_VLM_MODELS`, không có thì `HOLOCRON_VLM_MODEL`. Hai tiền tố trùng base URL và khoá được gộp.
Nhà cung cấp có tiền tố HOLOCRON_LLM coi là gateway của ban tổ chức (hạn mức nhỏ): không gửi ảnh lớn.
Nhà cung cấp không khai báo model chỉ được liệt kê `GET /models`.

Mỗi (nhà cung cấp, model): (c) ảnh nhỏ tự sinh (PDF dựng trong bộ nhớ bằng pypdfium2, chữ+số tiếng Việt, render PNG)
hỏi chép lại chữ; (b) một câu hỏi văn bản ngắn; mỗi nhà cung cấp: (a) `GET /models`; (d) ảnh lớn ~2 MB (không gửi tới
gateway). Thứ tự ưu tiên trong trần `--max-requests` (mặc định 10, đếm cả `GET /models` và mọi retry): (c), (b), (a), (d).

Kết quả: `results.json` và `results.md` trong `--out` (từ chối ghi vào trong repo). Không in khoá, base URL (chỉ host)
hay phản hồi quá 200 ký tự; mọi chuỗi ghi ra qua `redact` (thay mọi chuỗi con dài ≥ 4 của khoá bằng `***`).
"""

from __future__ import annotations

import argparse
import base64
import http.client
import io
import json
import math
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from email.message import Message
from pathlib import Path
from typing import Protocol

from pydantic import SecretStr

from holocron_core.domain.llm import ChatMessage, LlmUpstreamError
from holocron_core.infrastructure.document_readers.raster import GrayImage, encode_png_gray
from holocron_core.infrastructure.llm_client import OpenAiChatClient, Transport, _NoRedirect
from holocron_core.infrastructure.llm_config import ConfigError, check_base_url, is_valid_key

REPO_ROOT = Path(__file__).resolve().parents[3]
MAX_KEY_LENGTH = 512
INK_THRESHOLD = 128
READ_CHUNK = 65536
DEFAULT_MAX_REQUESTS = 10
TIMEOUT_S = 90.0
MAX_BODY_BYTES = 2_000_000
MAX_SHOWN = 200
MIN_SECRET_SUBSTRING = 4
MASK = "***"

# Nhà cung cấp công khai đã biết: ảnh lớn (d) chỉ gửi tới đây, tới host loopback, hoặc host mở tay bằng
# `--allow-big-image-host`. Mọi host khác (kể cả host của `HOLOCRON_LLM_BASE_URL`) coi là gateway hạn mức nhỏ.
PUBLIC_HOSTS = frozenset(
    {
        "generativelanguage.googleapis.com",
        "api.openai.com",
        "api.anthropic.com",
        "openrouter.ai",
        "api.groq.com",
        "api.mistral.ai",
        "api.together.xyz",
        "api.deepseek.com",
    }
)
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
PREFIXES = ("HOLOCRON_LLM", "HOLOCRON_VLM", "HOLOCRON_PROBE_GEMINI", "HOLOCRON_PROBE_CUSTOM")

TRUTH_TEXT = "Hóa đơn 4721: Tiếng Việt, Nguyễn Văn Ạ 1089"
ASCII_TRUTH_TEXT = "Hoa don 4721: Tieng Viet, Nguyen Van A 1089"
FONT_CANDIDATES = (
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)
IMAGE_PROMPT = "Chép lại CHÍNH XÁC dòng chữ trong ảnh, mỗi ký tự một lần, không giải thích, không thêm gì khác."
TEXT_PROMPT = "Trả lời chỉ bằng một chữ số: 2 + 3 bằng mấy?"
BIG_IMAGE_PROMPT = "Nêu một từ mô tả ảnh này."
BIG_IMAGE_SIDE = 2048

TABLE_HEADER = (
    "| Nhà cung cấp | Host | Model | Nhận ảnh? | Chép ảnh | Độ trễ ảnh (s) | Token ảnh (vào/ra) | Văn bản đúng? "
    "| Độ trễ văn bản (s) | Token văn bản | Giới hạn ảnh | 429 | retry-after | Lỗi |"
)
_URL_RE = re.compile(r"https?://\S+")


class RequestCapReached(LlmUpstreamError):
    """Đã dùng hết trần request thật của lần chạy."""


# --------------------------------------------------------------------------------------------------------------------
# Che khoá
# --------------------------------------------------------------------------------------------------------------------


def secret_windows(keys: Sequence[str]) -> frozenset[str]:
    """Mọi cửa sổ 4 ký tự liên tiếp của các khoá (O(n) mỗi khoá)."""
    width = MIN_SECRET_SUBSTRING
    return frozenset(key[start : start + width] for key in keys for start in range(len(key) - width + 1))


def mask_secrets(text: str, keys: Sequence[str]) -> str:
    """Thay mọi đoạn bị phủ bởi một cửa sổ 4 ký tự của khoá (kể cả 4 ký tự cuối) bằng `***`, một lượt O(len(text))."""
    windows = secret_windows(keys)
    if not windows:
        return text
    width = MIN_SECRET_SUBSTRING
    covered = [False] * len(text)
    for start in range(len(text) - width + 1):
        if text[start : start + width] in windows:
            covered[start : start + width] = [True] * width
    out: list[str] = []
    for index, char in enumerate(text):
        if not covered[index]:
            out.append(char)
        elif index == 0 or not covered[index - 1]:
            out.append(MASK)
    return "".join(out)


def redact(text: str, keys: Sequence[str]) -> str:
    """Che khoá (mọi chuỗi con dài ≥ 4) bằng `***`; URL đầy đủ thành `<url>`."""
    return _URL_RE.sub("<url>", mask_secrets(text, keys))


def shown(text: str, keys: Sequence[str]) -> str:
    """Che khoá TRƯỚC, rồi mới cắt còn 200 ký tự."""
    return redact(text, keys)[:MAX_SHOWN]


def redact_tree(value: object, keys: Sequence[str]) -> object:
    """Che mọi chuỗi (giá trị, và khoá dict) trong cấu trúc JSON-able."""
    if isinstance(value, str):
        return redact(value, keys)
    if isinstance(value, dict):
        return {redact(str(k), keys): redact_tree(v, keys) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_tree(v, keys) for v in value]
    return value


# --------------------------------------------------------------------------------------------------------------------
# Đọc nhà cung cấp từ biến môi trường
# --------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Provider:
    prefixes: tuple[str, ...]
    base_url: str = field(repr=False)
    api_key: str = field(repr=False)
    models: tuple[str, ...]
    big_image_allowed: bool = False

    @property
    def host(self) -> str:
        return (urllib.parse.urlsplit(self.base_url).hostname or "?").lower()

    @property
    def gateway(self) -> bool:
        """Mặc định an toàn: không gửi ảnh lớn trừ khi host công khai đã biết, loopback hoặc được mở tay."""
        return not self.big_image_allowed

    @property
    def label(self) -> str:
        return "+".join(prefix.removeprefix("HOLOCRON_").removeprefix("PROBE_").lower() for prefix in self.prefixes)


@dataclass(frozen=True)
class Skipped:
    prefix: str
    missing: tuple[str, ...]


def normalize_url(url: str) -> str:
    """Scheme và host viết thường, bỏ `/` cuối (để gộp hai tiền tố trỏ cùng một endpoint)."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}"


def _host_of(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def _split(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _models(prefix: str, env: Mapping[str, str]) -> list[str]:
    listed = _split(env.get(f"{prefix}_MODELS", ""))
    if listed:
        return listed
    if prefix == "HOLOCRON_LLM":
        return _split(",".join(env.get(f"HOLOCRON_LLM_MODEL_{role}", "") for role in ("EXTRACT", "AUTHOR")))
    if prefix == "HOLOCRON_VLM":
        return _split(env.get("HOLOCRON_VLM_MODEL", ""))
    return []


def load_providers(env: Mapping[str, str], allow_big_hosts: Sequence[str] = ()) -> tuple[list[Provider], list[Skipped]]:
    """Nhà cung cấp đủ biến (đã gộp theo base URL + khoá) và các tiền tố bị bỏ qua kèm TÊN biến thiếu/sai."""
    merged: dict[tuple[str, str], tuple[list[str], list[str]]] = {}
    skipped: list[Skipped] = []
    for prefix in PREFIXES:
        url_name, key_name = f"{prefix}_BASE_URL", f"{prefix}_API_KEY"
        url, key = env.get(url_name, "").strip(), env.get(key_name, "").strip()
        problems = [name for name, value in ((url_name, url), (key_name, key)) if not value]
        if not problems and url:
            try:
                check_base_url(url, url_name)
            except ConfigError:
                problems.append(f"{url_name} (không hợp lệ)")
        if not problems and not (is_valid_key(key) and len(key) <= MAX_KEY_LENGTH):
            problems.append(f"{key_name} (không hợp lệ)")
        if problems:
            skipped.append(Skipped(prefix, tuple(problems)))
            continue
        prefixes, models = merged.setdefault((normalize_url(url), key), ([], []))
        prefixes.append(prefix)
        models.extend(m for m in _models(prefix, env) if m not in models)
    llm_url = env.get("HOLOCRON_LLM_BASE_URL", "").strip()
    gateway_host = _host_of(llm_url) if llm_url else ""
    allowed = {host.lower() for host in allow_big_hosts}
    providers = [
        Provider(
            tuple(prefixes),
            url,
            key,
            tuple(models),
            big_image_allowed=_host_of(url) in allowed
            or (_host_of(url) != gateway_host and (_host_of(url) in PUBLIC_HOSTS or _host_of(url) in LOOPBACK)),
        )
        for (url, key), (prefixes, models) in merged.items()
    ]
    return providers, skipped


# --------------------------------------------------------------------------------------------------------------------
# Ảnh tự sinh
# --------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SmallImage:
    png: bytes
    truth: str
    accents: bool


def _pdf_with_text(text: str, font_bytes: bytes | None) -> bytes | None:
    """PDF một dòng chữ; `None` khi pdfium không nạp được font hoặc không dựng được text object."""
    import ctypes

    import pypdfium2 as pdfium
    from pypdfium2 import raw

    doc = pdfium.PdfDocument.new()
    page = doc.new_page(595, 120)
    if font_bytes is not None:
        buffer = (ctypes.c_uint8 * len(font_bytes)).from_buffer_copy(font_bytes)
        font = raw.FPDFText_LoadFont(doc.raw, buffer, len(font_bytes), raw.FPDF_FONT_TRUETYPE, 1)
    else:
        font = raw.FPDFText_LoadStandardFont(doc.raw, b"Helvetica")
    if not font:
        return None
    obj = raw.FPDFPageObj_CreateTextObj(doc.raw, font, 24.0)
    if not obj:
        return None
    wide = ctypes.create_string_buffer((text + "\0").encode("utf-16-le"))
    if not raw.FPDFText_SetText(obj, ctypes.cast(wide, ctypes.POINTER(ctypes.c_ushort))):
        return None
    raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 20, 50)
    raw.FPDFPage_InsertObject(page.raw, obj)
    raw.FPDFPage_GenerateContent(page.raw)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _render_gray(pdf_bytes: bytes) -> tuple[bytes, int, int]:
    import pypdfium2 as pdfium

    bitmap = pdfium.PdfDocument(pdf_bytes)[0].render(scale=2.0, grayscale=True)
    width, height, stride = bitmap.width, bitmap.height, bitmap.stride
    buffer = bytes(bitmap.buffer)
    return b"".join(buffer[row * stride : row * stride + width] for row in range(height)), width, height


def make_small_image(font_paths: Sequence[str] = FONT_CANDIDATES) -> SmallImage:
    """PNG xám của một PDF tự dựng: có dấu tiếng Việt nếu nhúng được font hệ thống, không thì chỉ ASCII.

    Font không nạp được, text object NULL hoặc ảnh không có điểm tối (không có chữ) ⇒ lùi về chữ ASCII (Helvetica).
    """
    attempts: list[tuple[str, bytes | None]] = []
    for candidate in font_paths:
        path = Path(candidate)
        if path.is_file():
            attempts.append((TRUTH_TEXT, path.read_bytes()))
            break
    attempts.append((ASCII_TRUTH_TEXT, None))
    for text, font_bytes in attempts:
        pdf = _pdf_with_text(text, font_bytes)
        if pdf is None:
            continue
        pixels, width, height = _render_gray(pdf)
        if min(pixels) >= INK_THRESHOLD:
            continue
        return SmallImage(encode_png_gray(GrayImage(pixels, width, height, 144.0)), text, font_bytes is not None)
    raise RuntimeError("không dựng được ảnh thử")


def make_big_image(side: int = BIG_IMAGE_SIDE) -> bytes:
    """PNG xám nhiễu giả ngẫu nhiên (seed cố định), 16 mức xám để nén còn cỡ ~2 MB."""
    import random

    noise = random.Random(20261101).randbytes(side * side).translate(bytes(value & 0xF0 for value in range(256)))
    return encode_png_gray(GrayImage(noise, side, side, 72.0))


# --------------------------------------------------------------------------------------------------------------------
# Gửi request có đếm
# --------------------------------------------------------------------------------------------------------------------


class ReadableBody(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


class HttpResponse(Protocol):
    status: int
    headers: Message

    def read(self, size: int = -1, /) -> bytes: ...

    def close(self) -> None: ...


class Opener(Protocol):
    """Phần của `OpenerDirector` mà `CountingSender` dùng (test thay bằng opener giả)."""

    def open(self, fullurl: urllib.request.Request, *, timeout: float) -> HttpResponse: ...


class CountingSender:
    """Transport có trần: mọi request thật (kể cả `GET /models` và retry) đi qua đây; vượt trần ⇒ `RequestCapReached`."""

    def __init__(
        self,
        max_requests: int,
        *,
        opener: Opener | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_requests = max_requests
        self.count = 0
        self.last_status = 0
        self.last_retry_after: str | None = None
        self.last_body = b""
        self.rate_limited = 0
        self._monotonic = monotonic
        self._opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)

    @property
    def remaining(self) -> int:
        return self.max_requests - self.count

    def _read(self, response: ReadableBody, deadline: float) -> bytes:
        """Đọc theo khối, tối đa `MAX_BODY_BYTES`; quá hạn tổng ⇒ `TimeoutError`."""
        chunks: list[bytes] = []
        total = 0
        while total < MAX_BODY_BYTES:
            if self._monotonic() > deadline:
                raise TimeoutError("quá hạn tổng thời gian khi đọc phản hồi")
            chunk = response.read(min(READ_CHUNK, MAX_BODY_BYTES - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)

    def __call__(self, request: urllib.request.Request, timeout_s: float) -> tuple[int, bytes]:
        if self.count >= self.max_requests:
            raise RequestCapReached("đã hết trần request của lần chạy", attempts=self.count)
        self.count += 1
        self.last_status, self.last_body, self.last_retry_after = 0, b"", None
        deadline = self._monotonic() + timeout_s
        try:
            response = self._opener.open(request, timeout=timeout_s)
        except urllib.error.HTTPError as exc:
            status, headers = exc.code, exc.headers
            try:
                body = self._read(exc, deadline)
            finally:
                exc.close()
        else:
            status, headers = int(response.status), response.headers
            try:
                body = self._read(response, deadline)
            finally:
                response.close()
        self.last_status, self.last_body = status, body
        self.last_retry_after = headers.get("retry-after") if headers is not None else None
        if status == 429:
            self.rate_limited += 1
        return status, body


def _auth_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Accept": "application/json", "User-Agent": "holocron-probe/1"}


def _dig(value: object, *path: str | int) -> object:
    for step in path:
        if isinstance(step, int):
            if not isinstance(value, list) or not -len(value) <= step < len(value):
                return None
            value = value[step]
        else:
            if not isinstance(value, dict):
                return None
            value = value.get(step)
    return value


def _content_text(body: bytes) -> tuple[str, tuple[int, int] | None]:
    """`choices[0].message.content` (chuỗi hoặc mảng phần text) và `usage`; phản hồi lạ ⇒ chuỗi rỗng."""
    try:
        data: object = json.loads(body)
    except ValueError:
        return "", None
    content = _dig(data, "choices", 0, "message", "content")
    if isinstance(content, list):
        content = "".join(str(_dig(part, "text") or "") for part in content)
    prompt, completion = _dig(data, "usage", "prompt_tokens"), _dig(data, "usage", "completion_tokens")
    usage = (prompt, completion) if isinstance(prompt, int) and isinstance(completion, int) else None
    return (content if isinstance(content, str) else ""), usage


def _error_text(status: int, body: bytes) -> str:
    """Thông điệp lỗi gọn: mã HTTP và `error.message` (hoặc đầu body); chưa che khoá."""
    detail = ""
    try:
        data: object = json.loads(body)
        found = _dig(data, "error", "message") or _dig(data, "message") or _dig(data, "error")
        detail = found if isinstance(found, str) else ""
    except ValueError:
        detail = body.decode("utf-8", errors="replace")
    return f"HTTP {status}" + (f": {' '.join(detail.split())}" if detail else "")


def image_payload(model: str, prompt: str, png: bytes, max_tokens: int) -> bytes:
    url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": url}}],
            }
        ],
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def digits_of(text: str) -> str:
    return "".join(char for char in text if char.isdigit())


def classify_copy(answer: str, truth: str) -> str:
    """`exact` (khớp sau NFC+khoảng trắng), `digits` (đúng dãy chữ số nhưng sai chữ), `no`."""
    if normalize(answer).rstrip(".") == normalize(truth):
        return "exact"
    if digits_of(answer) == digits_of(truth) and digits_of(truth):
        return "digits"
    return "no"


# --------------------------------------------------------------------------------------------------------------------
# Kế hoạch
# --------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Task:
    kind: str  # "image" (c) | "text" (b) | "list" (a) | "big" (d)
    provider: int
    model: str | None

    def describe(self, providers: Sequence[Provider]) -> str:
        p = providers[self.provider]
        names = {"image": "(c) ảnh nhỏ", "text": "(b) văn bản", "list": "(a) GET /models", "big": "(d) ảnh lớn"}
        return f"{names[self.kind]:<16} {p.label} @ {p.host}" + (f" model={self.model}" if self.model else "")


def build_plan(providers: Sequence[Provider]) -> list[Task]:
    """Thứ tự ưu tiên: (c) mọi nhà cung cấp, rồi (b), (a), (d) (không gửi tới gateway)."""
    pairs = [(i, model) for i, p in enumerate(providers) for model in p.models]
    plan = [Task("image", i, m) for i, m in pairs]
    plan += [Task("text", i, m) for i, m in pairs]
    plan += [Task("list", i, None) for i in range(len(providers))]
    plan += [Task("big", i, m) for i, m in pairs if not providers[i].gateway]
    return plan


# --------------------------------------------------------------------------------------------------------------------
# Chạy thật
# --------------------------------------------------------------------------------------------------------------------


@dataclass
class Row:
    provider: str
    host: str
    model: str
    gateway: bool
    image_accepted: bool | None = None
    image_match: str | None = None
    image_latency_s: float | None = None
    image_tokens: tuple[int, int] | None = None
    image_error: str | None = None
    text_ok: bool | None = None
    text_latency_s: float | None = None
    text_tokens: tuple[int, int] | None = None
    text_error: str | None = None
    big_bytes: int | None = None
    big_status: str | None = None
    big_latency_s: float | None = None
    retry_after: list[str] = field(default_factory=list)
    http_429: int = 0
    not_run: list[str] = field(default_factory=list)


@dataclass
class Listing:
    provider: str
    host: str
    count: int | None = None
    sample: list[str] = field(default_factory=list)
    error: str | None = None


def _models_listing(body: bytes) -> list[str]:
    try:
        data: object = json.loads(body)
    except ValueError:
        return []
    items = _dig(data, "data")
    if not isinstance(items, list):
        items = _dig(data, "models")
    if not isinstance(items, list):
        return []
    ids = [_dig(item, "id") or _dig(item, "name") for item in items]
    return [i for i in ids if isinstance(i, str)]


class Prober:
    def __init__(
        self,
        providers: Sequence[Provider],
        sender: CountingSender,
        *,
        small: SmallImage,
        big: Callable[[], bytes] = make_big_image,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.providers = providers
        self.sender = sender
        self.small = small
        self._big = big
        self._clock = clock
        self.keys = [p.api_key for p in providers]
        self.rows: dict[tuple[int, str], Row] = {}
        self.listings: dict[int, Listing] = {}

    def row(self, index: int, model: str) -> Row:
        p = self.providers[index]
        return self.rows.setdefault((index, model), Row(p.label, p.host, model, p.gateway))

    def _post(self, index: int, payload: bytes) -> tuple[int, bytes, float]:
        p = self.providers[index]
        request = urllib.request.Request(
            p.base_url.rstrip("/") + "/chat/completions",
            data=payload,
            method="POST",
            headers={**_auth_headers(p.api_key), "Content-Type": "application/json"},
        )
        start = self._clock()
        status, body = self.sender(request, TIMEOUT_S)
        return status, body, self._clock() - start

    def _note_limits(self, row: Row) -> None:
        if self.sender.last_status == 429:
            row.http_429 += 1
        if self.sender.last_retry_after:
            row.retry_after.append(shown(self.sender.last_retry_after, self.keys))

    def run(self, plan: Sequence[Task]) -> None:
        for number, task in enumerate(plan):
            if self.sender.remaining <= 0:
                for skipped in plan[number:]:
                    if skipped.model is not None:
                        self.row(skipped.provider, skipped.model).not_run.append(skipped.kind)
                    else:
                        p = self.providers[skipped.provider]
                        listing = self.listings.setdefault(skipped.provider, Listing(p.label, p.host))
                        listing.error = "chưa chạy: hết trần request"
                break
            self._run_one(task)

    def _run_one(self, task: Task) -> None:
        try:
            if task.kind == "image" and task.model:
                self._image(task.provider, task.model)
            elif task.kind == "text" and task.model:
                self._text(task.provider, task.model)
            elif task.kind == "list":
                self._list(task.provider)
            elif task.kind == "big" and task.model:
                self._big_image(task.provider, task.model)
        except (OSError, ValueError, http.client.HTTPException, LlmUpstreamError) as exc:
            message = shown(f"{type(exc).__name__}: {exc}", self.keys)
            if task.kind == "list":
                p = self.providers[task.provider]
                self.listings.setdefault(task.provider, Listing(p.label, p.host)).error = message
            elif task.model:
                row = self.row(task.provider, task.model)
                if task.kind == "image":
                    row.image_error = message
                elif task.kind == "text":
                    row.text_error = message
                else:
                    row.big_status = message

    def _image(self, index: int, model: str) -> None:
        row = self.row(index, model)
        status, body, latency = self._post(index, image_payload(model, IMAGE_PROMPT, self.small.png, 300))
        row.image_latency_s = round(latency, 2)
        self._note_limits(row)
        if not 200 <= status <= 299:
            row.image_accepted = False
            row.image_error = shown(_error_text(status, body), self.keys)
            return
        answer, usage = _content_text(body)
        row.image_accepted = True
        row.image_tokens = usage
        row.image_match = classify_copy(answer, self.small.truth)
        if row.image_match == "no":
            row.image_error = "chép sai: " + shown(answer, self.keys)

    def _text(self, index: int, model: str) -> None:
        p, row = self.providers[index], self.row(index, model)
        client = OpenAiChatClient(
            base_url=p.base_url,
            api_key=SecretStr(p.api_key),
            model=model,
            timeout_s=TIMEOUT_S,
            retry_max=0,
            transport=_as_transport(self.sender),
        )
        start = self._clock()
        try:
            result = client.complete([ChatMessage(role="user", content=TEXT_PROMPT)], max_tokens=300)
        except LlmUpstreamError as exc:
            row.text_latency_s = round(self._clock() - start, 2)
            self._note_limits(row)
            detail = (
                _error_text(self.sender.last_status, self.sender.last_body) if self.sender.last_status else str(exc)
            )
            row.text_ok = False
            row.text_error = shown(detail, self.keys)
            return
        row.text_latency_s = round(self._clock() - start, 2)
        self._note_limits(row)
        row.text_ok = re.search(r"\b5\b", result.content) is not None
        row.text_tokens = (
            (result.usage.prompt_tokens, result.usage.completion_tokens) if result.usage is not None else None
        )
        if not row.text_ok:
            row.text_error = "trả lời sai: " + shown(result.content, self.keys)

    def _list(self, index: int) -> None:
        p = self.providers[index]
        listing = self.listings.setdefault(index, Listing(p.label, p.host))
        request = urllib.request.Request(p.base_url.rstrip("/") + "/models", headers=_auth_headers(p.api_key))
        status, body = self.sender(request, TIMEOUT_S)
        if not 200 <= status <= 299:
            listing.error = shown(_error_text(status, body), self.keys)
            return
        ids = _models_listing(body)
        listing.count = len(ids)
        listing.sample = [shown(i, self.keys) for i in ids[:30]]

    def _big_image(self, index: int, model: str) -> None:
        row = self.row(index, model)
        png = self._big()
        row.big_bytes = len(png)
        status, body, latency = self._post(index, image_payload(model, BIG_IMAGE_PROMPT, png, 50))
        row.big_latency_s = round(latency, 2)
        self._note_limits(row)
        row.big_status = "chấp nhận" if 200 <= status <= 299 else shown(_error_text(status, body), self.keys)


def _as_transport(sender: CountingSender) -> Transport:
    return sender


# --------------------------------------------------------------------------------------------------------------------
# Báo cáo
# --------------------------------------------------------------------------------------------------------------------

_MATCH_RANK = {"exact": 0, "digits": 1}


def _latency_key(value: float | None) -> float:
    return math.inf if value is None else value


def recommend(rows: Sequence[Row]) -> dict[str, str]:
    """Chọn model theo vai: vlm = chép ảnh đúng nhất rồi nhanh nhất; llm = trả lời văn bản đúng rồi nhanh nhất."""
    vision = [r for r in rows if r.image_match in _MATCH_RANK]
    vision.sort(key=lambda r: (_MATCH_RANK.get(r.image_match or "", 9), _latency_key(r.image_latency_s)))
    text = sorted((r for r in rows if r.text_ok), key=lambda r: _latency_key(r.text_latency_s))
    return {
        "vlm": f"{vision[0].provider}/{vision[0].model} (chép ảnh: {vision[0].image_match})"
        if vision
        else "chưa có ứng viên",
        "llm": f"{text[0].provider}/{text[0].model}" if text else "chưa có ứng viên",
    }


def _cell(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "có" if value else "không"
    if isinstance(value, tuple):
        return f"{value[0]}/{value[1]}"
    return str(value).replace("|", "/")


def render_markdown(
    rows: Sequence[Row], listings: Sequence[Listing], skipped: Sequence[Skipped], meta: dict[str, object]
) -> str:
    lines = [
        "# Kết quả probe nhà cung cấp",
        "",
        f"- Request đã gửi: {meta['requests']}/{meta['max_requests']}; gặp 429: {meta['rate_limited']}",
        f"- Chữ trong ảnh nhỏ: có dấu tiếng Việt = {_cell(meta['accents'])}",
        f"- Lỗi giữa chừng: {_cell(meta['fatal'])}",
        "",
        TABLE_HEADER,
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        big = _cell(r.big_status) if r.big_bytes is None else f"{r.big_bytes // 1024} KiB: {r.big_status}"
        errors = "; ".join(e for e in (r.image_error, r.text_error) if e) or (
            "chưa chạy: " + ",".join(r.not_run) if r.not_run else "—"
        )
        lines.append(
            "| "
            + " | ".join(
                _cell(v)
                for v in (
                    r.provider,
                    r.host,
                    r.model,
                    r.image_accepted,
                    r.image_match,
                    r.image_latency_s,
                    r.image_tokens,
                    r.text_ok,
                    r.text_latency_s,
                    r.text_tokens,
                    "không thử (host chưa được phép)" if r.gateway and r.big_bytes is None else big,
                    r.http_429,
                    ",".join(r.retry_after) or None,
                    errors,
                )
            )
            + " |"
        )
    lines += ["", "## GET /models", ""]
    for item in listings:
        detail = item.error or f"{item.count} model; đầu danh sách: {', '.join(item.sample[:10])}"
        lines.append(f"- {item.provider} @ {item.host}: {detail}")
    if skipped:
        lines += ["", "## Bỏ qua", ""] + [f"- {s.prefix}: skipped: {', '.join(s.missing)}" for s in skipped]
    pick = recommend(rows)
    lines += ["", "## Đề xuất", "", f"- vai `vlm`: {pick['vlm']}", f"- vai `llm`: {pick['llm']}", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------------------------


def main(
    argv: list[str] | None = None, env: Mapping[str, str] | None = None, sender: CountingSender | None = None
) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    import os

    environment = os.environ if env is None else env
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="thư mục ghi kết quả (ngoài repo)")
    parser.add_argument(
        "--allow-big-image-host",
        action="append",
        default=[],
        metavar="HOST",
        help="cho phép gửi ảnh lớn (d) tới host này (mặc định chỉ host công khai đã biết và loopback)",
    )
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="gửi request thật")
    mode.add_argument("--dry-run", action="store_true", help="chỉ in kế hoạch (mặc định)")
    args = parser.parse_args(argv)

    out_dir = args.out.resolve()
    if out_dir.is_relative_to(REPO_ROOT):
        print(f"Từ chối ghi kết quả vào trong repo: {out_dir}", file=sys.stderr)
        return 2
    if args.max_requests < 1:
        print("--max-requests phải ≥ 1", file=sys.stderr)
        return 2

    providers, skipped = load_providers(environment, args.allow_big_image_host)
    keys = [p.api_key for p in providers]
    plan = build_plan(providers)
    for item in skipped:
        print(f"skipped {item.prefix}: {', '.join(item.missing)}")
    for task in plan[: args.max_requests]:
        print(shown("plan: " + task.describe(providers), keys))
    if len(plan) > args.max_requests:
        print(f"plan: còn {len(plan) - args.max_requests} request ngoài trần {args.max_requests}, sẽ không chạy")
    if not providers:
        print("OPEN: chưa có nhà cung cấp nào đủ biến (xem các dòng skipped)")
    if not args.live:
        print("dry-run: không gọi mạng; thêm --live để gửi")
        return 0
    if not providers:
        return 0

    small = make_small_image()
    if not small.accents:
        print("OPEN: chưa thử dấu tiếng Việt (không nhúng được font hệ thống)")
    counting = sender or CountingSender(args.max_requests)
    prober = Prober(providers, counting, small=small)
    completed = False
    try:
        prober.run(plan)
        completed = True
    finally:
        _write_results(prober, counting, out_dir, skipped, args.max_requests, small.accents, completed)
    return 0


def _write_results(
    prober: Prober,
    counting: CountingSender,
    out_dir: Path,
    skipped: Sequence[Skipped],
    max_requests: int,
    accents: bool,
    completed: bool,
) -> None:
    """Ghi results.json và results.md (cả khi chạy dở); mọi chuỗi qua `redact`."""
    keys = prober.keys
    rows = list(prober.rows.values())
    listings = list(prober.listings.values())
    meta: dict[str, object] = {
        "requests": counting.count,
        "max_requests": max_requests,
        "rate_limited": counting.rate_limited,
        "accents": accents,
        "fatal": None if completed else "chạy dở: ngoại lệ ngoài dự kiến",
    }
    document = redact_tree(
        {
            "meta": meta,
            "rows": [asdict(r) for r in rows],
            "listings": [asdict(x) for x in listings],
            "skipped": [asdict(s) for s in skipped],
            "recommend": recommend(rows),
        },
        keys,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown = redact(render_markdown(rows, listings, skipped, meta), keys)
    (out_dir / "results.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"Đã ghi {out_dir / 'results.json'} và {out_dir / 'results.md'}")


if __name__ == "__main__":
    raise SystemExit(main())
