"""Adapter duy nhất gọi RapidOCR, lượt đọc thứ hai của tầng 2 (ADR-008 mục 8, 11).

- Chỉ chạy trong worker có giới hạn (`_pdf_worker.py`). Module `rapidocr` được nạp bằng `importlib` khi cần, và mọi
  giá trị lấy từ nó được kiểm bằng `isinstance` rồi chuyển sang kiểu của Holocron trước khi rời module này (cùng
  cách với `pdfium_adapter`); RapidOCR là engine tuỳ chọn, thiếu gói thì lượt thứ hai coi như vắng mặt.
- Model PP-OCRv6 small đóng sẵn trong wheel `rapidocr` 3.10.0 (đã duyệt 2026-10-24). Đường dẫn model được truyền
  tường minh nên RapidOCR không bao giờ tự tải model (chạy offline); thiếu file model thì coi như vắng mặt. Bỏ bước
  phân loại hướng dòng (`use_cls`), vì trang đã được render theo hướng hiển thị.
- Chỉ lấy hộp toạ độ và token số/mã của từng dòng; văn bản RapidOCR không rời module này.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path

from .cross_check import numeric_tokens
from .protocol import MAX_PAGE_LINES, MAX_PAGE_WORDS, MAX_RAW_LINE_CHARS, RawSecondaryLine, RawSecondaryPass, UnitBBox

DET_MODEL = "PP-OCRv6_det_small.onnx"
REC_MODEL = "PP-OCRv6_rec_small.onnx"
CLS_MODEL = "ch_ppocr_mobile_v2.0_cls_mobile.onnx"


# Lỗi RapidOCR có thể ném khi khởi tạo hoặc đọc một trang: lỗi chuẩn của Python và các lớp lỗi riêng của gói
# (đều kế thừa thẳng `Exception`), nạp theo tên lúc nạp engine. Lỗi nào khác vẫn làm worker thoát (trang PAGE_ERROR).
BUILTIN_ERRORS: tuple[type[Exception], ...] = (
    RuntimeError,
    ValueError,
    LookupError,
    ArithmeticError,
    TypeError,
    MemoryError,
    OSError,
)
LIBRARY_ERRORS: tuple[tuple[str, str], ...] = (
    ("rapidocr.main", "RapidOCRError"),
    ("rapidocr.inference_engine.onnxruntime.main", "ONNXRuntimeError"),
    ("rapidocr.utils.load_image", "LoadImageError"),
    ("rapidocr.utils.process_img", "ResizeImgError"),
    ("rapidocr.ch_ppocr_det.utils", "ResizeImgError"),
    ("rapidocr.utils.download_file", "DownloadFileException"),
)


class RapidOcrDataError(RuntimeError):
    """RapidOCR lỗi khi đọc trang, trả giá trị không đúng kiểu mong đợi, hoặc vượt giới hạn của một trang."""


def library_errors(names: tuple[tuple[str, str], ...] = LIBRARY_ERRORS) -> tuple[type[Exception], ...]:
    """Các lớp lỗi riêng của RapidOCR tìm được theo `(module, tên lớp)`; module hay lớp vắng mặt thì bỏ qua."""
    found: list[type[Exception]] = []
    for module_name, class_name in names:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        error = getattr(module, class_name, None)
        if isinstance(error, type) and issubclass(error, Exception):
            found.append(error)
    return tuple(found)


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RapidOcrDataError(f"cần số thực, nhận {type(value).__name__}")
    return float(value)


def _as_list(value: object) -> list[object]:
    if not isinstance(value, list | tuple):
        raise RapidOcrDataError(f"cần danh sách, nhận {type(value).__name__}")
    return list(value)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _quad_to_box(quad: object, width: int, height: int) -> UnitBBox:
    """Tứ giác (4 điểm x, y theo điểm ảnh) ⇒ hộp chữ nhật bao ngoài, chuẩn hoá về [0, 1]."""
    points = [_as_list(point) for point in _as_list(quad)]
    if not points or any(len(point) != 2 for point in points):
        raise RapidOcrDataError("hộp toạ độ phải là danh sách điểm (x, y)")
    xs = [_as_float(point[0]) for point in points]
    ys = [_as_float(point[1]) for point in points]
    return (
        _clamp(min(xs) / width),
        _clamp(min(ys) / height),
        _clamp(max(xs) / width),
        _clamp(max(ys) / height),
    )


def _tolist(value: object) -> object:
    """`numpy.ndarray.tolist()` khi có; giá trị khác giữ nguyên để `_as_list` kiểm."""
    convert = getattr(value, "tolist", None)
    if callable(convert):
        result: object = convert()
        return result
    return value


class RapidOcrBackend:
    """Bọc một đối tượng `rapidocr.RapidOCR` đã khởi tạo (test thay bằng đối tượng giả cùng giao diện)."""

    def __init__(self, engine: object, errors: tuple[type[Exception], ...] = BUILTIN_ERRORS) -> None:
        if not callable(engine):
            raise RapidOcrDataError("engine RapidOCR phải gọi được")
        self._engine: Callable[[bytes], object] = engine
        self._errors = errors

    @classmethod
    def load(cls) -> RapidOcrBackend | None:
        """Nạp RapidOCR với model đóng sẵn; thiếu gói, thiếu model hoặc khởi tạo lỗi ⇒ None (không có lượt thứ hai)."""
        try:
            module = importlib.import_module("rapidocr")
        except ImportError:
            return None
        package_file = getattr(module, "__file__", None)
        factory = getattr(module, "RapidOCR", None)
        if not isinstance(package_file, str) or not callable(factory):
            return None
        models = Path(package_file).parent / "models"
        paths = {name: models / name for name in (DET_MODEL, REC_MODEL, CLS_MODEL)}
        if not all(path.is_file() for path in paths.values()):
            return None
        params: dict[str, object] = {
            "Global.use_cls": False,
            "Global.log_level": "critical",
            "Det.model_path": str(paths[DET_MODEL]),
            "Rec.model_path": str(paths[REC_MODEL]),
            "Cls.model_path": str(paths[CLS_MODEL]),
        }
        errors = BUILTIN_ERRORS + library_errors()
        try:
            engine: object = factory(params=params)
        except errors:
            return None
        return cls(engine, errors)

    def read(self, png: bytes, width: int, height: int) -> RawSecondaryPass:
        """Đọc ảnh PNG của trang; trả hộp và token số/mã của từng dòng (`RapidOcrDataError` khi dữ liệu sai)."""
        try:
            output: object = self._engine(png)
        except self._errors as exc:
            raise RapidOcrDataError(f"RapidOCR không đọc được trang ({type(exc).__name__})") from exc
        boxes = getattr(output, "boxes", None)
        texts = getattr(output, "txts", None)
        if boxes is None or texts is None:
            return RawSecondaryPass(engine="rapidocr", lines=[])
        quads = _as_list(_tolist(boxes))
        values = _as_list(texts)
        if len(quads) != len(values):
            raise RapidOcrDataError("số hộp và số dòng văn bản khác nhau")
        if len(quads) > MAX_PAGE_LINES:
            raise RapidOcrDataError("lượt đọc thứ hai vượt giới hạn số dòng của một trang")
        lines: list[RawSecondaryLine] = []
        total_tokens = 0
        for quad, text in zip(quads, values, strict=True):
            if not isinstance(text, str):
                raise RapidOcrDataError("văn bản dòng phải là chuỗi")
            tokens = [token[:MAX_RAW_LINE_CHARS] for token in numeric_tokens(text)][:MAX_RAW_LINE_CHARS]
            total_tokens += len(tokens)
            lines.append(RawSecondaryLine(bbox=_quad_to_box(quad, width, height), tokens=tokens))
        if total_tokens > MAX_PAGE_WORDS:
            raise RapidOcrDataError("lượt đọc thứ hai vượt giới hạn số token của một trang")
        return RawSecondaryPass(engine="rapidocr", lines=lines)
