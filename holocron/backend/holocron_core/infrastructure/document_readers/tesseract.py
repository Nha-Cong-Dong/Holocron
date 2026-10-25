"""Engine chính của tầng 2: binary Tesseract `vie` gọi qua subprocess (ADR-008 mục 8).

Chỉ chạy BÊN TRONG worker có giới hạn (`_pdf_worker.py`); Tesseract là tiến trình con của worker nên chịu cùng
giới hạn OS (RLIMIT kế thừa trên Unix, Job Object bao cả tiến trình con trên Windows). Ảnh PNG do PDFium render
đi vào qua stdin, kết quả TSV ra qua stdout, nên Tesseract không ghi file nào (giữ được `RLIMIT_FSIZE` 0) và không
bao giờ đọc file PDF gốc.

- Gọi bằng danh sách tham số (`shell=False`), môi trường tối thiểu (`SYSTEMROOT` trên Windows), timeout, stdout
  đọc có giới hạn byte; stdout giải mã UTF-8 strict.
- TSV xin bằng `-c tessedit_create_tsv=1` thay vì file cấu hình `tsv`: khi `--tessdata-dir` trỏ tới thư mục chỉ có
  `vie.traineddata`, Tesseract không tìm thấy `configs/tsv` và lặng lẽ trả văn bản thường (đo 2026-10-25 với 5.4.0).
- Hàm đọc unicharset của `.traineddata` (ADR-008 mục 14, test 1) là hàm thuần trên bytes.
"""

from __future__ import annotations

import contextlib
import os
import struct
import subprocess
import sys
import threading
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import IO, Literal

from .config import (
    DEFAULT_OCR_KILL_GRACE_S,
    DEFAULT_OCR_PROBE_MAX_BYTES,
    TESSERACT_LANGUAGE,
    OcrSettings,
    PdfReaderConfig,
)
from .protocol import MAX_PAGE_LINES, MAX_PAGE_WORDS, MAX_RAW_LINE_CHARS, RawOcrLine, RawWord, UnitBBox
from .raster import GrayImage, PixelBox

READ_CHUNK_BYTES = 64 * 1024
TSV_COLUMNS = 12
TSV_WORD_LEVEL = 5

RunStatus = Literal["ok", "timeout", "overflow", "failed"]


@dataclass(frozen=True)
class RunResult:
    status: RunStatus
    stdout: bytes = b""


Runner = Callable[[Sequence[str], bytes, float, int], RunResult]


class TesseractError(RuntimeError):
    """Tesseract không đọc được trang (quá hạn, thoát lỗi, đầu ra quá lớn hoặc sai dạng)."""


def tesseract_env() -> dict[str, str]:
    """Môi trường tối thiểu cho Tesseract; Windows cần `SYSTEMROOT` để nạp DLL hệ thống."""
    env: dict[str, str] = {}
    if sys.platform == "win32":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return env


def _feed(stdin: IO[bytes] | None, data: bytes) -> None:
    if stdin is None:
        return
    with contextlib.suppress(OSError):  # tiến trình con đã thoát hoặc bị kill
        stdin.write(data)
    with contextlib.suppress(OSError):
        stdin.close()


def _reap(process: subprocess.Popen[bytes], grace_s: float) -> int | None:
    """Chờ tiến trình con thoát tối đa `grace_s`; quá hạn thì kill rồi chờ thêm `grace_s`. Vẫn chưa thoát ⇒ None.

    `wait()` không có hạn có thể treo worker mãi (tiến trình con đã đóng stdout nhưng không thoát); worker có hạn
    CPU chứ không có hạn wall-clock, nên mọi lần chờ ở đây đều có hạn.
    """
    try:
        return process.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            process.kill()
    try:
        return process.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        return None


def run_bounded(
    args: Sequence[str],
    stdin_data: bytes,
    timeout_s: float,
    max_output_bytes: int,
    *,
    kill_grace_s: float = DEFAULT_OCR_KILL_GRACE_S,
) -> RunResult:
    """Chạy `args` (không qua shell), ghi `stdin_data` vào stdin, đọc stdout tối đa `max_output_bytes` byte.

    Quá hạn ⇒ "timeout"; stdout vượt giới hạn ⇒ "overflow"; mã thoát khác 0 hoặc không chạy được ⇒ "failed".
    Trường hợp nào tiến trình con cũng bị kill trước khi hàm trả về; sau khi kill, mỗi lần chờ (luồng đọc/ghi, tiến
    trình thoát) tối đa `kill_grace_s` (`PdfReaderConfig.ocr_kill_grace_s`). Tiến trình không thoát trong hạn đó
    cũng bị kill và kết quả là "timeout".
    """
    try:
        process = subprocess.Popen(
            list(args),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=tesseract_env(),
            shell=False,
            close_fds=True,
        )
    except OSError:
        return RunResult(status="failed")
    chunks: list[bytes] = []
    overflow = threading.Event()

    def drain() -> None:
        stdout = process.stdout
        if stdout is None:
            return
        descriptor = stdout.fileno()

        def read() -> bytes:
            try:
                return os.read(descriptor, READ_CHUNK_BYTES)
            except OSError:
                return b""  # pipe đã đóng vì tiến trình con bị kill

        received = 0
        while chunk := read():
            received += len(chunk)
            if received > max_output_bytes:
                overflow.set()
                with contextlib.suppress(OSError):
                    process.kill()
                return
            chunks.append(chunk)

    feeder = threading.Thread(target=_feed, args=(process.stdin, stdin_data), daemon=True)
    reader = threading.Thread(target=drain, daemon=True)
    feeder.start()
    reader.start()
    reader.join(timeout_s)
    timed_out = reader.is_alive()
    if timed_out and process.poll() is None:
        process.kill()
    # Sau khi kill, pipe đóng và hai luồng kết thúc ngay; hạn chờ chỉ để không treo nếu một tiến trình cháu (nếu
    # có) còn giữ pipe. Luồng là daemon nên không giữ worker lại.
    reader.join(kill_grace_s)
    feeder.join(kill_grace_s)
    returncode = _reap(process, kill_grace_s)
    if process.stdout is not None:
        process.stdout.close()
    if overflow.is_set():
        return RunResult(status="overflow")
    if timed_out or reader.is_alive() or returncode is None:
        return RunResult(status="timeout")
    if returncode != 0:
        return RunResult(status="failed")
    return RunResult(status="ok", stdout=b"".join(chunks))


# --------------------------------------------------------------------------- #
# Unicharset của .traineddata (ADR-008 mục 14, test 1)
# --------------------------------------------------------------------------- #

# Chỉ số thành phần trong file `.traineddata` (enum `TessdataType` của Tesseract).
TESSDATA_UNICHARSET = 1
TESSDATA_LSTM_UNICHARSET = 21
_VIETNAMESE_VOWELS = "aăâeêioôơuưy"
_TONE_MARKS = ("", "\u0300", "\u0301", "\u0309", "\u0303", "\u0323")


def vietnamese_letters() -> frozenset[str]:
    """134 chữ cái tiếng Việt ngoài ASCII (NFC): 66 nguyên âm có dấu và `đ`, mỗi chữ cả thường lẫn hoa."""
    lower = {unicodedata.normalize("NFC", vowel + tone) for vowel in _VIETNAMESE_VOWELS for tone in _TONE_MARKS}
    lower = {letter for letter in lower if not letter.isascii()} | {"đ"}
    return frozenset(lower | {letter.upper() for letter in lower})


def read_unicharset(traineddata: bytes) -> str | None:
    """Văn bản unicharset trong file `.traineddata`: ưu tiên của LSTM, sau đó của engine cũ; không có ⇒ None.

    Định dạng: số thành phần (int32 little-endian), bảng offset int64 (−1 khi vắng), rồi dữ liệu các thành phần;
    mỗi thành phần kéo dài tới offset dương kế tiếp hoặc tới cuối file.
    """
    if len(traineddata) < 4:
        return None
    (count,) = struct.unpack_from("<i", traineddata, 0)
    if count < 1 or 4 + 8 * count > len(traineddata):
        return None
    offsets = struct.unpack_from(f"<{count}q", traineddata, 4)
    for index in (TESSDATA_LSTM_UNICHARSET, TESSDATA_UNICHARSET):
        if index >= count or offsets[index] < 0:
            continue
        start = offsets[index]
        end = min([offset for offset in offsets if offset > start] + [len(traineddata)])
        try:
            return traineddata[start:end].decode("utf-8")
        except UnicodeDecodeError:
            return None
    return None


def unicharset_entries(unicharset: str) -> frozenset[str]:
    """Ký tự của từng mục unicharset (trường đầu mỗi dòng, sau dòng đếm), chuẩn hoá NFC."""
    lines = unicharset.splitlines()[1:]
    return frozenset(unicodedata.normalize("NFC", line.split(" ", 1)[0]) for line in lines if line.strip())


def missing_vietnamese_letters(traineddata: bytes) -> list[str]:
    """Các chữ trong `vietnamese_letters()` mà unicharset của file không có, sắp theo mã; không đọc được ⇒ cả 134."""
    unicharset = read_unicharset(traineddata)
    present = unicharset_entries(unicharset) if unicharset is not None else frozenset()
    return sorted(vietnamese_letters() - present)


# --------------------------------------------------------------------------- #
# TSV của Tesseract
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TsvWord:
    text: str
    box: PixelBox
    conf: float  # [0, 1]


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _unit_box(box: PixelBox, width: int, height: int) -> UnitBBox:
    left, top, right, bottom = box
    return (_clamp(left / width), _clamp(top / height), _clamp(right / width), _clamp(bottom / height))


def parse_tsv_words(tsv: str) -> list[list[TsvWord]]:
    """Từ (level 5) của TSV, gom theo dòng (block, par, line) giữ thứ tự xuất hiện; bỏ từ rỗng.

    `conf` của Tesseract (0–100, −1 khi không có) đổi về [0, 1]. Dòng sai số cột hoặc sai kiểu ném `TesseractError`.
    """
    lines: dict[tuple[int, int, int, int], list[TsvWord]] = {}
    rows = tsv.splitlines()
    for row in rows[1:]:
        if not row:
            continue
        fields = row.split("\t")
        if len(fields) != TSV_COLUMNS:
            raise TesseractError("TSV sai số cột")
        try:
            level, page, block, par, line = (int(v) for v in fields[:5])
            left, top, width, height = (int(v) for v in fields[6:10])
            conf = float(fields[10])
        except ValueError as exc:
            raise TesseractError("TSV sai kiểu dữ liệu") from exc
        text = fields[11].strip()
        if level != TSV_WORD_LEVEL or not text:
            continue
        word = TsvWord(
            text=text[:MAX_RAW_LINE_CHARS],
            box=(left, top, left + max(0, width), top + max(0, height)),
            conf=_clamp(conf / 100.0),
        )
        lines.setdefault((page, block, par, line), []).append(word)
    return list(lines.values())


def _weighted_conf(words: Sequence[TsvWord]) -> float:
    weight = sum(len(word.text) for word in words)
    if weight == 0:
        return 0.0
    return _clamp(sum(word.conf * len(word.text) for word in words) / weight)


def _split_long(words: list[TsvWord]) -> list[list[TsvWord]]:
    """Ngắt dòng có văn bản dài hơn `MAX_RAW_LINE_CHARS` thành nhiều dòng, theo ranh giới từ."""
    pieces: list[list[TsvWord]] = [[]]
    length = 0
    for word in words:
        extra = len(word.text) + (1 if pieces[-1] else 0)
        if pieces[-1] and length + extra > MAX_RAW_LINE_CHARS:
            pieces.append([])
            length, extra = 0, len(word.text)
        pieces[-1].append(word)
        length += extra
    return pieces


def tsv_to_lines(tsv: str, width: int, height: int) -> tuple[list[RawOcrLine], list[PixelBox]]:
    """Dòng OCR (bbox chuẩn hoá theo ảnh, gốc trên-trái) và hộp điểm ảnh của mọi từ (để đếm mực ngoài hộp chữ).

    Vượt `MAX_PAGE_LINES` dòng hoặc `MAX_PAGE_WORDS` từ ném `TesseractError`, như giới hạn của dữ liệu tầng 1.
    """
    grouped = [piece for words in parse_tsv_words(tsv) for piece in _split_long(words)]
    if len(grouped) > MAX_PAGE_LINES or sum(len(words) for words in grouped) > MAX_PAGE_WORDS:
        raise TesseractError("TSV vượt giới hạn số dòng hoặc số từ của một trang")
    lines: list[RawOcrLine] = []
    boxes: list[PixelBox] = []
    for words in grouped:
        raw_words = [RawWord(text=word.text, bbox=_unit_box(word.box, width, height)) for word in words]
        line_box = (
            min(word.box[0] for word in words),
            min(word.box[1] for word in words),
            max(word.box[2] for word in words),
            max(word.box[3] for word in words),
        )
        lines.append(
            RawOcrLine(
                text=" ".join(word.text for word in words),
                bbox=_unit_box(line_box, width, height),
                words=raw_words,
                conf=_weighted_conf(words),
            )
        )
        boxes.extend(word.box for word in words)
    return lines, boxes


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TesseractEngine:
    """Binary Tesseract đã kiểm khi khởi động worker; `run` thay được trong test (không gọi binary thật)."""

    command: str
    tessdata_dir: str | None
    run: Runner = run_bounded

    def _tessdata_args(self) -> list[str]:
        return [] if self.tessdata_dir is None else ["--tessdata-dir", self.tessdata_dir]

    @classmethod
    def probe(
        cls,
        settings: OcrSettings,
        timeout_s: float,
        run: Runner = run_bounded,
        *,
        max_output_bytes: int = DEFAULT_OCR_PROBE_MAX_BYTES,
    ) -> TesseractEngine | None:
        """Kiểm có binary (`--version`) và có ngôn ngữ `vie` (`--list-langs`); thiếu một trong hai ⇒ None.

        Đầu ra của mỗi lệnh kiểm đọc tối đa `max_output_bytes` (`PdfReaderConfig.ocr_probe_max_bytes`).
        """
        engine = cls(command=settings.tesseract_cmd, tessdata_dir=settings.tessdata_dir, run=run)
        limit = max_output_bytes
        if run([engine.command, "--version"], b"", timeout_s, limit).status != "ok":
            return None
        listing = run([engine.command, "--list-langs", *engine._tessdata_args()], b"", timeout_s, limit)
        if listing.status != "ok":
            return None
        try:
            languages = listing.stdout.decode("utf-8").splitlines()[1:]
        except UnicodeDecodeError:
            return None
        if TESSERACT_LANGUAGE not in {language.strip() for language in languages}:
            return None
        return engine

    def recognize(
        self, png: bytes, image: GrayImage, config: PdfReaderConfig
    ) -> tuple[list[RawOcrLine], list[PixelBox]]:
        """OCR một trang: `png` (mã hoá của `image`) qua stdin, TSV qua stdout (tối đa `max_message_bytes` byte)."""
        args = [self.command, "stdin", "stdout", "-l", TESSERACT_LANGUAGE, *self._tessdata_args()]
        args += ["--dpi", str(max(1, round(image.dpi))), "-c", "tessedit_create_tsv=1"]
        result = self.run(args, png, config.tesseract_timeout_s, config.max_message_bytes)
        if result.status != "ok":
            raise TesseractError(f"Tesseract không đọc được trang ({result.status})")
        try:
            tsv = result.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TesseractError("Tesseract ghi byte không phải UTF-8") from exc
        return tsv_to_lines(tsv, image.width, image.height)
