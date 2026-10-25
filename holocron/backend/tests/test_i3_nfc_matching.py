"""I3 so khớp sau NFC (ADR-006 mục 9): chỉ tương đương chính tắc mới khớp, không nới lỏng gì thêm."""

import unicodedata

import pytest
from pydantic import ValidationError

from holocron_core.domain.schemas import KnowledgeChunk, normalize_quote, quote_in_text

TEXT = "Tiến trình bị ngắt và đưa về cuối hàng đợi sẵn sàng."


def _chunk(raw_text: str, quote: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        doc_id="doc_x",
        subject_code="IT3070",
        chapter_code="ch5",
        page_number=1,
        raw_text=raw_text,
        verbatim_quote=quote,
        category="THEORY",
        token_count=10,
    )


def test_nfc_quote_matches_nfd_text() -> None:
    nfd_text = unicodedata.normalize("NFD", TEXT)
    assert _chunk(nfd_text, "đưa về cuối hàng đợi").raw_text == nfd_text


def test_whitespace_is_collapsed_after_nfc() -> None:
    assert quote_in_text("đưa  về\ncuối", TEXT)
    assert normalize_quote(" a\t\n b ") == "a b"


@pytest.mark.parametrize(
    "quote",
    [
        "TIẾN TRÌNH BỊ NGẮT",  # không casefold
        "Tien trinh bi ngat",  # không bỏ dấu
        "đưa vê cuối hàng đợi",  # sai dấu thanh là chữ khác
    ],
)
def test_non_canonical_variants_are_still_rejected(quote: str) -> None:
    assert not quote_in_text(quote, TEXT)
    with pytest.raises(ValidationError):
        _chunk(TEXT, quote)


def test_compatibility_forms_are_not_equated() -> None:
    """NFKC sẽ coi chữ ghép `ﬁ` là `fi`; I3 chỉ dùng NFC nên hai chuỗi này khác nhau."""
    assert unicodedata.normalize("NFKC", "ﬁle system") == "file system"
    assert not quote_in_text("file system", "The ﬁle system is a tree.")
