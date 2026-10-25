"""Heuristic TCVN3/VNI của `text_score` không bắt nhầm biểu thức toán (ADR-008 mục 6, R8)."""

from __future__ import annotations

import pytest

from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.document_readers.text_layer import (
    is_legacy_encoded_word,
    text_score,
    valid_word_ratio,
)

CONFIG = PdfReaderConfig()

# Slide Xác suất thống kê ở PDF số: ký hiệu toán trùng mã với dấu TCVN3 (−, ×, ·, µ).
STATISTICS_SLIDE = (
    "Cho biến ngẫu nhiên X có kỳ vọng µ và phương sai σ². Với hai biến cố bất kỳ ta có P(A∩B) = P(A)·P(B|A), "
    "và nếu A, B độc lập thì P(A∩B) = P(A)·P(B). Ví dụ 2·3 = 6 cách chọn, độ lệch x−1 so với giá trị trung bình, "
    "diện tích a×b của hình chữ nhật, thời gian đáp ứng đo bằng µs. Phương sai mẫu chia cho n−1 thay vì n."
)


@pytest.mark.parametrize("expression", ["x−1", "a×b", "µs", "2·3", "P(A∩B)", "n−1", "a×b×c", "10×5", "a÷b", "µm"])
def test_math_expressions_are_not_legacy_encoded(expression: str) -> None:
    assert not is_legacy_encoded_word(expression)


@pytest.mark.parametrize("word", ["Tr−êng", "§¹i", "häc", "Gi¶i", "tr×nh", "hµnh", "vµ", "ch−a"])
def test_tcvn3_words_with_math_like_markers_are_still_invalid(word: str) -> None:
    assert is_legacy_encoded_word(word)


def test_statistics_slide_keeps_the_text_layer_and_tcvn3_does_not() -> None:
    assert valid_word_ratio(STATISTICS_SLIDE) == 1.0
    assert text_score(STATISTICS_SLIDE, CONFIG) >= CONFIG.text_layer_min
    tcvn3 = "Tr−êng §¹i häc B¸ch khoa Hµ Néi. Gi¶i bµi to¸n theo tr×nh tù c¸c b−íc ®· häc ë ch−¬ng tr−íc."
    assert valid_word_ratio(tcvn3) < 0.5
