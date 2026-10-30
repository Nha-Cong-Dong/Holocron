"""Engine chính của tầng 2: binary Tesseract `vie` gọi qua subprocess (ADR-008 mục 8).

Nhận dạng trang (`TesseractEngine.recognize`) chỉ chạy BÊN TRONG worker có giới hạn (`_pdf_worker.py`); Tesseract
là tiến trình con của worker nên chịu cùng giới hạn OS (RLIMIT kế thừa trên Unix, Job Object bao cả tiến trình con
trên Windows). Ảnh PNG do PDFium render đi vào qua stdin, kết quả TSV ra qua stdout, nên Tesseract không ghi file
nào (giữ được `RLIMIT_FSIZE` 0) và không bao giờ đọc file PDF gốc.

Chạy ở TIẾN TRÌNH CHA (app), một lần lúc khởi động, cho kiểm cục bộ của chế độ khả năng
(`services/capability_checks.py`): `TesseractEngine.probe_status` (chỉ `--version` và `--list-langs`, qua
`run_bounded`: không qua shell, có timeout, có trần byte đầu ra, kill khi quá hạn), `listed_tessdata_dir` và các hàm
thuần đọc unicharset trên bytes của `vie.traineddata` (file đọc có trần byte ở tầng gọi). `run_bounded` cũng là bộ
chạy tiến trình con thăm dò RapidOCR (`secondary_probe.py`); tiến trình cha không nhận dạng ảnh nào.

- Gọi bằng danh sách tham số (`shell=False`), môi trường tối thiểu (`SYSTEMROOT` trên Windows), timeout, stdout
  đọc có giới hạn byte; stdout giải mã UTF-8 strict.
- TSV xin bằng `-c tessedit_create_tsv=1` thay vì file cấu hình `tsv`: khi `--tessdata-dir` trỏ tới thư mục chỉ có
  `vie.traineddata`, Tesseract không tìm thấy `configs/tsv` và lặng lẽ trả văn bản thường (đo 2026-10-25 với 5.4.0).
- Hàm đọc unicharset của `.traineddata` (ADR-008 mục 14, test 1) là hàm thuần trên bytes.
- `check_vie_charset` là hàm kiểm bộ chữ DÙNG CHUNG cho kiểm cục bộ lúc khởi động (tiến trình cha) và cho worker
  khi dựng engine (`_pdf_worker.build_ocr`). Nó chỉ kiểm `vie.traineddata` ở ĐÚNG thư mục mà lần gọi OCR thật dùng,
  do MỘT hàm `resolve_tessdata_dir` xác định (duyệt 2026-10-30): app truyền `--tessdata-dir` (`HOLOCRON_TESSDATA_DIR`)
  ⇒ thư mục đó; không truyền mà `--list-langs` ghi thư mục (Tesseract 5.x) ⇒ thư mục đó; không truyền và không ghi
  (Tesseract 4.x) ⇒ đường lùi: thư mục mặc định của bản cài (`PdfReaderConfig.ocr_tessdata_default_dirs`, rồi
  `tessdata` cạnh file chạy trên Windows), thư mục đầu tiên có `vie.traineddata` là file thường. `TESSDATA_PREFIX`
  không tham gia: Tesseract chạy với môi trường tối thiểu (`tesseract_env`) nên không đọc biến này. Thư mục đã xác
  định mà file vắng hoặc không phải file thường ⇒ không dùng được; đường lùi không thấy file ⇒ "không xác định được"
  (ngoại lệ Tesseract 4.x: bỏ qua kiểm). Hàm không ghi log; nơi gọi tự ghi (không ghi đường dẫn).
"""

from __future__ import annotations

import contextlib
import os
import re
import stat
import struct
import subprocess
import sys
import threading
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
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
# Kết quả kiểm Tesseract khi khởi động (`TesseractEngine.probe_status`).
ProbeStatus = Literal["ok", "engine_missing", "language_missing"]
# Dòng đầu của `tesseract --list-langs`: bản 5.x ghi `List of available languages in "<thư mục>" (<số>):`; bản 4.x
# ghi `List of available languages (<số>):`, không có thư mục (khi đó `listed_tessdata_dir` trả None).
_LIST_LANGS_DIR = re.compile(r'"(?P<dir>[^"]+)"')


@dataclass(frozen=True)
class RunResult:
    status: RunStatus
    stdout: bytes = b""


def listed_tessdata_dir(header: str) -> str | None:
    """Thư mục tessdata ghi trong dòng đầu của `--list-langs`; dòng không có thư mục trong ngoặc kép ⇒ None."""
    match = _LIST_LANGS_DIR.search(header)
    return match.group("dir") if match is not None else None


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
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> RunResult:
    """Chạy `args` (không qua shell), ghi `stdin_data` vào stdin, đọc stdout tối đa `max_output_bytes` byte.

    Quá hạn ⇒ "timeout"; stdout vượt giới hạn ⇒ "overflow"; mã thoát khác 0 hoặc không chạy được ⇒ "failed" (mã
    thoát khác 0 thì kèm stdout đã đọc, trong trần byte, để nơi gọi đọc được dòng báo lỗi cuối của tiến trình con).
    Trường hợp nào tiến trình con cũng bị kill trước khi hàm trả về; sau khi kill, mỗi lần chờ (luồng đọc/ghi, tiến
    trình thoát) tối đa `kill_grace_s` (`PdfReaderConfig.ocr_kill_grace_s`). Tiến trình không thoát trong hạn đó
    cũng bị kill và kết quả là "timeout". `env` là môi trường của tiến trình con (mặc định `tesseract_env()`, tối
    thiểu); nơi gọi tự cấp môi trường tối thiểu của mình khi cần thêm biến. `cwd` là thư mục làm việc của tiến trình con
    (mặc định kế thừa cwd của tiến trình cha).
    """
    try:
        process = subprocess.Popen(
            list(args),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=dict(env) if env is not None else tesseract_env(),
            cwd=cwd,
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
        return RunResult(status="failed", stdout=b"".join(chunks))
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


VIE_TRAINEDDATA = f"{TESSERACT_LANGUAGE}.traineddata"
# Kết quả kiểm bộ chữ: "ok" (đủ chữ, có thể thiếu chữ được phép thiếu), "missing_file" (thư mục engine dùng không có
# `vie.traineddata` là file thường: vắng, là thư mục, FIFO, thiết bị…), "unreadable" (file thường mà không đọc được:
# lỗi đọc hoặc vượt trần byte), "missing_letters" (thiếu chữ ngoài danh sách cho phép), "unknown_tessdata" (không xác
# định được thư mục engine dùng và đường lùi không có file ⇒ bỏ qua kiểm).
CharsetStatus = Literal["ok", "missing_file", "unreadable", "missing_letters", "unknown_tessdata"]


@dataclass(frozen=True)
class CharsetCheck:
    """Kết quả `check_vie_charset`: `missing` là mọi chữ thiếu, `outside` là phần nằm ngoài danh sách cho phép."""

    status: CharsetStatus
    missing: frozenset[str] = frozenset()
    outside: frozenset[str] = frozenset()

    @property
    def usable(self) -> bool:
        """Engine dùng được: đủ chữ, hoặc không xác định được tessdata (bỏ qua kiểm, ngoại lệ đã duyệt)."""
        return self.status in ("ok", "unknown_tessdata")


def passed_tessdata_dir(settings: OcrSettings) -> str | None:
    """Thư mục app truyền cho Tesseract bằng `--tessdata-dir` (`HOLOCRON_TESSDATA_DIR`); rỗng hay None ⇒ không truyền.

    `TesseractEngine` dựng từ `settings` dùng đúng giá trị này, nên kiểm bộ chữ và lời gọi OCR không lệch nhau.
    """
    return settings.tessdata_dir or None


def fallback_tessdata_dirs(settings: OcrSettings, default_dirs: Iterable[str], *, platform: str) -> list[str]:
    """Đường lùi khi không xác định được thư mục engine dùng (Tesseract 4.x không ghi thư mục ở `--list-langs`):
    `default_dirs`, rồi (chỉ Windows, khi file chạy là đường dẫn tuyệt đối) `tessdata` cạnh file chạy. Mục rỗng bị
    bỏ (không bao giờ dò thư mục hiện hành).

    `HOLOCRON_TESSDATA_DIR` đứng đầu thứ tự đã duyệt nhưng không cần có ở đây: khi đặt, app luôn truyền nó bằng
    `--tessdata-dir` nên `resolve_tessdata_dir` trả nó trước khi tới đường lùi.
    """
    candidates = list(default_dirs)
    if platform == "win32" and os.path.isabs(settings.tesseract_cmd):
        candidates.append(os.path.join(os.path.dirname(settings.tesseract_cmd), "tessdata"))
    return [candidate for candidate in candidates if candidate]


def resolve_tessdata_dir(
    settings: OcrSettings, listed_dir: str | None, default_dirs: Iterable[str], *, platform: str = sys.platform
) -> str | None:
    """Thư mục tessdata mà lần gọi OCR thật dùng (duyệt 2026-10-30); dùng chung cho kiểm lúc khởi động và worker.

    1. App truyền `--tessdata-dir` (`passed_tessdata_dir`) ⇒ thư mục đó.
    2. Không truyền, `--list-langs` ghi thư mục ở dòng đầu (`listed_dir`, Tesseract 5.x) ⇒ thư mục đó.
    3. Không truyền, không ghi (Tesseract 4.x) ⇒ thư mục đầu tiên trong `fallback_tessdata_dirs` có `vie.traineddata`
       là file thường; mục không phải file thường bị bỏ qua, dò tiếp. Không thư mục nào có ⇒ None.

    Ở bước 1 và 2 thư mục được trả về mà không cần có file: engine sẽ dùng đúng thư mục đó, file vắng là thiếu `vie`.
    `TESSDATA_PREFIX` không tham gia vì Tesseract chạy với môi trường tối thiểu (`tesseract_env`).
    """
    passed = passed_tessdata_dir(settings)
    if passed is not None:
        return passed
    if listed_dir is not None:
        return listed_dir
    for directory in fallback_tessdata_dirs(settings, default_dirs, platform=platform):
        if os.path.isfile(Path(directory) / VIE_TRAINEDDATA):  # lỗi quyền hay đường dẫn hỏng ⇒ False, dò tiếp
            return directory
    return None


def read_bounded(path: Path, max_bytes: int) -> bytes | None:
    """Đọc tối đa `max_bytes` byte của file thường `path`; không phải file thường, lớn hơn hoặc lỗi đọc ⇒ None.

    Mở không chặn (`O_NONBLOCK` trên POSIX) rồi kiểm loại file trên chính descriptor đã mở: FIFO hay thiết bị thay
    vào giữa lúc dò và lúc đọc không làm treo lần kiểm.
    """
    flags = os.O_RDONLY
    if sys.platform == "win32":
        flags |= os.O_BINARY
    else:
        flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except (OSError, ValueError):
        return None
    # Kiểm loại file trước `fdopen`: trên POSIX `os.open` mở được thư mục, còn `fdopen` của thư mục ném
    # `IsADirectoryError` ngoài khối bắt lỗi.
    try:
        regular = stat.S_ISREG(os.fstat(descriptor).st_mode)
    except OSError:
        regular = False
    if not regular:
        os.close(descriptor)
        return None
    with os.fdopen(descriptor, "rb") as handle:
        try:
            data = handle.read(max_bytes + 1)
        except OSError:
            return None
    return data if len(data) <= max_bytes else None


def check_vie_charset(
    settings: OcrSettings, listed_dir: str | None, config: PdfReaderConfig, *, platform: str = sys.platform
) -> CharsetCheck:
    """Kiểm unicharset của `vie.traineddata` (ADR-008 mục 14); dùng chung cho tiến trình cha và worker.

    Thư mục do `resolve_tessdata_dir` xác định. Chữ trong `config.ocr_charset_allowed_missing` được phép thiếu; file
    đọc tối đa `config.ocr_traineddata_max_bytes`.
    """
    directory = resolve_tessdata_dir(settings, listed_dir, config.ocr_tessdata_default_dirs, platform=platform)
    if directory is None:
        return CharsetCheck(status="unknown_tessdata")
    path = Path(directory) / VIE_TRAINEDDATA
    if not os.path.isfile(path):
        return CharsetCheck(status="missing_file")
    data = read_bounded(path, config.ocr_traineddata_max_bytes)
    if data is None:
        return CharsetCheck(status="unreadable")
    missing = frozenset(missing_vietnamese_letters(data))
    outside = missing - config.ocr_charset_allowed_missing
    return CharsetCheck(status="missing_letters" if outside else "ok", missing=missing, outside=outside)


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
        outcome = cls.probe_status(settings, timeout_s, run, max_output_bytes=max_output_bytes)
        return outcome.engine if outcome.status == "ok" else None

    @classmethod
    def probe_status(
        cls,
        settings: OcrSettings,
        timeout_s: float,
        run: Runner = run_bounded,
        *,
        max_output_bytes: int = DEFAULT_OCR_PROBE_MAX_BYTES,
    ) -> ProbeOutcome:
        """Như `probe` (cùng lệnh, cùng thứ tự, mỗi lệnh chạy đúng một lần) nhưng nói rõ lý do.

        `--version` không chạy được ⇒ "engine_missing"; `--list-langs` lỗi, sai UTF-8 hoặc không có `vie` ⇒
        "language_missing". Khi "ok", `listed_tessdata_dir` là thư mục tessdata Tesseract ghi ở dòng đầu của
        `--list-langs` (None nếu dòng đó không ghi thư mục).
        """
        engine = cls(command=settings.tesseract_cmd, tessdata_dir=passed_tessdata_dir(settings), run=run)
        limit = max_output_bytes
        if run([engine.command, "--version"], b"", timeout_s, limit).status != "ok":
            return ProbeOutcome(engine, "engine_missing")
        listing = run([engine.command, "--list-langs", *engine._tessdata_args()], b"", timeout_s, limit)
        if listing.status != "ok":
            return ProbeOutcome(engine, "language_missing")
        try:
            lines = listing.stdout.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            return ProbeOutcome(engine, "language_missing")
        languages = lines[1:]
        if TESSERACT_LANGUAGE not in {language.strip() for language in languages}:
            return ProbeOutcome(engine, "language_missing")
        return ProbeOutcome(engine, "ok", listed_tessdata_dir(lines[0]))

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


@dataclass(frozen=True)
class ProbeOutcome:
    """Kết quả `TesseractEngine.probe_status`: engine (đã dựng, dùng được khi "ok"), lý do và thư mục tessdata."""

    engine: TesseractEngine
    status: ProbeStatus
    listed_tessdata_dir: str | None = None
