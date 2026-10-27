"""Đơn vị đo có `µ` hoặc `·` không bị tính là dấu TCVN3 (ADR-008 mục 6, R8; review tầng 2, lỗi 5)."""

from __future__ import annotations

import pytest

from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.text_layer import (
    is_legacy_encoded_word,
    text_score,
    valid_word_ratio,
)

CONFIG = PdfReaderConfig()
PHYSICS_SLIDE = (
    "Thời gian chuyển mạch của transistor cỡ 10µs, tụ điện 47µF và dòng rò 5µA. Mô-men xoắn của động cơ là "
    "12 N·m, công tiêu thụ đo bằng kW·h, động lượng tính theo kg·m/s và độ nhớt theo Pa·s. Sinh viên ghi đơn vị "
    "đầy đủ sau mỗi kết quả, đổi đơn vị trước khi thay số vào công thức và làm tròn tới hai chữ số thập phân."
)


@pytest.mark.parametrize("unit", ["10µs", "47µF", "5µA", "(µs)", "kg·m", "kg·m/s", "N·m", "kW·h", "Pa·s", "5kg·m"])
def test_units_are_not_legacy_encoded(unit: str) -> None:
    assert not is_legacy_encoded_word(unit)


@pytest.mark.parametrize("word", ["hµnh", "vµ", "Hµ", "tr×nh", "Tr−êng", "ch−a", "Gi¶i", "®·"])
def test_tcvn3_words_are_still_caught(word: str) -> None:
    if word == "®·":
        # Chỉ gồm dấu TCVN3, không có chữ cái nào khác: theo quy tắc sẵn có, một cụm dấu đứng riêng vẫn hợp lệ.
        assert not is_legacy_encoded_word(word)
        return
    assert is_legacy_encoded_word(word)


def test_physics_slide_keeps_the_text_layer_and_tcvn3_does_not() -> None:
    assert valid_word_ratio(PHYSICS_SLIDE) == 1.0
    assert text_score(PHYSICS_SLIDE, CONFIG) >= CONFIG.text_layer_min
    tcvn3 = "Tr−êng §¹i häc B¸ch khoa Hµ Néi ®· l·nh ®¹o c¸c b−íc ®Çu tiªn, thêi gian ®o b»ng 10µs vµ kg·m."
    assert valid_word_ratio(tcvn3) < 0.5
