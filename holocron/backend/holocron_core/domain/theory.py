"""Model câu lý thuyết MCQ4 và các luật so khớp mà validator của nó dùng (ADR-007; SPECIFICATION §5.11).

Đặt ở `domain/` để `domain/twins.py` (`TheoryTwin`) và repository ở `infrastructure/` dùng được mà không import
`services/`. Bộ sinh (`services/theory_generator.py`) import lại và re-export mọi tên ở đây; logic sinh câu vẫn nằm ở
service.
"""

from __future__ import annotations

import unicodedata
from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from .schemas import SEED_SPACE, Citation, MisconceptionRef, OptionKey, StrictModel, normalize_quote
from .vault import CHAPTER_CODE_PATTERN, SUBJECT_CODE_PATTERN

__all__ = [
    "THEORY_MISCONCEPTIONS",
    "THEORY_SAME_CHAPTER_CONCEPT",
    "THEORY_SAME_SUBJECT_CONCEPT",
    "TheoryQuestion",
    "TheoryQuestionForm",
]

THEORY_SAME_CHAPTER_CONCEPT = "THEORY_SAME_CHAPTER_CONCEPT"
THEORY_SAME_SUBJECT_CONCEPT = "THEORY_SAME_SUBJECT_CONCEPT"
THEORY_MISCONCEPTIONS: tuple[str, ...] = (THEORY_SAME_CHAPTER_CONCEPT, THEORY_SAME_SUBJECT_CONCEPT)
"""Danh sách mã misconception cố định của câu lý thuyết (ADR-007 mục 2, I2)."""


class TheoryQuestionForm(str, Enum):
    """Ba dạng câu MCQ4 của ADR-007 mục 1."""

    CONCEPT_MATCH = "concept_match"
    FILL_BLANK = "fill_blank"
    DEFINITION_CHOICE = "definition_choice"


def _option_key(text: str) -> str:
    """Khoá so trùng phương án và khái niệm: NFC, gộp khoảng trắng, không phân biệt hoa thường."""
    return normalize_quote(text).casefold()


def _overlaps(first: str, second: str) -> bool:
    """Hai thuật ngữ trùng nhau, hoặc thuật ngữ này chứa thuật ngữ kia, sau cùng phép chuẩn hoá của `_option_key`."""
    first_key, second_key = _option_key(first), _option_key(second)
    return first_key in second_key or second_key in first_key


def _is_edge_char(char: str) -> bool:
    """Ký tự bị bỏ ở hai đầu đoạn văn khi so xung đột: khoảng trắng hoặc dấu câu (nhóm Unicode `P*`)."""
    return char.isspace() or unicodedata.category(char).startswith("P")


def _trim_edges(key: str) -> str:
    """Bỏ khoảng trắng và dấu câu ở hai đầu `key`; phần giữa giữ nguyên."""
    start, end = 0, len(key)
    while start < end and _is_edge_char(key[start]):
        start += 1
    while end > start and _is_edge_char(key[end - 1]):
        end -= 1
    return key[start:end]


def _match_key(text: str) -> str:
    """Khoá so xung đột của một đoạn văn (trích dẫn, thân câu đã điền): `_option_key` (NFC, gộp khoảng trắng, không
    phân biệt hoa thường) rồi bỏ khoảng trắng và dấu câu ở hai đầu, để dấu chấm cuối câu hay ngoặc bao ngoài không làm
    trượt phép so chuỗi con. Dấu câu ở giữa giữ nguyên."""
    return _trim_edges(_option_key(text))


def _quotes_overlap(first: str, second: str) -> bool:
    """Hai trích dẫn trùng nhau, hoặc trích dẫn này chứa trích dẫn kia, so theo `_match_key`."""
    first_key, second_key = _match_key(first), _match_key(second)
    return first_key in second_key or second_key in first_key


class TheoryQuestion(StrictModel):
    """Một câu lý thuyết MCQ4 do code ghép từ cặp khái niệm đã xác nhận (ADR-007).

    Không có id hay thời điểm: cùng `(generator_version, pool_sha256, seed)` (cộng chương của slot, nếu slot giới hạn
    chương) thì cho model giống từng byte khi serialize (I4). `form` là kết quả, không phải đầu vào: chính RNG của bộ ba
    đó chọn dạng câu trong các dạng khả thi.
    """

    question_kind: Literal["theory"] = "theory"
    form: TheoryQuestionForm
    subject_code: str = Field(pattern=SUBJECT_CODE_PATTERN)
    chapter_code: str = Field(pattern=CHAPTER_CODE_PATTERN)
    seed: int = Field(ge=0, lt=SEED_SPACE)
    generator_version: str = Field(min_length=1, max_length=32)
    pool_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stem: str = Field(min_length=20)
    options: dict[OptionKey, str]
    correct_key: OptionKey
    distractor_to_misconception_map: dict[OptionKey, MisconceptionRef]
    distractor_citations: dict[OptionKey, Citation]
    solution_explanation_vi: str = Field(min_length=10)
    citation: Citation

    @model_validator(mode="after")
    def _mcq4_rules(self) -> TheoryQuestion:
        if set(self.options) != set(OptionKey):
            raise ValueError("MCQ4 yêu cầu đủ 4 options A-D")
        if len({_option_key(text) for text in self.options.values()}) != len(OptionKey):
            raise ValueError("4 options phải khác nhau sau khi chuẩn hoá")
        wrong = set(OptionKey) - {self.correct_key}
        if set(self.distractor_to_misconception_map) != wrong:
            raise ValueError("distractor_to_misconception_map phải phủ đúng 3 phương án sai")
        if set(self.distractor_citations) != wrong:
            raise ValueError("distractor_citations phải phủ đúng 3 phương án sai")
        for ref in self.distractor_to_misconception_map.values():
            if ref.misconception_code not in THEORY_MISCONCEPTIONS:
                raise ValueError(f"mã misconception ngoài THEORY_MISCONCEPTIONS: {ref.misconception_code}")
            if (
                self.form is TheoryQuestionForm.DEFINITION_CHOICE
                and ref.misconception_code != THEORY_SAME_CHAPTER_CONCEPT
            ):
                raise ValueError("dạng chọn định nghĩa đúng chỉ dùng phương án nhiễu cùng chương")
        for citation in self.distractor_citations.values():
            if _quotes_overlap(citation.verbatim_quote, self.citation.verbatim_quote):
                raise ValueError("trích dẫn của phương án nhiễu trùng hoặc lồng với trích dẫn đáp án: hai đáp án đúng")
        if self.form is TheoryQuestionForm.FILL_BLANK:
            correct = self.options[self.correct_key]
            if any(_overlaps(self.options[key], correct) for key in wrong):
                raise ValueError("thuật ngữ nhiễu trùng hoặc lồng với thuật ngữ bị che: hai đáp án đúng")
        return self
