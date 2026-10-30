"""Adapter duy nhất gọi pypdfium2 (ADR-008 mục 1, 4).

pypdfium2 không kèm thông tin kiểu (không có `py.typed`), nên module được nạp bằng `importlib` và MỌI giá trị
lấy từ nó được kiểm bằng `isinstance` rồi chuyển sang kiểu của Holocron trước khi rời module này.
Chỉ chạy trong worker có giới hạn (`_pdf_worker.py`), không bao giờ trong tiến trình FastAPI.

An toàn: không gọi `init_forms()` (không khởi tạo form), wheel PDFium không có V8/XFA nên không chạy JavaScript,
không đọc file đính kèm, liên kết hay nội dung nhúng; chỉ đọc lớp chữ và khung bao của đối tượng trang, và render
trang ra ảnh xám cho OCR (tầng 2, không vẽ form và annotation; không giải nén ảnh nhúng trực tiếp).
Khoá ảnh template (`page_image_keys`, sửa đổi 2026-10-30) băm dữ liệu THÔ của luồng ảnh (chưa giải mã), có trần byte
kiểm TRƯỚC khi cấp bộ nhớ đệm.
"""

from __future__ import annotations

import ctypes
import hashlib
import importlib
import itertools
from collections.abc import Iterable
from types import ModuleType
from typing import Literal, Self

from .config import DEFAULT_RENDER_SHRINK_FACTOR, PdfReaderConfig
from .protocol import ImageKey, RawPdfPage
from .raster import POINTS_PER_INCH, GrayImage, pack_rows, render_scale
from .template_images import READ_ERRORS, PageImageKeys, TemplateFilter, image_key
from .text_layer import PdfBox, PdfChar, group_lines, normalize_box

OpenFailure = Literal["PASSWORD_REQUIRED", "OPEN_FAILED"]

# Giới hạn hiệu năng: số hộp ảnh tối đa của một trang khi tính diện tích hợp (`union_area_ratio`, O(n² log n)).
# Vượt trần thì dùng cận trên là tổng diện tích, nghiêng về OCR (có người xem lại). Trang scan cắt thành dải vẫn đúng
# vì các dải không chồng nhau (tổng = hợp).
MAX_UNION_BOXES = 256

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


def _page_area(page_box: PdfBox) -> float:
    left, bottom, right, top = page_box
    return max((right - left) * (top - bottom), 1e-9)


def _clip_boxes(boxes: list[PdfBox], page_box: PdfBox) -> list[PdfBox]:
    """Cắt các hộp theo khổ trang, bỏ hộp rỗng hoặc nằm hẳn ngoài trang."""
    left, bottom, right, top = page_box
    clipped: list[PdfBox] = []
    for bl, bb, br, bt in boxes:
        box = (max(bl, left), max(bb, bottom), min(br, right), min(bt, top))
        if box[2] > box[0] and box[3] > box[1]:
            clipped.append(box)
    return clipped


def _area_ratio(boxes: list[PdfBox], page_box: PdfBox) -> float:
    """Tổng diện tích các hộp (cắt theo khổ trang) chia diện tích trang, chặn ở 1 (cận trên của độ phủ)."""
    total = sum((br - bl) * (bt - bb) for bl, bb, br, bt in _clip_boxes(boxes, page_box))
    return min(1.0, total / _page_area(page_box))


def _covered_length(spans: list[tuple[float, float]]) -> float:
    """Độ dài hợp của các đoạn `[low, high]` đã sắp theo `low`."""
    covered = 0.0
    current_low, current_high = spans[0]
    for low, high in spans[1:]:
        if low > current_high:
            covered += current_high - current_low
            current_low, current_high = low, high
        else:
            current_high = max(current_high, high)
    return covered + current_high - current_low


def union_area_ratio(boxes: list[PdfBox], page_box: PdfBox) -> float:
    """Diện tích HỢP của các hộp (cắt theo khổ trang) chia diện tích trang, ∈ [0, 1] (ADR-008 mục 6).

    Hai ảnh chồng lên nhau chỉ được tính một lần, nên độ phủ không bị thổi lên bởi ảnh xếp lớp (ảnh nền khung slide
    vẽ hai lần, ảnh đè lên nền). Quét theo các dải dọc giữa hai mép trái/phải liên tiếp: O(n² log n) với n hộp. Quá
    `MAX_UNION_BOXES` hộp thì trả tổng diện tích chặn ở 1 (`_area_ratio`), là cận trên: trang nhiều ảnh bị coi là
    phủ nhiều hơn thực tế, nghiêng về OCR (có người xem lại) thay vì giữ lớp chữ. Không tính vùng cắt (clip path)
    của ảnh: hộp là khung bao của đối tượng ảnh.
    """
    clipped = _clip_boxes(boxes, page_box)
    if len(clipped) > MAX_UNION_BOXES:
        return _area_ratio(clipped, page_box)
    edges = sorted({x for box in clipped for x in (box[0], box[2])})
    total = 0.0
    for x_low, x_high in itertools.pairwise(edges):
        spans = sorted((box[1], box[3]) for box in clipped if box[0] <= x_low and box[2] >= x_high)
        if spans:
            total += (x_high - x_low) * _covered_length(spans)
    return min(1.0, total / _page_area(page_box))


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

    def read_page(self, index: int, templates: TemplateFilter | None = None) -> RawPdfPage:
        """Dữ liệu thô của trang; có `templates` thì thêm độ phủ của các ảnh không phải template.

        `templates` là tập ảnh template của lượt quét (`template_images.TemplateFilter.from_scan`); None ⇒
        `content_image_area_ratio` là None (không có ảnh template, độ phủ như cũ).
        """
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
            images, path_boxes = self._object_boxes(page)
            content_ratio = None
            if templates is not None:
                content_boxes = self._content_image_boxes(images, page_box, rotation, templates)
                content_ratio = union_area_ratio(content_boxes, page_box)
        finally:
            backend.call(page, "close")
        return RawPdfPage(
            page_index=index,
            width_pt=width_pt,
            height_pt=height_pt,
            lines=group_lines(chars, page_box, rotation),
            image_area_ratio=union_area_ratio([box for _, box in images], page_box),
            path_area_ratio=_area_ratio(path_boxes, page_box),
            content_image_area_ratio=content_ratio,
        )

    def page_image_keys(self, index: int, config: PdfReaderConfig) -> PageImageKeys | None:
        """Khoá các ảnh nhúng của trang `index` cho lượt quét ảnh template; không render, không đọc lớp chữ.

        Trang có quá `template_scan_max_images_per_page` ảnh ⇒ None (vượt trần). Ảnh có dữ liệu thô rỗng hoặc lớn hơn
        `template_scan_max_image_bytes` không có khoá. Lỗi đọc trang hay ảnh được ném ra (lượt quét thành `error`).
        """
        backend = self._backend
        page = backend.call(self._handle, "get_page", index)
        try:
            page_box = _as_box(backend.call(page, "get_bbox"))
            rotation = _as_int(backend.call(page, "get_rotation"))
            objects = backend.call(page, "get_objects", [backend.image_object_type])
            if not isinstance(objects, Iterable):
                raise PdfiumDataError("get_objects phải trả iterable")
            keys: list[ImageKey | None] = []
            hashed_bytes = 0
            for obj in objects:
                if len(keys) >= config.template_scan_max_images_per_page:
                    return None
                box = _as_box(backend.call(obj, "get_bounds"))
                step, max_bytes = config.template_image_bbox_step, config.template_scan_max_image_bytes
                key, hashed = self._image_key(obj, box, page_box, rotation, step, max_bytes)
                keys.append(key)
                hashed_bytes += hashed
            return PageImageKeys(keys=keys, hashed_bytes=hashed_bytes)
        finally:
            backend.call(page, "close")

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

    def _object_boxes(self, page: object) -> tuple[list[tuple[object, PdfBox]], list[PdfBox]]:
        """(đối tượng ảnh kèm hộp, hộp đường vẽ); đối tượng ảnh chỉ dùng được khi trang còn mở."""
        backend = self._backend
        image_type, path_type = backend.image_object_type, backend.path_object_type
        images: list[tuple[object, PdfBox]] = []
        paths: list[PdfBox] = []
        objects = backend.call(page, "get_objects", [image_type, path_type])
        if not isinstance(objects, Iterable):
            raise PdfiumDataError("get_objects phải trả iterable")
        for obj in objects:
            kind = _as_int(getattr(obj, "type", None))
            box = _as_box(backend.call(obj, "get_bounds"))
            if kind == image_type:
                images.append((obj, box))
            else:
                paths.append(box)
        return images, paths

    def _content_image_boxes(
        self, images: list[tuple[object, PdfBox]], page_box: PdfBox, rotation: int, templates: TemplateFilter
    ) -> list[PdfBox]:
        """Hộp các ảnh không phải template; trang có quá `max_images_per_page` ảnh giữ mọi ảnh (không loại ảnh nào).

        Khoá được tính đúng như lượt quét; ảnh không có khoá (quá trần byte, dữ liệu rỗng, đọc lỗi) là ảnh nội dung.
        """
        if len(images) > templates.max_images_per_page:
            return [box for _, box in images]
        content: list[PdfBox] = []
        for obj, box in images:
            try:
                key, _ = self._image_key(obj, box, page_box, rotation, templates.bbox_step, templates.max_image_bytes)
            except READ_ERRORS:
                key = None
            if key is None or key not in templates.keys:
                content.append(box)
        return content

    def _image_key(
        self, obj: object, box: PdfBox, page_box: PdfBox, rotation: int, step: float, max_bytes: int
    ) -> tuple[ImageKey | None, int]:
        """(khoá của ảnh, số byte đã băm); dữ liệu thô rỗng hoặc lớn hơn `max_bytes` ⇒ (None, 0), không băm."""
        backend = self._backend
        digest = backend.image_digest(obj, max_bytes)
        if digest is None:
            return None, 0
        size = backend.call(obj, "get_px_size")
        if not isinstance(size, tuple | list) or len(size) != 2:
            raise PdfiumDataError("get_px_size phải trả (width, height)")
        metadata = backend.call(obj, "get_metadata")
        filters = backend.call(obj, "get_filters")
        if not isinstance(filters, list) or not all(isinstance(name, str) for name in filters):
            raise PdfiumDataError("get_filters phải trả danh sách chuỗi")
        key = image_key(
            digest[0],
            _as_int(size[0]),
            _as_int(size[1]),
            _as_int(getattr(metadata, "bits_per_pixel", None)),
            [str(name) for name in filters],
            normalize_box(box, page_box, rotation),
            step,
        )
        return key, digest[1]


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

    def image_digest(self, image: object, max_bytes: int) -> tuple[str, int] | None:
        """(SHA-256 hex, số byte) của dữ liệu thô của luồng ảnh; rỗng hoặc lớn hơn `max_bytes` ⇒ None.

        Hỏi độ dài trước, kiểm trần, rồi mới cấp bộ nhớ đệm và chép dữ liệu (không giải mã bộ lọc nào).
        """
        raw_image: object = getattr(image, "raw", None)
        size = _as_int(self._raw.FPDFImageObj_GetImageDataRaw(raw_image, None, 0))
        if size < 1 or size > max_bytes:
            return None
        buffer = (ctypes.c_ubyte * size)()
        if _as_int(self._raw.FPDFImageObj_GetImageDataRaw(raw_image, buffer, size)) != size:
            raise PdfiumDataError("độ dài dữ liệu ảnh thay đổi giữa hai lần đọc")
        return hashlib.sha256(memoryview(buffer)).hexdigest(), size

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


# PDF một trang viết sẵn để làm nóng PDFium: một dòng chữ bằng font Type1 chuẩn KHÔNG nhúng (Helvetica, WinAnsi),
# giống font của PDF mẫu trong test. Bảng xref khớp từng byte (kiểm trong tests/test_pdf_worker_warmup.py).
WARMUP_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 50] /Contents 4 0 R"
    b" /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    b"4 0 obj\n<< /Length 50 >>\nstream\nBT /F1 12 Tf 10 20 Td (Holocron 0123456789) Tj ET\nendstream\nendobj\n"
    b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>\nendobj\n"
    b"xref\n0 6\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000115 00000 n \n"
    b"0000000240 00000 n \n"
    b"0000000339 00000 n \n"
    b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n436\n%%EOF\n"
)


def warm_up(backend: PdfiumBackend) -> RawPdfPage:
    """Đọc trang của `WARMUP_PDF` theo đúng đường trích lớp chữ của trang thật.

    Mục đích: trả trước chi phí khởi tạo một lần của PDFium (nạp font, ánh xạ font hệ thống cho font không nhúng)
    thay vì tính vào hạn của trang đầu tiên. Không đọc file nào: tài liệu là hằng trong bộ nhớ.
    Mở thất bại ⇒ `PdfiumDataError`.
    """
    opened = backend.open(WARMUP_PDF)
    if isinstance(opened, str):
        raise PdfiumDataError(f"không mở được PDF làm nóng: {opened}")
    with opened:
        return opened.read_page(0)
