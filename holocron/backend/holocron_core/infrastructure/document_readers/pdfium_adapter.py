"""Adapter duy nhất gọi pypdfium2 (ADR-008 mục 1, 4).

pypdfium2 không kèm thông tin kiểu (không có `py.typed`), nên module được nạp bằng `importlib` và MỌI giá trị
lấy từ nó được kiểm bằng `isinstance` rồi chuyển sang kiểu của Holocron trước khi rời module này.
Chỉ chạy trong worker có giới hạn (`_pdf_worker.py`), không bao giờ trong tiến trình FastAPI.

An toàn: không gọi `init_forms()` (không khởi tạo form), wheel PDFium không có V8/XFA nên không chạy JavaScript,
không đọc file đính kèm, liên kết hay nội dung nhúng; chỉ đọc lớp chữ và khung bao của đối tượng trang, và render
trang ra ảnh xám cho OCR (tầng 2, không vẽ form và annotation; không giải nén ảnh nhúng trực tiếp).
Khoá ảnh template (`page_image_keys`, sửa đổi 2026-10-30) không giải mã ảnh: băm dữ liệu THÔ của luồng ảnh (chưa
giải mã bộ lọc nào, có trần byte kiểm TRƯỚC khi cấp bộ nhớ đệm), cùng kích thước điểm ảnh và tên bộ lọc đọc từ từ điển
của luồng ảnh. Không gọi metadata ảnh của PDFium (`FPDFImageObj_GetImageMetadata` có thể giải mã JPX, JBIG2).

GĐ0 (sửa đổi 2026-10-31, spec §5.7 "Khử nhiễu trang"): đối tượng trang được duyệt đệ quy qua form XObject
(`_walk`, tới độ sâu `pdf_form_max_depth`, tối đa `page_objects_max` đối tượng), hộp của ảnh và đường vẽ là hộp sau
mọi ma trận form, thứ tự vẽ là thứ tự duyệt sâu. Trang được render màu ở `hidden_text_render_dpi` sau khi tắt mọi đối
tượng chữ TRONG BỘ NHỚ (`FPDFPageObj_SetIsActive`, không ghi file, không sinh lại nội dung) để đo nền thật của chữ và
dHash của ảnh nội dung. Quyết định (nền, chữ ẩn, chữ bị che, lớp OCR của scan) là hàm thuần ở `page_objects`.

Font (ADR-010 mục 5): chỉ đường đọc trang lớp chữ (`read_page`) đọc font của đối tượng chữ chứa từng ký tự
(`_FontReader`, cache theo đối tượng chữ và theo đối tượng font, tên đọc bằng bộ đệm cố định `FONT_NAME_BUFFER_BYTES`);
lượt quét template không đọc font. Lỗi khi đọc font chỉ làm ký tự không có font hay cỡ chữ, không bao giờ ném ra.
"""

from __future__ import annotations

import ctypes
import hashlib
import importlib
import itertools
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from types import ModuleType
from typing import Literal, Self

from .config import DEFAULT_RENDER_SHRINK_FACTOR, PdfReaderConfig
from .page_objects import (
    IDENTITY,
    INVISIBLE_RENDER_MODES,
    MAX_RENDER_MODE,
    STROKE_ONLY_RENDER_MODES,
    Matrix,
    ObjectKind,
    OcclusionIndex,
    PageObject,
    RgbImage,
    background_paths,
    compose,
    contrast_ratio,
    contrast_verdict,
    dhash,
    is_scan_text_layer,
    median_color,
    pixel_box,
    transform_box,
    union_box,
)
from .protocol import MAX_OWN_IMAGES, ImageKey, PathKey, RawFont, RawLineFlag, RawPageImage, RawPdfPage, Rgba
from .raster import POINTS_PER_INCH, GrayImage, pack_rows, render_scale
from .template_images import READ_ERRORS, PageImageKeys, TemplateFilter, image_key, path_key, text_key
from .text_layer import (
    PdfBox,
    PdfChar,
    assign_font_ids,
    cap_boxless_words,
    classify_font,
    clean_font_name,
    effective_font_size,
    group_lines,
    independent_nonws_counts,
    mark_header_footer,
    merge_spaced_chars,
    normalize_box,
)

OpenFailure = Literal["PASSWORD_REQUIRED", "OPEN_FAILED"]

# Giới hạn hiệu năng: số hộp ảnh tối đa của một trang khi tính diện tích hợp (`union_area_ratio`, O(n² log n)).
# Vượt trần thì dùng cận trên là tổng diện tích, nghiêng về OCR (có người xem lại). Trang scan cắt thành dải vẫn đúng
# vì các dải không chồng nhau (tổng = hợp).
MAX_UNION_BOXES = 256

# Bộ đệm cố định đọc tên font (ADR-010 mục 5): không cấp phát theo độ dài tên PDF khai báo; tên dài hơn không vừa bộ
# đệm thì không có tên (PDFium không ghi vào bộ đệm khi nó quá nhỏ).
FONT_NAME_BUFFER_BYTES = 256
# Lỗi khi đọc font của một đối tượng chữ: ký tự không có font/cỡ chữ, trang vẫn đọc bình thường.
FONT_ERRORS = (*READ_ERRORS, ctypes.ArgumentError)

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

    @property
    def backend(self) -> PdfiumBackend:
        return self._backend

    def read_page(
        self, index: int, templates: TemplateFilter | None = None, config: PdfReaderConfig | None = None
    ) -> RawPdfPage:
        """Dữ liệu thô của trang kèm dữ liệu khử nhiễu GĐ0 (spec §5.7); `config` mặc định `PdfReaderConfig()`.

        `templates` là template của lượt quét (`TemplateFilter.from_scan`); None ⇒ `content_image_area_ratio` là
        None (không có ảnh template, độ phủ như cũ), không đường vẽ template, không header/footer.
        """
        settings = PdfReaderConfig() if config is None else config
        backend = self._backend
        page = backend.call(self._handle, "get_page", index)
        try:
            page_box = _as_box(backend.call(page, "get_bbox"))
            rotation = _as_int(backend.call(page, "get_rotation"))
            size = backend.call(page, "get_size")
            if not isinstance(size, tuple | list) or len(size) != 2:
                raise PdfiumDataError("get_size phải trả (width, height)")
            width_pt, height_pt = _as_float(size[0]), _as_float(size[1])
            walked = self._walk(page, settings)
            chars = self._read_chars(page, _FontReader(backend, walked, settings))
            analysis = _PageAnalysis(walked, chars, page_box, rotation, settings, templates)
            analysis.classify(self)
            image = analysis.render_if_needed(self, page)
            analysis.judge_text(image)
            own_images = analysis.own_images(self, image)
        finally:
            backend.call(page, "close")
        nonws_total, hidden_nonws = independent_nonws_counts(chars, analysis.is_dropped_owner)
        fonts, kept = assign_font_ids(analysis.kept_chars())
        lines = group_lines(merge_spaced_chars(kept, settings), page_box, rotation)
        if templates is not None:
            lines = mark_header_footer(lines, templates.text_keys, templates.bbox_step)
        lines, boxless_dropped = cap_boxless_words(lines)
        all_images = [obj.box for obj in walked.images]
        content_ratio = None
        if templates is not None:
            content_ratio = union_area_ratio([obj.box for obj in analysis.content_images], page_box)
        return RawPdfPage(
            page_index=index,
            width_pt=width_pt,
            height_pt=height_pt,
            lines=lines,
            image_area_ratio=union_area_ratio(all_images, page_box),
            path_area_ratio=_area_ratio([obj.box for obj in walked.paths], page_box),
            content_image_area_ratio=content_ratio,
            content_path_area_ratio=_area_ratio([obj.box for obj in analysis.content_paths], page_box),
            own_images=own_images,
            hidden_chars_dropped=analysis.hidden_chars_dropped,
            scan_text_layer=analysis.scan_text_layer,
            objects_over_cap=walked.over_cap,
            fonts=fonts,
            boxless_words_dropped=boxless_dropped,
            pdfium_nonws_total=nonws_total,
            hidden_nonws_dropped=hidden_nonws,
        )

    def page_image_keys(self, index: int, config: PdfReaderConfig) -> PageImageKeys | None:
        """Khoá template của trang `index` cho lượt quét: ảnh, đường vẽ (GĐ0) và dòng chữ (GĐ0); không render.

        Trang có quá `template_scan_max_images_per_page` ảnh ⇒ None (vượt trần). Ảnh có dữ liệu thô rỗng hoặc lớn hơn
        `template_scan_max_image_bytes` không có khoá. Trang có quá `template_scan_max_paths_per_page` đường vẽ: đường
        vẽ của trang không có khoá. Trang vượt trần đối tượng (`page_objects_max`, `pdf_form_max_depth`): ảnh và đường
        vẽ đã duyệt vẫn có khoá. Dòng chữ dựng bằng đúng hàm dựng dòng lúc đọc trang (kể cả ghép ký tự rời) nhưng
        không lọc chữ ẩn. Lỗi đọc trang hay ảnh được ném ra (lượt quét thành `error`).
        """
        backend = self._backend
        page = backend.call(self._handle, "get_page", index)
        try:
            page_box = _as_box(backend.call(page, "get_bbox"))
            rotation = _as_int(backend.call(page, "get_rotation"))
            # Lượt quét không cần đối tượng chữ (dòng chữ lấy từ lớp chữ): chỉ ảnh, đường vẽ và form.
            walked = self._walk(page, config, backend.template_scan_kinds)
            if len(walked.images) > config.template_scan_max_images_per_page:
                return None
            step, max_bytes = config.template_image_bbox_step, config.template_scan_max_image_bytes
            keys: list[ImageKey | None] = []
            hashed_bytes = 0
            for obj in walked.images:
                key, hashed = self.image_key_of(walked.handles[obj.z], obj.box, page_box, rotation, step, max_bytes)
                keys.append(key)
                hashed_bytes += hashed
            paths: tuple[PathKey, ...] = ()
            if len(walked.paths) <= config.template_scan_max_paths_per_page:
                paths = tuple(_path_key(obj, page_box, rotation, step) for obj in walked.paths)
            chars = self._read_chars(page)
        finally:
            backend.call(page, "close")
        lines = group_lines(merge_spaced_chars([char for char, _ in chars], config), page_box, rotation)
        texts = tuple(key for key in (text_key(line.text, line.bbox, step) for line in lines) if key is not None)
        return PageImageKeys(keys=keys, hashed_bytes=hashed_bytes, paths=paths, texts=texts)

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

    def render_rgb_without_text(self, page: object, text_handles: Iterable[object], dpi: int) -> RgbImage:
        """Render màu trang ĐANG MỞ ở `dpi` sau khi tắt mọi đối tượng chữ trong bộ nhớ; bật lại sau khi render."""
        backend = self._backend
        handles = list(text_handles)
        size = backend.call(page, "get_size")
        if not isinstance(size, tuple | list) or len(size) != 2:
            raise PdfiumDataError("get_size phải trả (width, height)")
        scale = dpi / POINTS_PER_INCH
        for handle in handles:
            backend.set_active(handle, active=False)
        try:
            bitmap = backend.call(page, "render", scale=scale, grayscale=False, may_draw_forms=False, draw_annots=False)
            try:
                return backend.rgb_image(bitmap)
            finally:
                backend.call(bitmap, "close")
        finally:
            for handle in handles:
                backend.set_active(handle, active=True)

    def _read_chars(self, page: object, fonts: _FontReader | None = None) -> list[tuple[PdfChar, int | None]]:
        """Ký tự của lớp chữ kèm địa chỉ đối tượng chữ chứa nó (None: ký tự tự sinh hoặc không xác định).

        `fonts` (chỉ đường đọc trang lớp chữ; mặc định tắt) gắn font và cỡ chữ của đối tượng chữ chứa ký tự.
        """
        backend = self._backend
        textpage = backend.call(page, "get_textpage")
        try:
            raw_textpage: object = getattr(textpage, "raw", None)
            count = _as_int(backend.call(textpage, "count_chars"))
            codes = [backend.unicode_at(raw_textpage, i) for i in range(count)]
            boxes = [backend.charbox(raw_textpage, i) for i in range(count)]
            generated = [backend.is_generated(raw_textpage, i) for i in range(count)]
            owners = [backend.text_object_at(raw_textpage, i) for i in range(count)]
            result: list[tuple[PdfChar, int | None]] = []
            for text, first in decode_code_units(codes):
                char = PdfChar(text=text, box=boxes[first], generated=generated[first])
                if fonts is not None and not char.generated:
                    font, size = fonts.char_font(raw_textpage, first, owners[first])
                    char = replace(char, font=font, font_size_pt=size)
                result.append((char, owners[first]))
            return result
        finally:
            backend.call(textpage, "close")

    def _walk(self, page: object, config: PdfReaderConfig, kinds: list[int] | None = None) -> _Walk:
        """Duyệt mọi đối tượng của trang theo thứ tự vẽ, đệ quy qua form XObject, nhân ma trận form (0A).

        `kinds` (lượt quét template) giới hạn loại đối tượng được trả (phải gồm form để nhân ma trận); None ⇒ mọi
        loại. Quá `page_objects_max` đối tượng, hoặc có đối tượng sâu hơn `pdf_form_max_depth` cấp form ⇒ dừng
        duyệt, `over_cap = True` (các đối tượng đã duyệt vẫn được giữ).
        """
        backend = self._backend
        objects = backend.call(page, "get_objects", kinds, max_depth=config.pdf_form_max_depth + 2)
        if not isinstance(objects, Iterable):
            raise PdfiumDataError("get_objects phải trả iterable")
        walk = _Walk()
        stack: list[Matrix] = [IDENTITY]
        for obj in objects:
            level = _as_int(getattr(obj, "level", None))
            if len(walk.objects) >= config.page_objects_max or level > config.pdf_form_max_depth or level < 0:
                walk.over_cap = True
                break
            if level >= len(stack):
                raise PdfiumDataError("đối tượng nằm sâu hơn form chứa nó")
            matrix = stack[level]
            z = len(walk.objects)
            box = transform_box(_as_box(backend.call(obj, "get_bounds")), matrix)
            kind = backend.object_kind(_as_int(getattr(obj, "type", None)))
            raw_obj: object = getattr(obj, "raw", None)
            record = PageObject(kind=kind, z=z, box=box)
            if kind == "form":
                del stack[level + 1 :]
                stack.append(compose(matrix, backend.object_matrix(raw_obj)))
            elif kind == "path":
                fill, stroke = backend.path_colors(raw_obj)
                record = PageObject(
                    kind=kind, z=z, box=box, fill=fill, stroke=stroke, segments=backend.path_segments(raw_obj)
                )
            elif kind == "text":
                mode = backend.text_render_mode(raw_obj)
                color = (
                    backend.stroke_color(raw_obj) if mode in STROKE_ONLY_RENDER_MODES else backend.fill_color(raw_obj)
                )
                record = PageObject(kind=kind, z=z, box=box, render_mode=mode, color=color)
                walk.text_z[backend.address(raw_obj)] = z
                walk.form_matrix[z] = matrix
            walk.objects.append(record)
            walk.handles[z] = obj
        return walk

    def image_key_of(
        self, obj: object, box: PdfBox, page_box: PdfBox, rotation: int, step: float, max_bytes: int
    ) -> tuple[ImageKey | None, int]:
        """(khoá của ảnh, số byte đã băm); dữ liệu thô rỗng hoặc lớn hơn `max_bytes` ⇒ (None, 0), không băm.

        Chỉ đọc dữ liệu thô, kích thước điểm ảnh (`get_px_size`) và tên bộ lọc: không lời gọi nào giải mã ảnh.
        """
        backend = self._backend
        digest = backend.image_digest(obj, max_bytes)
        if digest is None:
            return None, 0
        size = backend.call(obj, "get_px_size")
        if not isinstance(size, tuple | list) or len(size) != 2:
            raise PdfiumDataError("get_px_size phải trả (width, height)")
        filters = backend.call(obj, "get_filters")
        if not isinstance(filters, list) or not all(isinstance(name, str) for name in filters):
            raise PdfiumDataError("get_filters phải trả danh sách chuỗi")
        key = image_key(
            digest[0],
            _as_int(size[0]),
            _as_int(size[1]),
            [str(name) for name in filters],
            normalize_box(box, page_box, rotation),
            step,
        )
        return key, digest[1]


def _path_key(obj: PageObject, page_box: PdfBox, rotation: int, step: float) -> PathKey:
    return path_key(normalize_box(obj.box, page_box, rotation), obj.fill, obj.stroke, obj.segments, step)


@dataclass
class _Walk:
    """Kết quả duyệt đối tượng của một trang (`PdfiumDocument._walk`)."""

    objects: list[PageObject] = field(default_factory=list)
    handles: dict[int, object] = field(default_factory=dict)  # z ⇒ đối tượng pypdfium2 (chỉ dùng khi trang còn mở)
    text_z: dict[int, int] = field(default_factory=dict)  # địa chỉ đối tượng chữ ⇒ z
    form_matrix: dict[int, Matrix] = field(default_factory=dict)  # z của đối tượng chữ ⇒ tích ma trận các form chứa nó
    over_cap: bool = False

    @property
    def images(self) -> list[PageObject]:
        return [obj for obj in self.objects if obj.kind == "image"]

    @property
    def paths(self) -> list[PageObject]:
        return [obj for obj in self.objects if obj.kind == "path"]


class _PageAnalysis:
    """Các bước GĐ0 0A, 0B trên một trang đang mở; quyết định là hàm thuần của `page_objects`."""

    def __init__(
        self,
        walked: _Walk,
        chars: list[tuple[PdfChar, int | None]],
        page_box: PdfBox,
        rotation: int,
        config: PdfReaderConfig,
        templates: TemplateFilter | None,
    ) -> None:
        self.walked = walked
        self.chars = chars
        self.page_box = page_box
        self.rotation = rotation
        self.config = config
        self.templates = templates
        self.content_images: list[PageObject] = list(walked.images)
        self.content_paths: list[PageObject] = list(walked.paths)
        self.template_image_z: set[int] = set()
        self.scan_text_layer = False
        self.hidden_chars_dropped = 0
        self.dropped_z: set[int] = set()
        self.flags_by_z: dict[int, set[RawLineFlag]] = {}
        # Hộp của từng đối tượng chữ: hợp hộp các ký tự của nó (toạ độ trang), hoặc hộp của đối tượng khi không có.
        char_boxes: dict[int, list[PdfBox]] = {}
        for char, owner in chars:
            z = None if owner is None else walked.text_z.get(owner)
            if z is not None and char.box is not None and not char.generated:
                char_boxes.setdefault(z, []).append(char.box)
        self.text_boxes: dict[int, PdfBox] = {}
        for obj in walked.objects:
            if obj.kind == "text":
                self.text_boxes[obj.z] = union_box(char_boxes.get(obj.z, [])) or obj.box

    def _char_z(self, owner: int | None) -> int | None:
        return None if owner is None else self.walked.text_z.get(owner)

    def is_dropped_owner(self, owner: int | None) -> bool:
        """Ký tự của đối tượng chữ `owner` bị loại vì chữ ẩn (0B(1)); gọi sau `judge_text`."""
        z = self._char_z(owner)
        return z is not None and z in self.dropped_z

    def classify(self, document: PdfiumDocument) -> None:
        """Ảnh và đường vẽ template, nền, lớp OCR của PDF scan (0A, 0B(1)). Trang vượt trần: mọi ảnh và đường vẽ là
        nội dung, không có nền."""
        walked, templates = self.walked, self.templates
        if walked.over_cap:
            return
        if templates is not None and len(walked.images) <= templates.max_images_per_page:
            for obj in walked.images:
                try:
                    key, _ = document.image_key_of(
                        walked.handles[obj.z],
                        obj.box,
                        self.page_box,
                        self.rotation,
                        templates.bbox_step,
                        templates.max_image_bytes,
                    )
                except READ_ERRORS:
                    key = None
                if key is not None and key in templates.keys:
                    self.template_image_z.add(obj.z)
        template_paths: set[int] = set()
        if templates is not None and len(walked.paths) <= templates.max_paths_per_page:
            template_paths = {
                obj.z
                for obj in walked.paths
                if _path_key(obj, self.page_box, self.rotation, templates.bbox_step) in templates.path_keys
            }
        backgrounds = background_paths(walked.paths, list(self.text_boxes.items()), self.page_box, self.config)
        self.content_images = [obj for obj in walked.images if obj.z not in self.template_image_z]
        self.content_paths = [obj for obj in walked.paths if obj.z not in template_paths | backgrounds]
        visible = [char for char, _ in self.chars if not char.generated and not char.text.isspace()]
        invisible = [
            char
            for char, owner in self.chars
            if not char.generated and not char.text.isspace() and self._render_mode(owner) in INVISIBLE_RENDER_MODES
        ]
        image_ratio = union_area_ratio([obj.box for obj in walked.images], self.page_box)
        self.scan_text_layer = is_scan_text_layer(len(invisible), len(visible), image_ratio, self.config)

    def _render_mode(self, owner: int | None) -> int | None:
        z = self._char_z(owner)
        return None if z is None else self.walked.objects[z].render_mode

    def _drawn_texts(self) -> list[PageObject]:
        """Đối tượng chữ được vẽ (chế độ 0–7 khác 3, 7; chế độ không đọc được thì không xét) có ký tự trong lớp chữ."""
        owners = {self._char_z(owner) for char, owner in self.chars if not char.generated}
        return [
            obj
            for obj in self.walked.objects
            if obj.kind == "text"
            and obj.z in owners
            and 0 <= obj.render_mode <= MAX_RENDER_MODE
            and obj.render_mode not in INVISIBLE_RENDER_MODES
        ]

    def _denoise_text(self) -> bool:
        return not self.walked.over_cap and not self.scan_text_layer

    def render_if_needed(self, document: PdfiumDocument, page: object) -> RgbImage | None:
        """Ảnh render không có chữ khi cần đo nền chữ hoặc dHash ảnh nội dung; trang vượt trần không render."""
        if self.walked.over_cap:
            return None
        if not self.content_images and not (self._denoise_text() and self._drawn_texts()):
            return None
        texts = [self.walked.handles[obj.z] for obj in self.walked.objects if obj.kind == "text"]
        return document.render_rgb_without_text(page, texts, self.config.hidden_text_render_dpi)

    def _unit(self, box: PdfBox) -> tuple[float, float, float, float]:
        return normalize_box(box, self.page_box, self.rotation)

    def judge_text(self, image: RgbImage | None) -> None:
        """Chữ ẩn (0B(1)) và chữ bị che (0B(2)) cho từng đối tượng chữ được vẽ; trang scan hay vượt trần: không xét.

        Chữ bị che xét TRƯỚC: ảnh render không có chữ vẫn có đối tượng vẽ sau chữ, nên nền đo được của chữ bị che là
        chính vật che. Chữ bị che được GIỮ kèm `TEXT_OCCLUDED` (0B(2)) và không bị loại vì tương phản.
        """
        if not self._denoise_text():
            return
        drawn = self._drawn_texts()
        occluders = [obj for obj in self.walked.objects if obj.kind == "image" or obj.solid_fill]
        index = OcclusionIndex(occluders, self.page_box)
        for obj in drawn:
            box = self.text_boxes[obj.z]
            if index.covered(box, obj.z) >= self.config.occlusion_min_cover:
                self.flags_by_z[obj.z] = {"TEXT_OCCLUDED"}
                continue
            if image is None or obj.color is None:
                continue
            area = pixel_box(self._unit(box), image.width, image.height)
            if area is None:
                continue
            background = median_color(image, area)
            color = (obj.color[0], obj.color[1], obj.color[2])
            verdict = contrast_verdict(contrast_ratio(color, background), self.config)
            if verdict == "drop":
                self.dropped_z.add(obj.z)
            elif verdict == "flag":
                self.flags_by_z[obj.z] = {"LOW_TEXT_CONTRAST"}

    def kept_chars(self) -> list[PdfChar]:
        """Ký tự còn lại sau khi bỏ TRỌN ký tự của đối tượng chữ ẩn (trước khi dựng dòng), kèm cờ của đối tượng."""
        kept: list[PdfChar] = []
        for char, owner in self.chars:
            z = self._char_z(owner)
            if z is not None and z in self.dropped_z:
                if not char.generated:
                    self.hidden_chars_dropped += 1
                continue
            flags = self.flags_by_z.get(z) if z is not None else None
            kept.append(char if not flags else replace(char, flags=frozenset(flags)))
        return kept

    def own_images(self, document: PdfiumDocument, image: RgbImage | None) -> list[RawPageImage]:
        """Ảnh nội dung theo thứ tự vẽ (tối đa `MAX_OWN_IMAGES`, trần của trang), kèm SHA-256 dữ liệu thô và dHash."""
        limit = min(MAX_OWN_IMAGES, self.config.template_scan_max_images_per_page)
        result: list[RawPageImage] = []
        backend = document.backend
        for obj in self.content_images[:limit]:
            unit = self._unit(obj.box)
            try:
                digest = backend.image_digest(self.walked.handles[obj.z], self.config.template_scan_max_image_bytes)
            except READ_ERRORS:
                digest = None
            area = None if image is None else pixel_box(unit, image.width, image.height)
            fingerprint = None if image is None or area is None else dhash(image, area)
            result.append(RawPageImage(bbox=unit, sha256=None if digest is None else digest[0], dhash=fingerprint))
        return result


def _determinant(matrix: Matrix) -> float:
    a, b, c, d, _, _ = matrix
    return a * d - b * c


def _attempt[T](read: Callable[[], T]) -> T | None:
    """Kết quả của một lời gọi đọc font; lỗi ⇒ None (lỗi đọc font không bao giờ làm hỏng trang)."""
    try:
        return read()
    except FONT_ERRORS:
        return None


class _FontReader:
    """Đọc font và cỡ chữ hiệu dụng của đối tượng chữ chứa ký tự, khi dựng ký tự của trang lớp chữ (ADR-010 mục 5).

    Cache theo đối tượng chữ chủ (mỗi đối tượng chữ được đọc tối đa một lượt, kể cả khi có hàng nghìn ký tự) và theo
    đối tượng font (thuộc tính font đọc một lần cho mỗi font của trang). Ký tự mà đối tượng chữ chủ không có trong cây
    đối tượng đã duyệt (trang vượt trần đối tượng) không có font hay cỡ chữ.
    """

    def __init__(self, backend: PdfiumBackend, walked: _Walk, config: PdfReaderConfig) -> None:
        self._backend = backend
        self._walked = walked
        self._config = config
        self._by_owner: dict[int, tuple[RawFont | None, float | None]] = {}
        self._by_font: dict[int, RawFont | None] = {}

    def char_font(self, raw_textpage: object, index: int, owner: int | None) -> tuple[RawFont | None, float | None]:
        if owner is None:
            return None, None
        cached = self._by_owner.get(owner)
        if cached is None:
            cached = self._by_owner[owner] = self._read(raw_textpage, index, owner)
        return cached

    def _read(self, raw_textpage: object, index: int, owner: int) -> tuple[RawFont | None, float | None]:
        z = self._walked.text_z.get(owner)
        if z is None:
            return None, None
        raw_obj: object = getattr(self._walked.handles[z], "raw", None)
        form_matrix = self._walked.form_matrix.get(z)
        size = _attempt(lambda: self._size(raw_textpage, index, raw_obj, form_matrix))
        font = _attempt(lambda: self._backend.text_font(raw_obj))
        return (None if font is None else self._font(font)), size

    def _size(self, raw_textpage: object, index: int, raw_obj: object, form_matrix: Matrix | None) -> float | None:
        if form_matrix is None:
            return None
        backend = self._backend
        determinants = (_determinant(backend.object_matrix(raw_obj)), _determinant(form_matrix))
        return effective_font_size(backend.font_size_at(raw_textpage, index), determinants)

    def _font(self, font: object) -> RawFont | None:
        key = _attempt(lambda: PdfiumBackend.address(font))
        if key is not None and key in self._by_font:
            return self._by_font[key]
        backend = self._backend
        name = _attempt(lambda: clean_font_name(backend.font_name(font)))
        result = _attempt(
            lambda: classify_font(
                name,
                _attempt(lambda: backend.font_weight(font)),
                _attempt(lambda: backend.font_flags(font)),
                _attempt(lambda: backend.font_italic_angle(font)),
                self._config,
            )
        )
        if key is not None:
            self._by_font[key] = result
        return result


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
        self.text_object_type = _as_int(getattr(raw, "FPDF_PAGEOBJ_TEXT", None))
        self.form_object_type = _as_int(getattr(raw, "FPDF_PAGEOBJ_FORM", None))
        self.gray_bitmap_format = _as_int(getattr(raw, "FPDFBitmap_Gray", None))
        self.bgr_bitmap_format = _as_int(getattr(raw, "FPDFBitmap_BGR", None))
        self.template_scan_kinds = [self.image_object_type, self.path_object_type, self.form_object_type]

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

    def is_generated(self, raw_textpage: object, index: int) -> bool:
        """Ký tự do PDFium tự sinh (khoảng trắng, ngắt dòng); lỗi (-1) coi như không tự sinh."""
        return _as_int(self._raw.FPDFText_IsGenerated(raw_textpage, index)) == 1

    def text_object_at(self, raw_textpage: object, index: int) -> int | None:
        """Địa chỉ đối tượng chữ chứa ký tự `index`; ký tự tự sinh hay không xác định ⇒ None."""
        handle: object = self._raw.FPDFText_GetTextObject(raw_textpage, index)
        return self.address(handle) if handle else None

    @staticmethod
    def address(handle: object) -> int:
        """Địa chỉ của một con trỏ ctypes (để so khớp đối tượng giữa lớp chữ và cây đối tượng)."""
        if not isinstance(handle, ctypes._Pointer):
            raise PdfiumDataError("cần con trỏ ctypes của đối tượng trang")
        value = ctypes.cast(handle, ctypes.c_void_p).value
        return 0 if value is None else int(value)

    def object_kind(self, kind: int) -> ObjectKind:
        if kind == self.text_object_type:
            return "text"
        if kind == self.image_object_type:
            return "image"
        if kind == self.path_object_type:
            return "path"
        if kind == self.form_object_type:
            return "form"
        return "other"

    def object_matrix(self, raw_obj: object) -> Matrix:
        matrix = self._raw.FS_MATRIX()
        if not self._raw.FPDFPageObj_GetMatrix(raw_obj, ctypes.byref(matrix)):
            raise PdfiumDataError("không đọc được ma trận của form")
        values = (matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f)
        a, b, c, d, e, f = (_as_float(value) for value in values)
        return (a, b, c, d, e, f)

    def _color(self, getter: str, raw_obj: object) -> Rgba | None:
        red, green, blue, alpha = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        ok = getattr(self._raw, getter)(raw_obj, red, green, blue, alpha)
        if not ok:
            return None
        values = tuple(min(255, max(0, int(channel.value))) for channel in (red, green, blue, alpha))
        return (values[0], values[1], values[2], values[3])

    def fill_color(self, raw_obj: object) -> Rgba | None:
        return self._color("FPDFPageObj_GetFillColor", raw_obj)

    def stroke_color(self, raw_obj: object) -> Rgba | None:
        return self._color("FPDFPageObj_GetStrokeColor", raw_obj)

    def path_colors(self, raw_obj: object) -> tuple[Rgba | None, Rgba | None]:
        """(màu tô khi path có tô, màu viền khi path có viền); không đọc được chế độ vẽ ⇒ (None, None)."""
        fill_mode, stroke = ctypes.c_int(), ctypes.c_int()
        if not self._raw.FPDFPath_GetDrawMode(raw_obj, ctypes.byref(fill_mode), ctypes.byref(stroke)):
            return None, None
        fill = self.fill_color(raw_obj) if fill_mode.value != 0 else None
        line = self.stroke_color(raw_obj) if stroke.value else None
        return fill, line

    def path_segments(self, raw_obj: object) -> int:
        return max(0, _as_int(self._raw.FPDFPath_CountSegments(raw_obj)))

    def text_render_mode(self, raw_obj: object) -> int:
        return _as_int(self._raw.FPDFTextObj_GetTextRenderMode(raw_obj))

    def text_font(self, raw_obj: object) -> object | None:
        """Đối tượng font của đối tượng chữ (`FPDFTextObj_GetFont`); không có ⇒ None."""
        font: object = self._raw.FPDFTextObj_GetFont(raw_obj)
        return font if font else None

    def font_name(self, font: object) -> bytes:
        """Tên gốc của font (UTF-8, có thể có tiền tố subset), đọc bằng bộ đệm CỐ ĐỊNH `FONT_NAME_BUFFER_BYTES`.

        PDFium trả độ dài cần (kể cả NUL) và không ghi gì khi bộ đệm nhỏ hơn; khi đó hay khi lỗi ⇒ `b""`.
        """
        buffer = (ctypes.c_char * FONT_NAME_BUFFER_BYTES)()
        needed = _as_int(self._raw.FPDFFont_GetBaseFontName(font, buffer, FONT_NAME_BUFFER_BYTES))
        if needed < 1 or needed > FONT_NAME_BUFFER_BYTES:
            return b""
        return bytes(buffer)[:needed]

    def font_weight(self, font: object) -> int | None:
        """Weight của font (`FPDFFont_GetWeight`); lỗi (-1) hay âm ⇒ None."""
        weight = _as_int(self._raw.FPDFFont_GetWeight(font))
        return weight if weight >= 0 else None

    def font_flags(self, font: object) -> int | None:
        """Cờ font của PDF (`FPDFFont_GetFlags`); lỗi (-1) ⇒ None."""
        flags = _as_int(self._raw.FPDFFont_GetFlags(font))
        return flags if flags >= 0 else None

    def font_italic_angle(self, font: object) -> int | None:
        angle = ctypes.c_int()
        if not self._raw.FPDFFont_GetItalicAngle(font, ctypes.byref(angle)):
            return None
        return int(angle.value)

    def font_size_at(self, raw_textpage: object, index: int) -> float:
        """Cỡ chữ PDFium của ký tự `index` (`FPDFText_GetFontSize`, chưa nhân ma trận)."""
        return _as_float(self._raw.FPDFText_GetFontSize(raw_textpage, index))

    def set_active(self, handle: object, *, active: bool) -> None:
        raw_obj: object = getattr(handle, "raw", None)
        self._raw.FPDFPageObj_SetIsActive(raw_obj, active)

    def rgb_image(self, bitmap: object) -> RgbImage:
        """Chép điểm ảnh của `PdfBitmap` BGR (3 byte mỗi điểm ảnh) ra RGB, bỏ phần đệm cuối dòng."""
        width = _as_int(getattr(bitmap, "width", None))
        height = _as_int(getattr(bitmap, "height", None))
        stride = _as_int(getattr(bitmap, "stride", None))
        if _as_int(getattr(bitmap, "format", None)) != self.bgr_bitmap_format:
            raise PdfiumDataError("bitmap không phải ảnh BGR")
        if width < 1 or height < 1 or stride < 3 * width:
            raise PdfiumDataError("kích thước bitmap ngoài giới hạn")
        buffer = getattr(bitmap, "buffer", None)
        if not isinstance(buffer, ctypes.Array):
            raise PdfiumDataError("bitmap không có vùng nhớ điểm ảnh")
        data = ctypes.string_at(ctypes.addressof(buffer), min(ctypes.sizeof(buffer), stride * height))
        rows = bytearray()
        for row in range(height):
            line = bytearray(data[row * stride : row * stride + 3 * width])
            line[0::3], line[2::3] = line[2::3], line[0::3]
            rows += line
        if len(rows) != 3 * width * height:
            raise PdfiumDataError("bitmap thiếu điểm ảnh")
        return RgbImage(pixels=bytes(rows), width=width, height=height)

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
