"""Cấu hình bộ nhập PDF (ADR-008 mục 3, 4, 6, 8, 9, 12; SPECIFICATION §5.7, §11.2).

Mọi ngưỡng và giới hạn là field có giá trị mặc định ở đây; code xử lý không ghi cứng con số nào.
Các ngưỡng tên viết hoa trong ADR (`TEXT_LAYER_MIN`…) giữ đúng giá trị khởi đầu của ADR; các ngưỡng phụ của
tầng 1 (ký hiệu toán, bảng, hình) là giá trị khởi đầu chưa hiệu chỉnh bằng phép đo.

Engine OCR (tầng 2) cấu hình bằng biến môi trường `HOLOCRON_PDF_OCR_*`, `HOLOCRON_TESSERACT_CMD`,
`HOLOCRON_TESSDATA_DIR` (spec §11.2), đọc bằng `load_ocr_settings` thành `OcrSettings`. Bộ đọc không có
`OcrSettings` thì coi như không có engine OCR: trang cần OCR thành `failed` (`OCR_ENGINE_MISSING`).
"""

from __future__ import annotations

import os
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

    # `text_score` (ADR-008 mục 6): độ đủ chữ = min(1, số ký tự không trắng / text_full_chars).
    text_full_chars: int = Field(default=200, ge=1)

    # `has_formula` (mục 6, 9): tỉ lệ ký hiệu toán trên số ký tự không trắng, kèm số ký hiệu tối thiểu.
    formula_density_min: float = Field(default=0.03, gt=0.0, le=1.0)
    formula_min_symbols: int = Field(default=3, ge=1)

    # `has_figure` trên PDF số (mục 9): diện tích ảnh nhúng đủ lớn; diện tích đường vẽ chỉ tính khi trang có ảnh.
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


SecondaryEngine = Literal["rapidocr"]


class OcrSettings(BaseModel):
    """Engine OCR của tầng 2 (ADR-008 mục 8, 12). Chỉ chứa đường dẫn và tên engine, không chứa bí mật.

    `tesseract_cmd` là đường dẫn tuyệt đối khi tìm được trong PATH của tiến trình cha (worker chạy với môi trường
    tối thiểu, không có PATH). `tessdata_dir` là None thì dùng thư mục mặc định của bản cài Tesseract.
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


def load_ocr_settings(
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> OcrSettings:
    """Đọc cấu hình OCR từ `env` (mặc định `os.environ`); giá trị sai là `ConfigError` chỉ nêu tên biến.

    - `HOLOCRON_PDF_OCR_PRIMARY`: MVP chỉ nhận `tesseract` (mặc định).
    - `HOLOCRON_PDF_OCR_SECONDARY`: chưa đặt thì là `rapidocr`; ĐẶT RỖNG thì không có lượt thứ hai (spec §11.2 ghi
      riêng cho biến này, ưu tiên hơn quy tắc chung "rỗng coi như chưa đặt"); giá trị khác là `ConfigError`.
    - `HOLOCRON_TESSERACT_CMD`: mặc định `tesseract`, tìm trong PATH của tiến trình cha bằng `which`; không tìm
      thấy thì giữ nguyên, worker sẽ không chạy được binary và coi như thiếu engine.
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
    command = _read_env(source, ENV_TESSERACT_CMD) or DEFAULT_TESSERACT_CMD
    return OcrSettings(
        tesseract_cmd=which(command) or command,
        tessdata_dir=_read_env(source, ENV_TESSDATA_DIR),
        secondary=secondary,
    )
