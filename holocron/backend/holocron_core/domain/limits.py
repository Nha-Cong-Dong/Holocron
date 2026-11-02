"""Giới hạn dùng chung giữa bộ đọc tài liệu và Knowledge Vault (SPECIFICATION §5.7; ADR-004).

`MAX_LINE_CHARS` là độ dài tối đa của một dòng văn bản, tính sau chuẩn hoá NFC: `ExtractedLine.text`
(`domain.documents`) và mỗi dòng của `ConfirmedPage` (`domain.vault`) cùng dùng hằng này. Bộ đọc cắt dòng dài theo
đúng giới hạn đó, nên mọi dòng nó trả ra đều vào được Vault; vì vậy không cần mã lỗi riêng cho dòng quá dài.
`tests/test_line_limit_contract.py` khoá: chỉ có một phép gán hằng này trong `holocron_core`.
"""

from __future__ import annotations

MAX_LINE_CHARS = 2000

# Trần dữ liệu cho bộ dựng khối (ADR-010 mục 5; SPECIFICATION §5.7 "Dữ liệu cho bộ dựng khối"). `protocol.py` của bộ
# đọc PDF re-export các hằng này; `PageGeometry` (`domain.documents`) dùng chúng làm trần. Không nâng trần nào của
# ADR-008. `MAX_PAGE_GEOMETRY_LINES` có tên riêng để không trùng `MAX_PAGE_LINES = 5000` của `domain/vault.py`.
MAX_PAGE_GEOMETRY_LINES = 10_000
MAX_PAGE_WORDS = 50_000
MAX_PAGE_FONTS = 256
FONT_NAME_MAX_CHARS = 64
FONT_SIZE_MIN_PT = 1.0
FONT_SIZE_MAX_PT = 1000.0
MAX_PAGE_RULES = 20_000
# Trần byte JSON của `PageGeometry`: hằng domain, KHÔNG phải field `PdfReaderConfig` (cùng giá trị mặc định với
# `max_message_bytes` của ADR-008).
MAX_GEOMETRY_JSON_BYTES = 8 * 1024 * 1024
# Số khối tối đa mỗi trang (ADR-010 mục 5); bộ dựng khối vượt trần ⇒ trang `LAYOUT_FAILED`.
MAX_PAGE_BLOCKS = 10_000
