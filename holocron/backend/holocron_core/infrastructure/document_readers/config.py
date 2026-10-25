"""Cấu hình bộ nhập PDF (ADR-008 mục 3, 4, 6, 9, 12; SPECIFICATION §5.7, §11.2).

Mọi ngưỡng và giới hạn là field có giá trị mặc định ở đây; code xử lý không ghi cứng con số nào.
Các ngưỡng tên viết hoa trong ADR (`TEXT_LAYER_MIN`…) giữ đúng giá trị khởi đầu của ADR; các ngưỡng phụ của
tầng 1 (ký hiệu toán, bảng, hình) là giá trị khởi đầu chưa hiệu chỉnh bằng phép đo.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

MIB = 1024 * 1024
GIB = 1024 * MIB


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
