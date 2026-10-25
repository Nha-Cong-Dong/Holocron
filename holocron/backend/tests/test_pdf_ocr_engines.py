"""Engine của tầng 2 sau giao diện thay được (ADR-008 mục 3, 4, 8, 11, 12).

Tesseract và RapidOCR thật KHÔNG chạy trong các test này: Tesseract được thay bằng `Runner` giả, RapidOCR bằng
engine giả cùng giao diện. `run_bounded` được kiểm bằng tiến trình con Python vô hại. Riêng
`test_real_tesseract_reads_a_page_without_text_layer` chạy binary Tesseract thật khi máy có (tự `skip` khi không).
"""

from __future__ import annotations

import io
import json
import math
import struct
import sys
import time
import zlib
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from holocron_core.infrastructure.document_readers import _pdf_worker, rapidocr_adapter
from holocron_core.infrastructure.document_readers._pdf_worker import OcrRunner, build_ocr
from holocron_core.infrastructure.document_readers.config import (
    ENV_PDF_OCR_PRIMARY,
    ENV_PDF_OCR_SECONDARY,
    ENV_TESSDATA_DIR,
    ENV_TESSERACT_CMD,
    OCR_INSTALL_HINT,
    OcrSettings,
    PdfReaderConfig,
    load_ocr_settings,
)
from holocron_core.infrastructure.document_readers.pdf_reader import WorkerPdfReader
from holocron_core.infrastructure.document_readers.pdfium_adapter import PdfiumBackend, PdfiumDataError
from holocron_core.infrastructure.document_readers.protocol import (
    OcrStartedMessage,
    OpenedMessage,
    PageErrorMessage,
    PageMessage,
    PageRequest,
    RawOcrLine,
    RawOcrPage,
    RawPdfPage,
    RawSecondaryLine,
    RawSecondaryPass,
    RawTextLine,
    ReadyMessage,
    WorkerRequest,
    decode_message,
)
from holocron_core.infrastructure.document_readers.rapidocr_adapter import (
    RapidOcrBackend,
    RapidOcrDataError,
    library_errors,
)
from holocron_core.infrastructure.document_readers.raster import (
    GrayImage,
    PixelBox,
    encode_png_gray,
    ink_outside_text_ratio,
    pack_rows,
    render_scale,
)
from holocron_core.infrastructure.document_readers.tesseract import (
    RunResult,
    TesseractEngine,
    TesseractError,
    parse_tsv_words,
    run_bounded,
    tesseract_env,
    tsv_to_lines,
)
from holocron_core.infrastructure.llm_config import ConfigError
from tests.pdf_samples import Page, text_lines, write_pdf

DOC_ID = "doc_00000000000000ff"
CONFIG = PdfReaderConfig(ocr_dpi=36)  # ảnh nhỏ cho test nhanh
SETTINGS = OcrSettings(tesseract_cmd="tesseract-test", tessdata_dir="/data/tess", secondary=None)
TCVN3 = ["HÖ ®iÒu hµnh cÊp ph¸t CPU cho c¸c tiÕn tr×nh theo l\u00adît vµ ®\u00adîc ch¹y tèi ®a mét l\u00adîng tö."] * 4
BODY = ["The scheduler gives each ready process the processor for one time quantum."] * 4
TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def _tsv(rows: Sequence[tuple[int, int, int, int, int, int, float, str]]) -> str:
    """Dòng TSV: (block, par, line, left, top, width, conf, text); chiều cao cố định 20 px."""
    body = [
        f"5\t1\t{b}\t{p}\t{ln}\t1\t{left}\t{top}\t{width}\t20\t{conf}\t{text}"
        for b, p, ln, left, top, width, conf, text in rows
    ]
    return "\n".join([TSV_HEADER, "1\t1\t0\t0\t0\t0\t0\t0\t1000\t1000\t-1\t", *body]) + "\n"


SAMPLE_TSV = _tsv(
    [
        (1, 1, 1, 100, 100, 200, 90.0, "Hệ"),
        (1, 1, 1, 320, 100, 100, 60.0, "điều"),
        (1, 1, 2, 100, 140, 80, 95.5, "P1"),
        (1, 1, 2, 200, 140, 20, -1.0, " "),
        (2, 1, 1, 100, 500, 300, 99.0, "0x1F"),
    ]
)


class FakeRunner:
    """`Runner` giả: trả kết quả theo tham số đầu tiên sau tên binary, ghi lại mọi lời gọi."""

    def __init__(self, outcomes: dict[str, RunResult]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[list[str], bytes, float, int]] = []

    def __call__(self, args: Sequence[str], stdin_data: bytes, timeout_s: float, max_output_bytes: int) -> RunResult:
        self.calls.append((list(args), stdin_data, timeout_s, max_output_bytes))
        return self.outcomes[args[1]]


def _probe_runner(langs: bytes = b'List of available languages in "/data/tess/" (2):\neng\nvie\n') -> FakeRunner:
    return FakeRunner(
        {
            "--version": RunResult(status="ok", stdout=b"tesseract 5.4.0\n"),
            "--list-langs": RunResult(status="ok", stdout=langs),
            "stdin": RunResult(status="ok", stdout=SAMPLE_TSV.encode("utf-8")),
        }
    )


def _image(width: int = 10, height: int = 4, pixels: bytes | None = None) -> GrayImage:
    return GrayImage(pixels=pixels or b"\xff" * (width * height), width=width, height=height, dpi=300.0)


# --------------------------------------------------------------------------- #
# Ảnh trang (raster)
# --------------------------------------------------------------------------- #


def test_render_scale_keeps_300_dpi_and_caps_megapixels() -> None:
    letter = render_scale(612.0, 792.0, 300, 25_000_000)
    assert letter == pytest.approx(300 / 72)
    huge = render_scale(14_400.0, 14_400.0, 300, 25_000_000)
    assert huge < 300 / 72 and math.ceil(14_400 * huge) ** 2 <= 25_000_000
    tight = render_scale(613.3, 791.7, 300, 1_000_000)
    assert math.ceil(613.3 * tight) * math.ceil(791.7 * tight) <= 1_000_000
    with pytest.raises(ValueError):
        render_scale(0.0, 792.0, 300, 25_000_000)


def test_pack_rows_drops_stride_padding() -> None:
    assert pack_rows(b"ab..cd..", 2, 2, 4) == b"abcd"
    assert pack_rows(b"abcd", 2, 2, 2) == b"abcd"
    with pytest.raises(ValueError):
        pack_rows(b"abc", 2, 2, 2)
    with pytest.raises(ValueError):
        GrayImage(pixels=b"abc", width=2, height=2, dpi=72.0)


def test_png_encoding_is_valid_grayscale_with_dpi() -> None:
    image = GrayImage(pixels=bytes(range(12)), width=4, height=3, dpi=300.0)
    png = encode_png_gray(image)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    chunks: dict[bytes, bytes] = {}
    position = 8
    while position < len(png):
        (length,) = struct.unpack(">I", png[position : position + 4])
        kind = png[position + 4 : position + 8]
        data = png[position + 8 : position + 8 + length]
        assert struct.unpack(">I", png[position + 8 + length : position + 12 + length])[0] == zlib.crc32(kind + data)
        chunks[kind] = data
        position += 12 + length
    assert struct.unpack(">IIBBBBB", chunks[b"IHDR"]) == (4, 3, 8, 0, 0, 0, 0)
    assert struct.unpack(">IIB", chunks[b"pHYs"]) == (11811, 11811, 1)  # 300 DPI
    raw = zlib.decompress(chunks[b"IDAT"])
    assert raw == b"".join(b"\x00" + image.pixels[r * 4 : (r + 1) * 4] for r in range(3))


def test_ink_outside_text_ratio_counts_ink_once() -> None:
    # Ảnh 10×4: mực ở cột 0–3 của dòng 0–1 (8 điểm, trong hộp chữ) và cột 8–9 của dòng 3 (2 điểm, ngoài hộp).
    rows = [b"\x00" * 4 + b"\xff" * 6] * 2 + [b"\xff" * 10] + [b"\xff" * 8 + b"\x10" * 2]
    image = _image(pixels=b"".join(rows))
    boxes: list[PixelBox] = [(0, 0, 4, 2), (2, 0, 4, 2), (-5, -5, 1, 1), (20, 0, 30, 2)]
    assert ink_outside_text_ratio(image, boxes, 128) == pytest.approx(2 / 10)
    assert ink_outside_text_ratio(image, [], 128) == 1.0
    assert ink_outside_text_ratio(_image(), boxes, 128) == 0.0  # trang trắng
    assert ink_outside_text_ratio(image, boxes, 1) == 0.0  # ngưỡng 1: không điểm nào là mực


def test_pdfium_renders_gray_page_in_display_orientation(tmp_path: Path) -> None:
    backend = PdfiumBackend.load()
    data = write_pdf(tmp_path / "rot.pdf", [Page(texts=text_lines(BODY), rotate=90)]).read_bytes()
    document = backend.open(data)
    assert not isinstance(document, str)
    with document:
        image = document.render_gray(0, 72, 25_000_000)
        assert (image.width, image.height, image.dpi) == (792, 612, pytest.approx(72.0))
        assert min(image.pixels) < 128 < max(image.pixels)  # có chữ đen trên nền trắng
        small = document.render_gray(0, 300, 100_000)
        assert small.width * small.height <= 100_000 and small.dpi < 72
    fake = SimpleNamespace(width=2, height=2, stride=2, format=backend.gray_bitmap_format + 1, buffer=None)
    with pytest.raises(PdfiumDataError, match="xám"):
        backend.gray_image(fake, 72.0, 100)
    fake.format = backend.gray_bitmap_format
    with pytest.raises(PdfiumDataError, match="vùng nhớ"):
        backend.gray_image(fake, 72.0, 100)
    with pytest.raises(PdfiumDataError, match="giới hạn"):
        backend.gray_image(fake, 72.0, 3)


# --------------------------------------------------------------------------- #
# Tesseract (Runner giả) và run_bounded (tiến trình con Python vô hại)
# --------------------------------------------------------------------------- #


def test_tsv_is_grouped_into_lines_with_weighted_confidence() -> None:
    lines, boxes = tsv_to_lines(SAMPLE_TSV, 1000, 1000)
    assert [line.text for line in lines] == ["Hệ điều", "P1", "0x1F"]
    assert lines[0].conf == pytest.approx((0.9 * 2 + 0.6 * 4) / 6)
    assert lines[0].bbox == pytest.approx((0.1, 0.1, 0.42, 0.12))
    assert [w.text for w in lines[0].words] == ["Hệ", "điều"]
    assert lines[1].conf == pytest.approx(0.955)
    assert boxes == [(100, 100, 300, 120), (320, 100, 420, 120), (100, 140, 180, 160), (100, 500, 400, 520)]
    clamped, _ = tsv_to_lines(_tsv([(1, 1, 1, 900, 990, 300, 120.0, "x")]), 1000, 1000)
    assert clamped[0].bbox == (0.9, 0.99, 1.0, 1.0) and clamped[0].conf == 1.0
    assert tsv_to_lines(TSV_HEADER + "\n", 10, 10) == ([], [])


def test_long_tsv_lines_are_split_and_limits_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    words = [(1, 1, 1, 10 * i, 10, 9, 90.0, "w" * 999) for i in range(20)]
    lines, _ = tsv_to_lines(_tsv(words), 1000, 1000)
    assert all(len(line.text) <= 8000 for line in lines) and len(lines) == 3
    assert sum(len(line.words) for line in lines) == 20
    from holocron_core.infrastructure.document_readers import tesseract

    monkeypatch.setattr(tesseract, "MAX_PAGE_LINES", 2)
    with pytest.raises(TesseractError, match="giới hạn"):
        tsv_to_lines(_tsv(words), 1000, 1000)


@pytest.mark.parametrize(
    "bad",
    [
        TSV_HEADER + "\n5\t1\t1\n",
        TSV_HEADER + "\n5\t1\t1\t1\t1\t1\tx\t0\t1\t1\t90\tP1\n",
        TSV_HEADER + "\n5\t1\t1\t1\t1\t1\t0\t0\t1\t1\tnan?\tP1\n",
    ],
)
def test_malformed_tsv_is_rejected(bad: str) -> None:
    with pytest.raises(TesseractError):
        parse_tsv_words(bad)


def test_probe_checks_binary_and_vietnamese_language() -> None:
    runner = _probe_runner()
    engine = TesseractEngine.probe(SETTINGS, 3.0, runner)
    assert engine is not None and engine.command == "tesseract-test"
    assert runner.calls[0][0] == ["tesseract-test", "--version"]
    assert runner.calls[1][0] == ["tesseract-test", "--list-langs", "--tessdata-dir", "/data/tess"]
    assert all(call[2] == 3.0 for call in runner.calls)

    no_vie = _probe_runner(b'List of available languages in "/x/" (1):\neng\n')
    assert TesseractEngine.probe(SETTINGS, 3.0, no_vie) is None
    assert TesseractEngine.probe(SETTINGS, 3.0, _probe_runner(b"\xff\xfe")) is None
    missing = FakeRunner({"--version": RunResult(status="failed")})
    assert TesseractEngine.probe(SETTINGS, 3.0, missing) is None
    listing_fails = FakeRunner({"--version": RunResult(status="ok"), "--list-langs": RunResult(status="timeout")})
    assert TesseractEngine.probe(SETTINGS, 3.0, listing_fails) is None
    default_dir = _probe_runner()
    TesseractEngine.probe(SETTINGS.model_copy(update={"tessdata_dir": None}), 3.0, default_dir)
    assert default_dir.calls[1][0] == ["tesseract-test", "--list-langs"]


def test_recognize_sends_png_on_stdin_and_reads_tsv() -> None:
    runner = _probe_runner()
    engine = TesseractEngine(command="tesseract-test", tessdata_dir="/data/tess", run=runner)
    image = _image(1000, 1000)
    png = encode_png_gray(image)
    lines, boxes = engine.recognize(png, image, CONFIG)
    assert [line.text for line in lines] == ["Hệ điều", "P1", "0x1F"] and len(boxes) == 4
    args, stdin_data, timeout_s, limit = runner.calls[0]
    assert args == [
        "tesseract-test", "stdin", "stdout", "-l", "vie", "--tessdata-dir", "/data/tess",
        "--dpi", "300", "-c", "tessedit_create_tsv=1",
    ]  # fmt: skip
    assert stdin_data == png and timeout_s == CONFIG.tesseract_timeout_s and limit == CONFIG.max_message_bytes

    for status in ("timeout", "overflow", "failed"):
        failing = TesseractEngine("t", None, FakeRunner({"stdin": RunResult(status=status)}))
        with pytest.raises(TesseractError, match=status):
            failing.recognize(png, image, CONFIG)
    garbage = TesseractEngine("t", None, FakeRunner({"stdin": RunResult(status="ok", stdout=b"\xff")}))
    with pytest.raises(TesseractError, match="UTF-8"):
        garbage.recognize(png, image, CONFIG)


def _python(code: str) -> list[str]:
    return [sys.executable, "-I", "-S", "-c", code]


def test_run_bounded_feeds_stdin_and_bounds_output_and_time() -> None:
    echo = run_bounded(_python("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()[::-1])"), b"abc", 30.0, 100)
    assert echo == RunResult(status="ok", stdout=b"cba")
    flood = run_bounded(_python("import sys\nwhile True: sys.stdout.buffer.write(b'x' * 65536)"), b"", 30.0, 200_000)
    assert flood.status == "overflow" and flood.stdout == b""
    started = time.monotonic()
    slow = run_bounded(_python("import time; time.sleep(30)"), b"", 0.5, 100)
    assert slow.status == "timeout" and time.monotonic() - started < 15.0
    assert run_bounded(_python("raise SystemExit(3)"), b"", 30.0, 100).status == "failed"
    assert run_bounded(["holocron-no-such-binary-xyz"], b"", 5.0, 100).status == "failed"


def test_tesseract_environment_is_minimal() -> None:
    env = tesseract_env()
    assert set(env) <= {"SYSTEMROOT"}
    if sys.platform == "win32":
        assert "SYSTEMROOT" in env


# --------------------------------------------------------------------------- #
# RapidOCR (engine giả cùng giao diện)
# --------------------------------------------------------------------------- #


class _Array:
    def __init__(self, value: object) -> None:
        self.value = value

    def tolist(self) -> object:
        return self.value


class FakeRapid:
    def __init__(self, output: object = None, error: Exception | None = None) -> None:
        self.output = output
        self.error = error
        self.inputs: list[bytes] = []

    def __call__(self, image: bytes) -> object:
        self.inputs.append(image)
        if self.error is not None:
            raise self.error
        return self.output


def _quad(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def test_rapidocr_adapter_keeps_only_boxes_and_numeric_tokens() -> None:
    output = SimpleNamespace(
        boxes=_Array([_quad(100, 100, 400, 140), _quad(-5, 500, 1200, 540)]),
        txts=("P1 0 5 #12", "Thoi gian cho"),
        scores=(0.99, 0.98),
    )
    engine = FakeRapid(output)
    result = RapidOcrBackend(engine).read(b"png", 1000, 1000)
    assert engine.inputs == [b"png"]
    assert result == RawSecondaryPass(
        engine="rapidocr",
        lines=[
            RawSecondaryLine(bbox=(0.1, 0.1, 0.4, 0.14), tokens=["1", "0", "5", "#12"]),
            RawSecondaryLine(bbox=(0.0, 0.5, 1.0, 0.54), tokens=[]),
        ],
    )
    assert "Thoi" not in result.model_dump_json()  # văn bản RapidOCR không rời adapter
    empty = RapidOcrBackend(FakeRapid(SimpleNamespace(boxes=None, txts=None))).read(b"png", 10, 10)
    assert empty.lines == []
    plain_lists = SimpleNamespace(boxes=[_quad(0, 0, 5, 5)], txts=["7"])
    assert RapidOcrBackend(FakeRapid(plain_lists)).read(b"png", 10, 10).lines[0].tokens == ["7"]


@pytest.mark.parametrize(
    "output",
    [
        SimpleNamespace(boxes=_Array([_quad(0, 0, 1, 1)]), txts=()),
        SimpleNamespace(boxes=_Array([_quad(0, 0, 1, 1)]), txts=(3,)),
        SimpleNamespace(boxes=_Array("box"), txts=("1",)),
        SimpleNamespace(boxes=_Array([[[0, "y"]]]), txts=("1",)),
        SimpleNamespace(boxes=_Array([[[0, 1, 2]]]), txts=("1",)),
        SimpleNamespace(boxes=_Array([[[True, 1]]]), txts=("1",)),
    ],
)
def test_rapidocr_adapter_rejects_malformed_output(output: object) -> None:
    with pytest.raises(RapidOcrDataError):
        RapidOcrBackend(FakeRapid(output)).read(b"png", 10, 10)


def test_rapidocr_adapter_bounds_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RapidOcrDataError):
        RapidOcrBackend("not callable")
    with pytest.raises(RapidOcrDataError, match="ValueError"):
        RapidOcrBackend(FakeRapid(error=ValueError("onnx"))).read(b"png", 10, 10)
    with pytest.raises(KeyboardInterrupt):  # lỗi ngoài danh sách không bị nuốt
        RapidOcrBackend(FakeRapid(error=KeyboardInterrupt())).read(b"png", 10, 10)
    monkeypatch.setattr(rapidocr_adapter, "MAX_PAGE_LINES", 1)
    two = SimpleNamespace(boxes=_Array([_quad(0, 0, 1, 1)] * 2), txts=("1", "2"))
    with pytest.raises(RapidOcrDataError, match="số dòng"):
        RapidOcrBackend(FakeRapid(two)).read(b"png", 10, 10)
    monkeypatch.setattr(rapidocr_adapter, "MAX_PAGE_WORDS", 2)
    many = SimpleNamespace(boxes=_Array([_quad(0, 0, 1, 1)]), txts=("1 2 3",))
    with pytest.raises(RapidOcrDataError, match="token"):
        RapidOcrBackend(FakeRapid(many)).read(b"png", 10, 10)


def test_library_errors_are_found_by_name() -> None:
    names = (("json", "JSONDecodeError"), ("json", "dumps"), ("holocron_no_such_module", "Error"))
    assert library_errors(names) == (json.JSONDecodeError,)


def test_rapidocr_load_uses_bundled_models_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = tmp_path / "rapidocr"
    (package / "models").mkdir(parents=True)
    created: list[dict[str, object]] = []

    class Factory:
        def __init__(self, params: dict[str, object]) -> None:
            created.append(params)

        def __call__(self, image: bytes) -> object:
            return SimpleNamespace(boxes=None, txts=None)

    module = SimpleNamespace(__file__=str(package / "__init__.py"), RapidOCR=Factory)
    real_import = rapidocr_adapter.importlib.import_module

    def fake_import(name: str) -> object:
        if name == "rapidocr":
            return module
        if name.startswith("rapidocr."):
            raise ImportError(name)
        return real_import(name)

    monkeypatch.setattr(rapidocr_adapter.importlib, "import_module", fake_import)
    assert RapidOcrBackend.load() is None  # thiếu file model: không tự tải, coi như vắng mặt
    for name in (rapidocr_adapter.DET_MODEL, rapidocr_adapter.REC_MODEL, rapidocr_adapter.CLS_MODEL):
        (package / "models" / name).write_bytes(b"onnx")
    backend = RapidOcrBackend.load()
    assert backend is not None and backend.read(b"png", 10, 10).lines == []
    [params] = created
    assert params["Global.use_cls"] is False
    assert params["Det.model_path"] == str(package / "models" / rapidocr_adapter.DET_MODEL)
    assert params["Rec.model_path"] == str(package / "models" / rapidocr_adapter.REC_MODEL)

    class Broken:
        def __init__(self, params: dict[str, object]) -> None:
            raise RuntimeError("onnxruntime")

    module.RapidOCR = Broken
    assert RapidOcrBackend.load() is None
    module.RapidOCR = None
    assert RapidOcrBackend.load() is None

    def no_package(name: str) -> object:
        raise ImportError(name)

    monkeypatch.setattr(rapidocr_adapter.importlib, "import_module", no_package)
    assert RapidOcrBackend.load() is None


# --------------------------------------------------------------------------- #
# Worker: OCR trang có lớp chữ kém (engine giả, PDFium thật)
# --------------------------------------------------------------------------- #


class StubPrimary:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.images: list[GrayImage] = []

    def recognize(
        self, png: bytes, image: GrayImage, config: PdfReaderConfig
    ) -> tuple[list[RawOcrLine], list[PixelBox]]:
        assert png == encode_png_gray(image)
        self.images.append(image)
        if self.error is not None:
            raise self.error
        line = RawOcrLine(text="Hệ điều hành 42", bbox=(0.1, 0.1, 0.9, 0.12), words=[], conf=0.93)
        return [line], [(0, 0, image.width, image.height)]


class StubSecondary:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def read(self, png: bytes, width: int, height: int) -> RawSecondaryPass:
        if self.error is not None:
            raise self.error
        return RawSecondaryPass(engine="rapidocr", lines=[RawSecondaryLine(bbox=(0.1, 0.1, 0.9, 0.12), tokens=["42"])])


def _run_worker(path: Path, pages: list[int], ocr: OcrRunner | None) -> list[BaseModel]:
    emitted: list[BaseModel] = []
    request = WorkerRequest(path=str(path), page_budget=len(pages), config=CONFIG, ocr=SETTINGS)
    _pdf_worker.run(request, PdfiumBackend.load(), [PageRequest(page_index=i) for i in pages], emitted.append, ocr)
    return emitted


def test_worker_ocrs_only_pages_with_a_poor_text_layer(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "mixed.pdf", [Page(texts=text_lines(BODY)), Page(texts=text_lines(TCVN3))])
    primary = StubPrimary()
    emitted = _run_worker(path, [0, 1], OcrRunner(primary=primary, secondary=StubSecondary()))
    assert isinstance(emitted[0], OpenedMessage)
    assert isinstance(emitted[1], PageMessage) and emitted[1].page.ocr is None  # lớp chữ tốt: không OCR
    assert emitted[2] == OcrStartedMessage(page_index=1)
    assert isinstance(emitted[3], PageMessage)
    ocr = emitted[3].page.ocr
    assert ocr is not None and ocr.secondary is not None and ocr.secondary.lines[0].tokens == ["42"]
    assert ocr.dpi == pytest.approx(36.0) and ocr.ink_outside_text_ratio == 0.0  # hộp chữ phủ cả trang
    assert len(primary.images) == 1 and primary.images[0].width == 306  # 612 pt ở 36 DPI


def test_worker_ocr_failures(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "scan.pdf", [Page(texts=text_lines(TCVN3))])
    # Lượt thứ hai lỗi ở trang này: trang vẫn có kết quả của lượt chính, không có lượt đối chiếu.
    emitted = _run_worker(path, [0], OcrRunner(primary=StubPrimary(), secondary=StubSecondary(RapidOcrDataError("x"))))
    assert isinstance(emitted[2], PageMessage) and emitted[2].page.ocr is not None
    assert emitted[2].page.ocr.secondary is None
    # Lượt chính lỗi (quá hạn, thoát lỗi): trang thành page_error, tiến trình cha đánh dấu PAGE_ERROR.
    emitted = _run_worker(path, [0], OcrRunner(primary=StubPrimary(TesseractError("timeout")), secondary=None))
    assert emitted[2] == PageErrorMessage(page_index=0, message="TesseractError")


def test_build_ocr_requires_tesseract_and_vie() -> None:
    assert build_ocr(SETTINGS, CONFIG, FakeRunner({"--version": RunResult(status="failed")})) is None
    runner = build_ocr(SETTINGS, CONFIG, _probe_runner())
    assert runner is not None and runner.secondary is None
    loaded: list[int] = []

    def load_secondary() -> StubSecondary:
        loaded.append(1)
        return StubSecondary()

    with_second = build_ocr(
        SETTINGS.model_copy(update={"secondary": "rapidocr"}), CONFIG, _probe_runner(), load_secondary
    )
    assert with_second is not None and isinstance(with_second.secondary, StubSecondary) and loaded == [1]


def test_worker_main_uses_ocr_cpu_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_pdf(tmp_path / "one.pdf", [Page(texts=text_lines(BODY))])
    request = WorkerRequest(path=str(path), page_budget=2, config=CONFIG, ocr=SETTINGS)
    calls: list[str] = []
    limits: list[int] = []

    def fake_build(settings: OcrSettings, config: PdfReaderConfig) -> OcrRunner:
        calls.append("build_ocr")
        return OcrRunner(primary=StubPrimary(), secondary=None)

    def fake_limits(mem_bytes: int, cpu_seconds: int) -> None:
        calls.append("apply_limits")
        limits.append(cpu_seconds)

    stdout = io.StringIO()
    monkeypatch.setattr(_pdf_worker, "build_ocr", fake_build)
    monkeypatch.setattr(_pdf_worker, "apply_limits", fake_limits)
    monkeypatch.setattr("sys.stdin", io.StringIO(request.model_dump_json() + "\n"))
    monkeypatch.setattr("sys.stdout", stdout)
    _pdf_worker.main()
    assert calls == ["build_ocr", "apply_limits"]  # kiểm engine trước khi áp giới hạn
    assert limits == [CONFIG.ocr_cpu_seconds_per_page * 3]
    assert [decode_message(line) for line in stdout.getvalue().splitlines()] == [
        ReadyMessage(),
        OpenedMessage(page_count=1),
    ]


# --------------------------------------------------------------------------- #
# Tiến trình cha: hạn của tầng 2, cấu hình OCR
# --------------------------------------------------------------------------- #


class FakeSession:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.timeouts: list[float] = []
        self.killed = False

    def request_page(self, index: int) -> None:
        return None

    def receive(self, timeout_s: float) -> object:
        self.timeouts.append(timeout_s)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def kill(self) -> None:
        self.killed = True


def _scan_raw(index: int = 0) -> RawPdfPage:
    garbled = RawTextLine(text=TCVN3[0], bbox=None, words=[])
    line = RawOcrLine(text="Hệ điều hành", bbox=(0.1, 0.1, 0.9, 0.12), words=[], conf=0.95)
    ocr = RawOcrPage(dpi=300.0, lines=[line], ink_outside_text_ratio=0.0, secondary=None)
    return RawPdfPage(
        page_index=index, width_pt=612, height_pt=792, lines=[garbled], image_area_ratio=0, path_area_ratio=0, ocr=ocr
    )


def test_parent_switches_to_ocr_timeout_after_ocr_started() -> None:
    reader = WorkerPdfReader(PdfReaderConfig(text_layer_page_timeout_s=5.0, ocr_page_timeout_s=30.0))
    session = FakeSession([OcrStartedMessage(page_index=0), PageMessage(page=_scan_raw())])
    page, healthy = reader._next_page(session, 0, DOC_ID)
    assert (page.tier_used, page.status, healthy) == ("ocr", "needs_review", True)
    assert session.timeouts == [5.0, 30.0]
    session = FakeSession([OcrStartedMessage(page_index=0), PageMessage(page=_scan_raw())])
    reader._next_page(session, 0, DOC_ID, 2.0, 7.0)
    assert session.timeouts == [2.0, 7.0]
    # Quá hạn OCR: PAGE_TIMEOUT và kill worker; báo OCR cho trang khác: PAGE_ERROR và kill worker.
    timed_out = FakeSession([OcrStartedMessage(page_index=0), TimeoutError()])
    page, healthy = reader._next_page(timed_out, 0, DOC_ID)
    assert (page.failure_reason, healthy, timed_out.killed) == ("PAGE_TIMEOUT", False, True)
    wrong = FakeSession([OcrStartedMessage(page_index=3)])
    page, healthy = reader._next_page(wrong, 0, DOC_ID)
    assert (page.failure_reason, healthy, wrong.killed) == ("PAGE_ERROR", False, True)
    twice = FakeSession([OcrStartedMessage(page_index=0), OcrStartedMessage(page_index=0)])
    assert reader._next_page(twice, 0, DOC_ID)[0].failure_reason == "PAGE_ERROR"


def test_ocr_settings_from_environment() -> None:
    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name == "tesseract" else None

    assert load_ocr_settings({}, which) == OcrSettings(
        tesseract_cmd="/usr/bin/tesseract", tessdata_dir=None, secondary="rapidocr"
    )
    custom = load_ocr_settings(
        {
            ENV_PDF_OCR_PRIMARY: " tesseract ",
            ENV_PDF_OCR_SECONDARY: "rapidocr",
            ENV_TESSERACT_CMD: "C:/Tesseract/tesseract.exe",
            ENV_TESSDATA_DIR: "  ",
        },
        which,
    )
    assert custom == OcrSettings(tesseract_cmd="C:/Tesseract/tesseract.exe", tessdata_dir=None, secondary="rapidocr")
    assert load_ocr_settings({ENV_PDF_OCR_SECONDARY: ""}, which).secondary is None  # rỗng: không có lượt thứ hai
    assert load_ocr_settings({ENV_PDF_OCR_SECONDARY: "  "}, which).secondary is None
    assert load_ocr_settings({ENV_TESSDATA_DIR: "/opt/tessdata"}, which).tessdata_dir == "/opt/tessdata"
    for name, value in ((ENV_PDF_OCR_PRIMARY, "rapidocr"), (ENV_PDF_OCR_SECONDARY, "easyocr-secret-value")):
        with pytest.raises(ConfigError) as excinfo:
            load_ocr_settings({name: value}, which)
        assert name in str(excinfo.value) and value not in str(excinfo.value)
    assert load_ocr_settings({}, lambda _: None).tesseract_cmd == "tesseract"
    assert "vie.traineddata" in OCR_INSTALL_HINT and "HOLOCRON_TESSERACT_CMD" in OCR_INSTALL_HINT
    with pytest.raises(ValidationError):
        OcrSettings(tesseract_cmd="", secondary=None)


def test_reader_with_unusable_tesseract_marks_scan_pages_engine_missing(tmp_path: Path) -> None:
    """Worker thật, binary Tesseract không tồn tại: trang cần OCR vẫn là `OCR_ENGINE_MISSING`, trang số đọc bình thường."""
    path = write_pdf(tmp_path / "mixed.pdf", [Page(texts=text_lines(BODY)), Page(texts=text_lines(TCVN3))])
    settings = OcrSettings(tesseract_cmd=str(tmp_path / "no-such-tesseract"), tessdata_dir=None, secondary=None)
    reader = WorkerPdfReader(ocr=settings)
    assert reader.ocr is settings
    pages = list(reader.read_pages(path, DOC_ID))
    assert [(p.status, p.failure_reason) for p in pages] == [("ok", None), ("failed", "OCR_ENGINE_MISSING")]


# --------------------------------------------------------------------------- #
# Tích hợp: binary Tesseract thật (tự skip khi máy không có)
# --------------------------------------------------------------------------- #


def _real_tesseract() -> OcrSettings | None:
    try:
        settings = load_ocr_settings()
    except ConfigError:
        return None
    settings = settings.model_copy(update={"secondary": None})  # không chạy RapidOCR thật trong pytest
    return settings if TesseractEngine.probe(settings, 10.0) is not None else None


def test_real_tesseract_reads_a_page_without_text_layer(tmp_path: Path) -> None:
    settings = _real_tesseract()
    if settings is None:
        pytest.skip("máy không có Tesseract với gói `vie`")
    # Trang ít chữ: `text_score` thấp (độ đủ chữ) nên được OCR dù chữ hiển thị rõ.
    lines = ["Process Arrival Burst", "P1 0 5", "P2 1 3"]
    path = write_pdf(tmp_path / "short.pdf", [Page(texts=text_lines(lines, step=40.0))])
    [page] = list(WorkerPdfReader(PdfReaderConfig(), ocr=settings).read_pages(path, DOC_ID))
    assert page.tier_used == "ocr" and all(line.engine == "tesseract" for line in page.lines)
    text = " ".join(line.text for line in page.lines)
    assert "Process" in text and "Burst" in text
    assert page.ocr_score is not None and page.ocr_score > 0.5
    assert page.cross_check.status == "unavailable" and "CROSS_CHECK_UNAVAILABLE" in page.review_reasons
    assert page.ink_outside_text_ratio is not None and page.ink_outside_text_ratio < CONFIG.figure_ink_min
