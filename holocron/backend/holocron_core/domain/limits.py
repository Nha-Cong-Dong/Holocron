"""Giới hạn dùng chung giữa bộ đọc tài liệu và Knowledge Vault (SPECIFICATION §5.7; ADR-004).

`MAX_LINE_CHARS` là độ dài tối đa của một dòng văn bản, tính sau chuẩn hoá NFC: `ExtractedLine.text`
(`domain.documents`) và mỗi dòng của `ConfirmedPage` (`domain.vault`) cùng dùng hằng này. Bộ đọc cắt dòng dài theo
đúng giới hạn đó, nên mọi dòng nó trả ra đều vào được Vault; vì vậy không cần mã lỗi riêng cho dòng quá dài.
`tests/test_line_limit_contract.py` khoá: chỉ có một phép gán hằng này trong `holocron_core`.
"""

from __future__ import annotations

MAX_LINE_CHARS = 2000
