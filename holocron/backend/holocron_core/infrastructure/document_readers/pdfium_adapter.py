"""Adapter duy nhất gọi pypdfium2 (ADR-008 mục 1, 4).

pypdfium2 không kèm thông tin kiểu (không có `py.typed`), nên module được nạp bằng `importlib` và MỌI giá trị
lấy từ nó được kiểm bằng `isinstance` rồi chuyển sang kiểu của Holocron trước khi rời module này.
Chỉ chạy trong worker có giới hạn (`_pdf_worker.py`), không bao giờ trong tiến trình FastAPI.

An toàn: không gọi `init_forms()` (không khởi tạo form), wheel PDFium không có V8/XFA nên không chạy JavaScript,
không đọc file đính kèm, liên kết hay nội dung nhúng; chỉ đọc lớp chữ và khung bao của đối tượng trang, và render
trang ra ảnh xám cho OCR (tầng 2, không vẽ form và annotation; không giải nén ảnh nhúng trực tiếp).
"""

from __future__ import annotations

import ctypes
import importlib
from collections.abc import Iterable
from types import ModuleType
from typing import Literal, Self

from .config import DEFAULT_RENDER_SHRINK_FACTOR
from .protocol import RawPdfPage
from .raster import POINTS_PER_INCH, GrayImage, pack_rows, render_scale
from .text_layer import PdfBox, PdfChar, group_lines

OpenFailure = Literal["PASSWORD_REQUIRED", "OPEN_FAILED"]

_HIGH_SURROGATES = range(0xD800, 0xDC00)
_LOW_SURROGATES = range(0xDC00, 0xE000)


class PdfiumDataError(RuntimeError):
    """pypdfium2 trả giá trị không đúng kiểu mong đợi."""


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PdfiumDataError(f"cần số nguyên, nhận {type(value).__name__}")
    return value


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PdfiumDataError(f"cần số thực, nhận {type(value).__name__}")
    return float(value)


def _as_box(value: object) -> PdfBox:
    if not isinstance(value, tuple | list) or len(value) != 4:
        raise PdfiumDataError("cần bộ 4 số (left, bottom, right, top)")
    left, bottom, right, top = (_as_float(v) for v in value)
    return (left, bottom, right, top)


def _area_ratio(boxes: list[PdfBox], page_box: PdfBox) -> float:
    """Tổng diện tích các hộp (cắt theo khổ trang) chia diện tích trang, chặn ở 1."""
    left, bottom, right, top = page_box
    page_area = max((right - left) * (top - bottom), 1e-9)
    total = 0.0
    for bl, bb, br, bt in boxes:
        width = max(0.0, min(br, right) - max(bl, left))
        height = max(0.0, min(bt, top) - max(bb, bottom))
        total += width * height
    return min(1.0, total / page_area)


def decode_code_units(codes: list[int]) -> list[tuple[str, int]]:
    """Ghép mã PDFium thành ký tự, trả (ký tự, chỉ số mã đầu tiên).

    PDFium trên Windows trả UTF-16 (ký tự ngoài BMP, ví dụ dải toán U+1D400–1D7FF, thành cặp surrogate); trên Linux
    trả mã đầy đủ. Surrogate lẻ hoặc mã ngoài Unicode thành U+FFFD (được tính là ký tự rác trong `text_score`).
    """
    out: list[tuple[str, int]] = []
    index = 0
    while index < len(codes):
        code, step = codes[index], 1
        if code in _HIGH_SURROGATES and index + 1 < len(codes) and codes[index + 1] in _LOW_SURROGATES:
            code, step = 0x10000 + ((code - 0xD800) << 10) + (codes[index + 1] - 0xDC00), 2
        valid = 0 <= code <= 0x10FFFF and code not in _HIGH_SURROGATES and code not in _LOW_SURROGATES
        out.append((chr(code) if valid else "\ufffd", index))
        index += step
    return out


class PdfiumDocument:
    """Tài liệu đã mở; đóng bằng `close()` hoặc dùng `with`."""

    def __init__(self, backend: PdfiumBackend, handle: object) -> None:
        self._backend = backend
        self._handle = handle

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._backend.call(self._handle, "close")

    def page_count(self) -> int:
        return _as_int(self._backend.call(self._handle, "__len__"))

    def read_page(self, index: int) -> RawPdfPage:
        backend = self._backend
        page = backend.call(self._handle, "get_page", index)
        try:
            page_box = _as_box(backend.call(page, "get_bbox"))
            rotation = _as_int(backend.call(page, "get_rotation"))
            size = backend.call(page, "get_size")
            if not isinstance(size, tuple | list) or len(size) != 2:
                raise PdfiumDataError("get_size phải trả (width, height)")
            width_pt, height_pt = _as_float(size[0]), _as_float(size[1])
            chars = self._read_chars(page)
            image_boxes, path_boxes = self._object_boxes(page)
        finally:
            backend.call(page, "close")
        return RawPdfPage(
            page_index=index,
            width_pt=width_pt,
            height_pt=height_pt,
            lines=group_lines(chars, page_box, rotation),
            image_area_ratio=_area_ratio(image_boxes, page_box),
            path_area_ratio=_area_ratio(path_boxes, page_box),
        )

    def render_gray(
        self, index: int, dpi: int, max_pixels: int, *, shrink_factor: float = DEFAULT_RENDER_SHRINK_FACTOR
    ) -> GrayImage:
        """Render trang theo hướng hiển thị ra ảnh xám 8 bit, `dpi` giảm dần khi vượt `max_pixels` (ADR-008 mục 4)."""
        backend = self._backend
        page = backend.call(self._handle, "get_page", index)
        try:
            size = backend.call(page, "get_size")
            if not isinstance(size, tuple | list) or len(size) != 2:
                raise PdfiumDataError("get_size phải trả (width, height)")
            scale = render_scale(_as_float(size[0]), _as_float(size[1]), dpi, max_pixels, shrink_factor=shrink_factor)
            bitmap = backend.call(page, "render", scale=scale, grayscale=True, may_draw_forms=False, draw_annots=False)
            try:
                return backend.gray_image(bitmap, scale * POINTS_PER_INCH, max_pixels)
            finally:
                backend.call(bitmap, "close")
        finally:
            backend.call(page, "close")

    def _read_chars(self, page: object) -> list[PdfChar]:
        backend = self._backend
        textpage = backend.call(page, "get_textpage")
        try:
            raw_textpage: object = getattr(textpage, "raw", None)
            count = _as_int(backend.call(textpage, "count_chars"))
            codes = [backend.unicode_at(raw_textpage, i) for i in range(count)]
            boxes = [backend.charbox(raw_textpage, i) for i in range(count)]
            return [PdfChar(text=text, box=boxes[first]) for text, first in decode_code_units(codes)]
        finally:
            backend.call(textpage, "close")

    def _object_boxes(self, page: object) -> tuple[list[PdfBox], list[PdfBox]]:
        backend = self._backend
        image_type, path_type = backend.image_object_type, backend.path_object_type
        images: list[PdfBox] = []
        paths: list[PdfBox] = []
        objects = backend.call(page, "get_objects", [image_type, path_type])
        if not isinstance(objects, Iterable):
            raise PdfiumDataError("get_objects phải trả iterable")
        for obj in objects:
            kind = _as_int(getattr(obj, "type", None))
            box = _as_box(backend.call(obj, "get_bounds"))
            (images if kind == image_type else paths).append(box)
        return images, paths


class PdfiumBackend:
    """Bọc module `pypdfium2` và `pypdfium2.raw` đã nạp."""

    def __init__(self, pdfium: ModuleType, raw: ModuleType) -> None:
        self._pdfium = pdfium
        self._raw = raw
        error = getattr(pdfium, "PdfiumError", None)
        if not (isinstance(error, type) and issubclass(error, Exception)):
            raise PdfiumDataError("pypdfium2 thiếu PdfiumError")
        self._error: type[Exception] = error
        self.password_error_code = _as_int(getattr(raw, "FPDF_ERR_PASSWORD", None))
        self.image_object_type = _as_int(getattr(raw, "FPDF_PAGEOBJ_IMAGE", None))
        self.path_object_type = _as_int(getattr(raw, "FPDF_PAGEOBJ_PATH", None))
        self.gray_bitmap_format = _as_int(getattr(raw, "FPDFBitmap_Gray", None))

    @classmethod
    def load(cls) -> PdfiumBackend:
        return cls(importlib.import_module("pypdfium2"), importlib.import_module("pypdfium2.raw"))

    def call(self, target: object, method: str, *args: object, **kwargs: object) -> object:
        """Gọi một method của đối tượng pypdfium2; kết quả là `object`, người gọi phải tự kiểm kiểu."""
        fn = getattr(target, method)
        if not callable(fn):
            raise PdfiumDataError(f"{method} không gọi được")
        result: object = fn(*args, **kwargs)
        return result

    def gray_image(self, bitmap: object, dpi: float, max_pixels: int) -> GrayImage:
        """Chép điểm ảnh của `PdfBitmap` xám (1 byte mỗi điểm ảnh) ra bytes, bỏ phần đệm cuối dòng."""
        width = _as_int(getattr(bitmap, "width", None))
        height = _as_int(getattr(bitmap, "height", None))
        stride = _as_int(getattr(bitmap, "stride", None))
        if _as_int(getattr(bitmap, "format", None)) != self.gray_bitmap_format:
            raise PdfiumDataError("bitmap không phải ảnh xám")
        if width < 1 or height < 1 or width * height > max_pixels or stride < width:
            raise PdfiumDataError("kích thước bitmap ngoài giới hạn")
        buffer = getattr(bitmap, "buffer", None)
        if not isinstance(buffer, ctypes.Array):
            raise PdfiumDataError("bitmap không có vùng nhớ điểm ảnh")
        data = ctypes.string_at(ctypes.addressof(buffer), min(ctypes.sizeof(buffer), stride * height))
        return GrayImage(pixels=pack_rows(data, width, height, stride), width=width, height=height, dpi=dpi)

    def open(self, data: bytes) -> PdfiumDocument | OpenFailure:
        """Mở tài liệu từ bytes (không giữ file); cần mật khẩu ⇒ "PASSWORD_REQUIRED", không thử phá."""
        document_class = self._pdfium.PdfDocument
        try:
            handle: object = document_class(data)
        except self._error as exc:
            code = getattr(exc, "err_code", None)
            return "PASSWORD_REQUIRED" if code == self.password_error_code else "OPEN_FAILED"
        return PdfiumDocument(self, handle)

    def unicode_at(self, raw_textpage: object, index: int) -> int:
        return _as_int(self._raw.FPDFText_GetUnicode(raw_textpage, index))

    def charbox(self, raw_textpage: object, index: int) -> PdfBox | None:
        """Hộp chặt của ký tự; PDFium không cho hộp (ký tự tự chèn, lỗi) ⇒ None."""
        left, right, bottom, top = ctypes.c_double(), ctypes.c_double(), ctypes.c_double(), ctypes.c_double()
        ok = self._raw.FPDFText_GetCharBox(raw_textpage, index, left, right, bottom, top)
        if not ok:
            return None
        box = (left.value, bottom.value, right.value, top.value)
        if box[2] - box[0] <= 0.0 and box[3] - box[1] <= 0.0:
            return None
        return box
