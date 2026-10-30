"""Cấu hình bộ nhập PDF (ADR-008 mục 3, 4, 6, 8, 9, 12; SPECIFICATION §5.7, §11.2).

Mọi ngưỡng và giới hạn là field có giá trị mặc định ở đây; code xử lý không ghi cứng con số nào.
Các ngưỡng tên viết hoa trong ADR (`TEXT_LAYER_MIN`…) giữ đúng giá trị khởi đầu của ADR; các ngưỡng phụ của
tầng 1 (ký hiệu toán, bảng, hình) là giá trị khởi đầu chưa hiệu chỉnh bằng phép đo.

Engine OCR (tầng 2) cấu hình bằng biến môi trường `HOLOCRON_PDF_OCR_*`, `HOLOCRON_TESSERACT_CMD`,
`HOLOCRON_TESSDATA_DIR` (spec §11.2), đọc bằng `load_ocr_settings` thành `OcrSettings`. `TESSDATA_PREFIX` KHÔNG được
đọc: worker chạy Tesseract với môi trường tối thiểu nên engine không thấy biến này (duyệt 2026-10-30). Bộ đọc không có
`OcrSettings` thì coi như không có engine OCR: trang cần OCR thành `failed` (`OCR_ENGINE_MISSING`).
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..llm_config import ConfigError

ENV_PDF_OCR_PRIMARY = "HOLOCRON_PDF_OCR_PRIMARY"
ENV_PDF_OCR_SECONDARY = "HOLOCRON_PDF_OCR_SECONDARY"
ENV_TESSERACT_CMD = "HOLOCRON_TESSERACT_CMD"
ENV_TESSDATA_DIR = "HOLOCRON_TESSDATA_DIR"

DEFAULT_TESSERACT_CMD = "tesseract"
TESSERACT_LANGUAGE = "vie"

# Hướng dẫn hiện trên màn hình xem lại cho trang `failed` vì `OCR_ENGINE_MISSING` (spec §5.7; ADR-008 mục 8).
OCR_INSTALL_HINT = (
    "Trang này cần OCR nhưng máy chưa có Tesseract hoặc gói tiếng Việt. Hãy cài Tesseract 5 và tải "
    "`vie.traineddata` (tessdata_best), rồi đặt HOLOCRON_TESSERACT_CMD trỏ tới file chạy `tesseract` và "
    "HOLOCRON_TESSDATA_DIR trỏ tới thư mục chứa `vie.traineddata`. Bạn vẫn có thể tự gõ văn bản cho trang này."
)

MIB = 1024 * 1024
GIB = 1024 * MIB

# Giá trị mặc định dùng chung giữa field của `PdfReaderConfig` và tham số mặc định của các hàm cấp thấp (gọi được
# riêng trong test hay script), để mỗi con số chỉ ghi ở một chỗ.
DEFAULT_OCR_KILL_GRACE_S = 5.0
DEFAULT_OCR_PROBE_MAX_BYTES = 64 * 1024
DEFAULT_RENDER_SHRINK_FACTOR = 0.995
DEFAULT_CROSS_CHECK_MAX_LINES = 5000
DEFAULT_CROSS_CHECK_MAX_LINE_TOKENS = 512
DEFAULT_CROSS_CHECK_MAX_LINE_PAIRS = 200_000
DEFAULT_CROSS_CHECK_MAX_TOKEN_CELLS = 250_000

# Chữ tiếng Việt hiếm đã duyệt (2026-10-25, ADR-008 mục 14) được phép thiếu trong unicharset của engine chính:
# `vie.traineddata` bản best thiếu đúng hai chữ này. Mở rộng tập này cần người dùng duyệt riêng, nên field
# `ocr_charset_allowed_missing` chỉ nhận tập con của nó.
OCR_CHARSET_APPROVED_MISSING: frozenset[str] = frozenset({"Ỵ", "ỵ"})

# Thư mục tessdata mặc định của các bản cài Tesseract thường gặp trên Linux (gói 5.x, gói 4.x, bản dựng chung): đường
# lùi khi kiểm bộ chữ mà app không truyền `--tessdata-dir` và `--list-langs` không ghi thư mục (Tesseract 4.x, duyệt
# 2026-10-30). Trên Windows còn dò thêm `tessdata` cạnh file chạy Tesseract (bộ cài thường đặt ở đó).
TESSDATA_DEFAULT_DIRS: tuple[str, ...] = (
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
    "/usr/share/tessdata",
)


class PdfReaderConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    # Giới hạn đầu vào (ADR-008 mục 4).
    max_bytes: int = Field(default=50 * MIB, ge=1)
    max_pages: int = Field(default=300, ge=1)

    # Worker và thời gian (ADR-008 mục 3). Khởi động worker (nạp Python và thư viện) có hạn riêng để máy chậm
    # không bị tính thành "không mở được tài liệu".
    worker_startup_timeout_s: float = Field(default=30.0, gt=0)
    open_timeout_s: float = Field(default=5.0, gt=0)
    text_layer_page_timeout_s: float = Field(default=5.0, gt=0)
    worker_mem_bytes: int = Field(default=2 * GIB, ge=256 * MIB)
    worker_cpu_seconds_per_page: int = Field(default=5, ge=1)
    # Hạn tổng cho cả tài liệu, chỉ tính thời gian bộ đọc làm việc (không tính lúc nơi gọi xử lý trang đã nhận).
    # 300 trang × 5 s là 1500 s; 600 s đủ cho tài liệu số bình thường (vài chục ms mỗi trang) mà vẫn chặn được file
    # khiến mọi trang đều chạm timeout. Hết hạn thì các trang còn lại thành `failed` (`PAGE_TIMEOUT`).
    document_timeout_s: float = Field(default=600.0, gt=0)
    # Giới hạn byte của MỘT thông điệp worker gửi qua stdout (một dòng, tính cả ký tự xuống dòng). Tiến trình cha
    # không có giới hạn OS, nên dòng dài hơn mức này bị cắt ngay khi đọc: worker bị kill, trang thành `PAGE_ERROR`.
    # Một trang A4 dày đặc (vài nghìn từ, mỗi từ kèm bbox) cỡ vài trăm KiB JSON; 8 MiB cao hơn hàng chục lần mà vẫn
    # giữ bộ nhớ của tiến trình cha ở mức vài chục MiB cho mỗi worker.
    max_message_bytes: int = Field(default=8 * MIB, ge=1024)

    # Tầng 2 (ADR-008 mục 3, 4, 8). Trang cần OCR được tiến trình cha chờ tối đa `ocr_page_timeout_s` (30 s theo
    # mục 3) sau khi worker báo bắt đầu OCR. Worker có bật OCR nhận hạn CPU `ocr_cpu_seconds_per_page` mỗi trang
    # thay cho `worker_cpu_seconds_per_page`, vì RapidOCR chạy nhiều luồng trong chính worker (CPU tính cộng dồn).
    ocr_page_timeout_s: float = Field(default=30.0, gt=0)
    ocr_cpu_seconds_per_page: int = Field(default=60, ge=1)
    # Render 300 DPI, tối đa 25 megapixel mỗi trang; trang lớn hơn thì giảm DPI (mục 4).
    ocr_dpi: int = Field(default=300, ge=36, le=1200)
    ocr_max_pixels: int = Field(default=25_000_000, ge=10_000)
    # Kiểm Tesseract khi khởi động worker (`--version`, `--list-langs`) và hạn của một lần nhận dạng trang; hạn này
    # nhỏ hơn `ocr_page_timeout_s` để còn thời gian cho lượt đọc thứ hai.
    ocr_probe_timeout_s: float = Field(default=10.0, gt=0)
    tesseract_timeout_s: float = Field(default=20.0, gt=0)
    # Đầu ra tối đa (byte) của `--version` và `--list-langs` khi kiểm Tesseract.
    ocr_probe_max_bytes: int = Field(default=DEFAULT_OCR_PROBE_MAX_BYTES, ge=1024)
    # Sau khi kill Tesseract (quá hạn, đầu ra quá lớn), thời gian chờ tối đa để pipe đóng và tiến trình thoát hẳn;
    # quá mức này thì worker thôi chờ (luồng đọc là daemon), nên một tiến trình con treo không giữ worker mãi.
    ocr_kill_grace_s: float = Field(default=DEFAULT_OCR_KILL_GRACE_S, gt=0)
    # `OCR_CHARSET_ALLOWED_MISSING` (spec §5.7; ADR-008 mục 14): chữ được phép thiếu trong unicharset của
    # `vie.traineddata`, kèm cảnh báo; thiếu chữ ngoài tập này thì coi như thiếu gói `vie`.
    ocr_charset_allowed_missing: frozenset[str] = OCR_CHARSET_APPROVED_MISSING
    # Trần byte khi đọc `vie.traineddata` để kiểm unicharset lúc khởi động (bản best khoảng 12 MiB); file lớn hơn
    # coi như không đọc được.
    ocr_traineddata_max_bytes: int = Field(default=64 * MIB, ge=1024)
    # Thư mục tessdata mặc định, chỉ dò khi không xác định được thư mục engine dùng (`tesseract.resolve_tessdata_dir`);
    # test thay bằng thư mục tạm để không phụ thuộc máy chạy test.
    ocr_tessdata_default_dirs: tuple[str, ...] = TESSDATA_DEFAULT_DIRS
    # Hệ số giảm dần tỉ lệ render khi ảnh làm tròn lên vẫn vượt `ocr_max_pixels` (mục 4).
    render_shrink_factor: float = Field(default=DEFAULT_RENDER_SHRINK_FACTOR, gt=0.0, lt=1.0)
    # Nhị phân hoá ảnh xám để đếm điểm ảnh mực (`ink_outside_text_ratio`, mục 9): điểm ảnh tối hơn ngưỡng là mực.
    ink_threshold: int = Field(default=128, ge=1, le=255)
    # Ghép dòng hai lượt đọc theo hộp toạ độ (mục 11): hai hộp phải chồng nhau theo chiều dọc ít nhất tỉ lệ này
    # (tính trên chiều cao của hộp thấp hơn) và có phần chồng theo chiều ngang. Giá trị khởi đầu, chưa hiệu chỉnh.
    line_match_min_overlap: float = Field(default=0.5, gt=0.0, le=1.0)
    # Trần độ phức tạp của đối chiếu chéo (mục 11). Việc đối chiếu chạy ở tiến trình cha, nơi không có giới hạn OS,
    # trên dữ liệu của worker (không tin cậy), nên mọi vòng lặp đều có trần: số dòng mỗi lượt, số token mỗi dòng,
    # số cặp dòng được xét khi ghép (chỉ các cặp chồng nhau theo trục y, qua index sắp theo mép trên) và số ô của
    # bảng gióng token (quy hoạch động O(n·m) mỗi dòng, cộng dồn cả trang). Vượt trần nào thì trang coi như không có
    # lượt đối chiếu (`cross_check = unavailable`, `needs_review`), không bao giờ coi là khớp. Trang A4 dày đặc có
    # vài trăm dòng và vài chục token mỗi dòng, thấp hơn các trần này nhiều lần.
    cross_check_max_lines: int = Field(default=DEFAULT_CROSS_CHECK_MAX_LINES, ge=1)
    cross_check_max_line_tokens: int = Field(default=DEFAULT_CROSS_CHECK_MAX_LINE_TOKENS, ge=1)
    cross_check_max_line_pairs: int = Field(default=DEFAULT_CROSS_CHECK_MAX_LINE_PAIRS, ge=1)
    cross_check_max_token_cells: int = Field(default=DEFAULT_CROSS_CHECK_MAX_TOKEN_CELLS, ge=1)

    # Ngưỡng của ADR-008 mục 6, 10, 11, 12.
    text_layer_min: float = Field(default=0.85, ge=0.0, le=1.0)
    text_score_min: float = Field(default=0.80, ge=0.0, le=1.0)
    ocr_score_min: float = Field(default=0.80, ge=0.0, le=1.0)
    line_conf_min: float = Field(default=0.70, ge=0.0, le=1.0)
    coverage_min: float = Field(default=0.85, ge=0.0, le=1.0)
    figure_ink_min: float = Field(default=0.10, ge=0.0, le=1.0)
    cross_line_cer_warn: float = Field(default=0.05, ge=0.0, le=1.0)

    # Chọn tầng (ADR-008 mục 6; sửa đổi 2026-10-29): `text_score` chỉ đo lớp chữ có rác hay không, ít chữ không phải
    # lý do bỏ lớp chữ. Ngoại lệ là trang ảnh: lớp chữ sạch nhưng có dưới `text_sparse_chars` ký tự không trắng
    # (thường chỉ còn tiêu đề khung slide và số trang) trong khi ảnh nhúng phủ ít nhất `text_sparse_image_min` diện
    # tích trang (diện tích hợp) ⇒ OCR cả trang; "dưới" là so sánh `<`. Đo trên 3 chương slide thật (ADR-008 mục 6):
    # trang ảnh có 2–31 ký tự, ảnh phủ 0,434–0,86 (ch2 tr.32: 31 ký tự / 0,558; tr.46: 2 ký tự / 0,434); trang giữ
    # lớp chữ gần biên nhất có 34 ký tự / 0,542. Giá trị khởi đầu, hiệu chỉnh tiếp bằng `scripts/eval_pdf_tiers`.
    text_sparse_chars: int = Field(default=32, ge=1)
    text_sparse_image_min: float = Field(default=0.40, gt=0.0, le=1.0)
    # Trang ảnh phủ gần kín (sửa đổi 2026-10-30): lớp chữ sạch, có dưới `text_full_image_max_chars` ký tự không trắng
    # (so sánh `<`) trong khi ảnh nhúng phủ ít nhất `text_full_image_min` diện tích trang (diện tích hợp) ⇒ OCR cả
    # trang, vì phần chữ còn lại chỉ là tiêu đề, header, số trang nằm dưới ảnh. Trần ký tự giữ lớp chữ cho PDF scan đã
    # có lớp chữ đầy đủ (ảnh phủ cả trang, chữ ẩn bên trên). Đo trên 3 chương slide thật: ch4 tr.22 (sơ đồ là ảnh) có
    # 64 ký tự / ảnh 0,98; trang giữ lớp chữ có ảnh lớn nhất là 0,74. `text_full_image_min` phải ≥
    # `text_sparse_image_min` (luật này chỉ nới luật trang ảnh ít chữ, không thay nó).
    text_full_image_min: float = Field(default=0.90, gt=0.0, le=1.0)
    text_full_image_max_chars: int = Field(default=200, ge=1)

    # `has_formula` (mục 6, 9): tỉ lệ ký hiệu toán trên số ký tự không trắng, kèm số ký hiệu tối thiểu.
    formula_density_min: float = Field(default=0.03, gt=0.0, le=1.0)
    formula_min_symbols: int = Field(default=3, ge=1)

    # Ảnh template (sửa đổi 2026-10-30): ảnh nhúng cùng nội dung (SHA-256 dữ liệu thô của luồng ảnh + kích thước
    # điểm ảnh, số bit mỗi điểm ảnh, bộ lọc) và cùng vị trí, kích thước trên trang (hộp chuẩn hoá theo khổ trang đã
    # xoay, làm tròn theo bước `template_image_bbox_step`) xuất hiện trên ít nhất `template_image_min_pages` trang VÀ
    # ít nhất `template_image_min_page_ratio` tổng số trang của tài liệu (so sánh `≥`). Ảnh template không được tính
    # vào độ phủ ảnh của mọi luật dựa trên độ phủ (trang ảnh ít chữ, ảnh phủ gần kín, phần ảnh của cờ hình). Tài liệu
    # dưới `template_image_min_pages` trang không có ảnh template; trần dưới là 3 để tài liệu 1–2 trang luôn như vậy.
    template_image_min_page_ratio: float = Field(default=0.60, gt=0.0, le=1.0)
    template_image_min_pages: int = Field(default=3, ge=3)
    template_image_bbox_step: float = Field(default=0.01, ge=1e-4, le=0.1)
    # Lượt quét ảnh cấp tài liệu trong worker, trước trang đầu, không render: hạn riêng `template_scan_timeout_s`,
    # không tính vào `open_timeout_s` hay hạn trang; thời gian chờ lượt quét được cộng vào hạn tổng của tài liệu, và
    # `ceil(template_scan_timeout_s)` giây CPU được cộng vào giới hạn CPU của worker có quét. Trần số ảnh mỗi trang và
    # cả tài liệu, số byte đem băm mỗi ảnh và cả tài liệu. Vượt trần của trang hay của tài liệu, quá hạn hoặc lỗi ⇒
    # không ảnh nào là template (hành vi cũ). Ảnh lớn hơn `template_scan_max_image_bytes` không có khoá nên không bao
    # giờ là template.
    template_scan_timeout_s: float = Field(default=20.0, gt=0)
    template_scan_max_images_per_page: int = Field(default=256, ge=1)
    template_scan_max_images: int = Field(default=20_000, ge=1)
    template_scan_max_image_bytes: int = Field(default=16 * MIB, ge=1)
    template_scan_max_bytes: int = Field(default=512 * MIB, ge=1)

    # `has_figure` trên PDF số (mục 9): nhánh ảnh dùng diện tích ảnh nhúng KHÔNG phải template; nhánh đường vẽ tính
    # mọi ảnh, kể cả template, như develop (sửa đổi 2026-10-30).
    figure_image_area_min: float = Field(default=0.10, gt=0.0, le=1.0)
    figure_path_area_min: float = Field(default=0.25, gt=0.0, le=1.0)

    # `has_table` theo lưới toạ độ chữ (mục 6): khoảng trống giữa hai ô tính theo chiều cao dòng; các dòng liền
    # nhau có cùng số ô và mép trái các ô thẳng hàng trong `table_col_tolerance` (tỉ lệ bề ngang trang).
    table_gap_em: float = Field(default=1.5, gt=0.0)
    table_min_rows: int = Field(default=3, ge=2)
    table_min_cols: int = Field(default=2, ge=2)
    table_col_tolerance: float = Field(default=0.02, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _tesseract_fits_in_page(self) -> PdfReaderConfig:
        """Hạn của một lần chạy Tesseract phải nhỏ hơn hạn của cả trang OCR, để còn thời gian cho lượt thứ hai."""
        if self.tesseract_timeout_s >= self.ocr_page_timeout_s:
            raise ValueError("tesseract_timeout_s phải nhỏ hơn ocr_page_timeout_s")
        return self

    @model_validator(mode="after")
    def _only_approved_missing_letters(self) -> PdfReaderConfig:
        """Danh sách chữ được phép thiếu chỉ gồm các chữ hiếm đã duyệt (spec §5.7)."""
        if not self.ocr_charset_allowed_missing <= OCR_CHARSET_APPROVED_MISSING:
            approved = ", ".join(sorted(OCR_CHARSET_APPROVED_MISSING))
            raise ValueError(f"ocr_charset_allowed_missing chỉ được chứa các chữ đã duyệt ({approved})")
        return self

    @model_validator(mode="after")
    def _full_image_not_below_sparse_image(self) -> PdfReaderConfig:
        """Ngưỡng trang ảnh phủ gần kín không được thấp hơn ngưỡng ảnh của luật trang ảnh ít chữ."""
        if self.text_full_image_min < self.text_sparse_image_min:
            raise ValueError("text_full_image_min phải lớn hơn hoặc bằng text_sparse_image_min")
        return self

    @model_validator(mode="after")
    def _template_scan_caps_are_ordered(self) -> PdfReaderConfig:
        """Trần mỗi trang (mỗi ảnh) không vượt trần cả tài liệu, cho số ảnh và số byte đem băm."""
        if self.template_scan_max_images_per_page > self.template_scan_max_images:
            raise ValueError("template_scan_max_images_per_page phải nhỏ hơn hoặc bằng template_scan_max_images")
        if self.template_scan_max_image_bytes > self.template_scan_max_bytes:
            raise ValueError("template_scan_max_image_bytes phải nhỏ hơn hoặc bằng template_scan_max_bytes")
        return self


SecondaryEngine = Literal["rapidocr"]


class OcrSettings(BaseModel):
    """Engine OCR của tầng 2 (ADR-008 mục 8, 12). Chỉ chứa đường dẫn và tên engine, không chứa bí mật.

    `tesseract_cmd` là đường dẫn tuyệt đối khi tìm được trong PATH của tiến trình cha (worker chạy với môi trường
    tối thiểu, không có PATH). `tessdata_dir` (`HOLOCRON_TESSDATA_DIR`) khác rỗng thì được truyền cho Tesseract bằng
    `--tessdata-dir`; None thì Tesseract dùng thư mục mặc định của bản cài.
    `secondary` là None thì không có lượt đọc thứ hai (`cross_check = unavailable`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tesseract_cmd: str = Field(min_length=1)
    tessdata_dir: str | None = None
    secondary: SecondaryEngine | None = "rapidocr"


def _read_env(env: Mapping[str, str], name: str) -> str | None:
    """Giá trị đã cắt khoảng trắng; chuỗi rỗng coi như chưa đặt (spec §11.2)."""
    value = env.get(name, "").strip()
    return value or None


_WINDOWS_DRIVE_PATH = re.compile(r"[A-Za-z]:[\\/]")


def _has_root(value: str, *, sep: str, altsep: str | None) -> bool:
    """Dạng đường dẫn coi như đã có gốc: bắt đầu bằng `/` hoặc có ổ đĩa (`C:/x`, `C:\\x`) trên mọi nền tảng.

    Dạng bắt đầu bằng gạch ngược (`\\x`) chỉ có gốc khi gạch ngược là dấu phân cách của nền tảng (`sep` hoặc
    `altsep`, tức Windows); trên POSIX nó là tên tương đối. Hàm thuần nhận `sep`/`altsep` làm tham số để kiểm được
    cả hai nhánh trên mọi máy.
    """
    if value[:1] == "/" or _WINDOWS_DRIVE_PATH.match(value):
        return True
    backslash_separates = sep == "\\" or (altsep is not None and "\\" in altsep)
    return backslash_separates and value[:1] == "\\"


def _absolute_path(value: str) -> str:
    """Đường dẫn tương đối đổi sang tuyệt đối theo cwd hiện tại; đã tuyệt đối hay có gốc (`_has_root`) giữ nguyên.

    Lần kiểm lúc khởi động (cwd của app) và worker (cwd tạm) phải thấy cùng một đường dẫn.
    """
    if os.path.isabs(value) or _has_root(value, sep=os.sep, altsep=os.altsep):
        return value
    return os.path.abspath(value)


def _absolute_command(command: str) -> str:
    """Lệnh có dấu phân cách đường dẫn mà tương đối ⇒ tuyệt đối; tên lệnh trần giữ nguyên để tra PATH."""
    has_separator = "/" in command or "\\" in command
    return _absolute_path(command) if has_separator else command


def load_ocr_settings(
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> OcrSettings:
    """Đọc cấu hình OCR từ `env` (mặc định `os.environ`); giá trị sai là `ConfigError` chỉ nêu tên biến.

    - `HOLOCRON_PDF_OCR_PRIMARY`: MVP chỉ nhận `tesseract` (mặc định).
    - `HOLOCRON_PDF_OCR_SECONDARY`: chưa đặt thì là `rapidocr`; ĐẶT RỖNG thì không có lượt thứ hai (spec §11.2 ghi
      riêng cho biến này, ưu tiên hơn quy tắc chung "rỗng coi như chưa đặt"); giá trị khác là `ConfigError`.
    - `HOLOCRON_TESSERACT_CMD`: mặc định `tesseract`, tìm trong PATH của tiến trình cha bằng `which`; không tìm
      thấy thì giữ nguyên, worker sẽ không chạy được binary và coi như thiếu engine. Dạng đường dẫn tương đối (có dấu
      phân cách) đổi sang tuyệt đối theo cwd lúc đọc; tên lệnh trần giữ nguyên.
    - `HOLOCRON_TESSDATA_DIR`: dạng tương đối đổi sang tuyệt đối theo cwd lúc đọc.
    """
    source = os.environ if env is None else env
    primary = _read_env(source, ENV_PDF_OCR_PRIMARY) or "tesseract"
    if primary != "tesseract":
        raise ConfigError(f"{ENV_PDF_OCR_PRIMARY} không hợp lệ: MVP chỉ nhận 'tesseract'")
    secondary: SecondaryEngine | None = "rapidocr"
    if ENV_PDF_OCR_SECONDARY in source:
        raw_secondary = _read_env(source, ENV_PDF_OCR_SECONDARY)
        if raw_secondary is None:
            secondary = None
        elif raw_secondary != "rapidocr":
            raise ConfigError(f"{ENV_PDF_OCR_SECONDARY} không hợp lệ: chỉ nhận 'rapidocr' hoặc để rỗng")
    command = _absolute_command(_read_env(source, ENV_TESSERACT_CMD) or DEFAULT_TESSERACT_CMD)
    tessdata_dir = _read_env(source, ENV_TESSDATA_DIR)
    return OcrSettings(
        tesseract_cmd=which(command) or command,
        tessdata_dir=None if tessdata_dir is None else _absolute_path(tessdata_dir),
        secondary=secondary,
    )
