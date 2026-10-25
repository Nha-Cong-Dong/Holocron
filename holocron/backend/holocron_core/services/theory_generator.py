"""Bộ sinh câu lý thuyết MCQ4 tất định từ cặp khái niệm đã xác nhận (ADR-007; SPECIFICATION §0 I2–I4, §5.8, §5.11).

Thuộc Deterministic Core: hàm thuần, không gọi LLM, không gọi mạng, không rẽ nhánh theo môn hay chương.

- **Nhóm ứng viên:** chỉ cặp `confirmed` cùng `subject_code` với slot (ADR-007 mục 1). Cặp chưa xác nhận, bị từ chối
  hay thuộc môn khác không bao giờ được dùng và không tính vào `pool_sha256`.
- **Ba dạng câu (ADR-007 mục 1):** ghép khái niệm, điền khuyết, chọn định nghĩa đúng. Thân câu là khung cố định do
  code viết cộng trích dẫn nguyên văn; mọi phương án là `term` hoặc `definition_quote` của một cặp (tên khái niệm bên
  trong định nghĩa được che bằng `____`). Không có văn bản nào do LLM soạn.
- **Phương án nhiễu (I2):** ưu tiên cặp cùng chương rồi mới tới cùng môn; mỗi phương án nhiễu mang đúng một mã thuộc
  `THEORY_MISCONCEPTIONS` theo nguồn chọn. Dạng chọn định nghĩa đúng chỉ lấy khái niệm cùng chương.
- **Trích dẫn (I3):** `citation` của câu và trích dẫn riêng của từng phương án nhiễu có `verbatim_quote` là
  `definition_quote` của cặp tương ứng; trích dẫn được kiểm lại là chuỗi con (NFC + gộp khoảng trắng) của
  `raw_text` của chunk nguồn trước khi dùng.
- **Tái lập (I4):** mọi lựa chọn ngẫu nhiên chỉ dùng `random.Random(f"{generator_version}:{pool_sha256}:{seed}")`;
  ứng viên luôn được sắp theo thứ tự chuẩn trước khi xáo, nên thứ tự cặp ở đầu vào không ảnh hưởng kết quả.
- **Đúng một đáp án:** cặp có `definition_quote` trùng, chứa hoặc nằm trong trích dẫn của cặp đáp án (so sau NFC, gộp
  khoảng trắng, không phân biệt hoa thường) không bao giờ làm phương án nhiễu, vì nó cũng là một đáp án đúng. Dạng điền
  khuyết còn loại thuật ngữ trùng, chứa hoặc nằm trong thuật ngữ bị che, và thuật ngữ mà khi điền vào chỗ trống cho ra
  một đoạn nằm trong `definition_quote` của một cặp thuộc nhóm ứng viên. Luật này chỉ đọc dữ liệu đã nằm trong
  `pool_sha256` (I4): `raw_text` của chunk không được dùng, nên thêm hay sửa chunk cùng môn mà không đổi cặp nào thì
  câu không đổi. Câu điền khuyết vẫn có thể có đáp án đúng thứ hai nằm trong một chunk chưa có cặp xác nhận; người dùng
  giảm rủi ro này bằng cách xác nhận thêm cặp cho đoạn văn đó.
- **Không đủ căn cứ:** không chọn được 3 phương án nhiễu khác nhau thì trả `ReviewTicket`, không bịa phương án.
- **Giới hạn đã biết:** che tên khái niệm chỉ khớp đúng chuỗi `term` (không phân biệt hoa thường, đúng ranh giới chữ);
  dạng biến hình hay số nhiều ("Process" và "Processes") không bị che và có thể lộ đáp án. Bộ sinh cố ý không tách từ
  hay biến hình theo ngôn ngữ.

`TheoryQuestion` là model nội bộ của service, chưa xuất hiện trong response nào. Phần contract để đưa câu lý thuyết vào
`TwinQuestion` (`TheoryTrace`, `template_id`/`topic_key` nullable, `MisconceptionRef.citation`, spec §5.11) đã duyệt
2026-10-25; việc nối vào `TwinQuestion` làm ở một thay đổi sau.
"""

from __future__ import annotations

import hashlib
import json
import random
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from ..domain.repositories import VaultRepository
from ..domain.schemas import Citation, KnowledgeChunk, MisconceptionRef, OptionKey, StrictModel, normalize_quote
from ..domain.twins import SEED_SPACE, ReviewTicket
from ..domain.vault import (
    CHAPTER_CODE_PATTERN,
    SUBJECT_CODE_PATTERN,
    ConceptPair,
    ConceptPairStatus,
    check_definition_quote,
    find_term_spans,
)

__all__ = [
    "BLANK",
    "GENERATOR_VERSION",
    "NOT_ENOUGH_EVIDENCE",
    "THEORY_MISCONCEPTIONS",
    "THEORY_SAME_CHAPTER_CONCEPT",
    "THEORY_SAME_SUBJECT_CONCEPT",
    "TheoryQuestion",
    "TheoryQuestionForm",
    "candidate_pool",
    "generate_theory_question",
    "generate_theory_question_from_vault",
    "mask_terms",
    "pool_sha256",
    "theory_rng",
]

GENERATOR_VERSION = "theory_mcq4_v1"
"""Phiên bản luật chọn của bộ sinh; tăng khi đổi luật chọn cặp, phương án hay thứ tự (ADR-007 mục 2)."""

THEORY_SAME_CHAPTER_CONCEPT = "THEORY_SAME_CHAPTER_CONCEPT"
THEORY_SAME_SUBJECT_CONCEPT = "THEORY_SAME_SUBJECT_CONCEPT"
THEORY_MISCONCEPTIONS: tuple[str, ...] = (THEORY_SAME_CHAPTER_CONCEPT, THEORY_SAME_SUBJECT_CONCEPT)
"""Danh sách mã misconception cố định của câu lý thuyết (ADR-007 mục 2, I2)."""

BLANK = "____"
NOT_ENOUGH_EVIDENCE = "không đủ căn cứ trong Vault"
DISTRACTOR_COUNT = 3


def _max_length(model: type[StrictModel], field: str) -> int:
    """Giới hạn `max_length` khai báo trên một trường của model, để không chép cứng con số của `domain`."""
    for constraint in model.model_fields[field].metadata:
        limit = getattr(constraint, "max_length", None)
        if isinstance(limit, int):
            return limit
    raise LookupError(f"{model.__name__}.{field} không khai báo max_length")


MAX_RATIONALE_CHARS = _max_length(MisconceptionRef, "rationale_vi")
"""Độ dài tối đa của `MisconceptionRef.rationale_vi`, đọc từ ràng buộc của model."""

_SAME_CHAPTER_LABEL = "Khái niệm khác cùng chương"
_SAME_SUBJECT_LABEL = "Khái niệm khác cùng môn"


class TheoryQuestionForm(str, Enum):
    """Ba dạng câu MCQ4 của ADR-007 mục 1."""

    CONCEPT_MATCH = "concept_match"
    FILL_BLANK = "fill_blank"
    DEFINITION_CHOICE = "definition_choice"


def _option_key(text: str) -> str:
    """Khoá so trùng phương án và khái niệm: NFC, gộp khoảng trắng, không phân biệt hoa thường."""
    return normalize_quote(text).casefold()


def _overlaps(first: str, second: str) -> bool:
    """Hai đoạn văn trùng nhau, hoặc đoạn này chứa đoạn kia, sau cùng phép chuẩn hoá của `_option_key`."""
    first_key, second_key = _option_key(first), _option_key(second)
    return first_key in second_key or second_key in first_key


class TheoryQuestion(StrictModel):
    """Một câu lý thuyết MCQ4 do code ghép từ cặp khái niệm đã xác nhận (ADR-007).

    Không có id hay thời điểm: cùng `(generator_version, pool_sha256, seed)` và cùng dạng câu, chương thì cho model
    giống từng byte khi serialize (I4).
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
            if _overlaps(citation.verbatim_quote, self.citation.verbatim_quote):
                raise ValueError("trích dẫn của phương án nhiễu trùng hoặc lồng với trích dẫn đáp án: hai đáp án đúng")
        if self.form is TheoryQuestionForm.FILL_BLANK:
            correct = self.options[self.correct_key]
            if any(_overlaps(self.options[key], correct) for key in wrong):
                raise ValueError("thuật ngữ nhiễu trùng hoặc lồng với thuật ngữ bị che: hai đáp án đúng")
        return self


# --------------------------------------------------------------------------- #
# Nhóm ứng viên, pool_sha256 và RNG (ADR-007 mục 2)
# --------------------------------------------------------------------------- #


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _pool_record(pair: ConceptPair) -> dict[str, str]:
    """Đúng năm trường của ADR-007 mục 2, mỗi trường chuẩn hoá NFC."""
    return {
        "subject_code": _nfc(pair.subject_code),
        "chapter_code": _nfc(pair.chapter_code),
        "term": _nfc(pair.term),
        "definition_quote": _nfc(pair.definition_quote),
        "chunk_id": _nfc(pair.chunk_id),
    }


def _pool_order(pair: ConceptPair) -> tuple[str, str, str, str]:
    """Sắp theo `(chapter_code, term, chunk_id, definition_quote)` sau NFC như ADR-007 mục 2; khoá cuối chỉ phân xử
    khi hoà, để thứ tự (và do đó `pool_sha256`) không phụ thuộc thứ tự đầu vào.

    Khoá phân xử `definition_quote` thuộc cách tính của ADR-007 mục 2 (duyệt 2026-10-25).
    """
    record = _pool_record(pair)
    return (record["chapter_code"], record["term"], record["chunk_id"], record["definition_quote"])


def candidate_pool(pairs: Sequence[ConceptPair], subject_code: str) -> list[ConceptPair]:
    """Cặp đã xác nhận cùng `subject_code` với slot, theo thứ tự chuẩn của `pool_sha256`."""
    selected = [
        pair for pair in pairs if pair.status is ConceptPairStatus.CONFIRMED and pair.subject_code == subject_code
    ]
    return sorted(selected, key=_pool_order)


def pool_sha256(pairs: Sequence[ConceptPair], subject_code: str) -> str:
    """sha256 hex của nhóm ứng viên theo đúng cách tính ở ADR-007 mục 2."""
    records = [_pool_record(pair) for pair in candidate_pool(pairs, subject_code)]
    payload = json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def theory_rng(pool_digest: str, seed: int) -> random.Random:
    """Nguồn ngẫu nhiên duy nhất của bộ sinh (I4)."""
    return random.Random(f"{GENERATOR_VERSION}:{pool_digest}:{seed}")


# --------------------------------------------------------------------------- #
# Che tên khái niệm, trích dẫn
# --------------------------------------------------------------------------- #


def mask_terms(text: str, terms: Sequence[str]) -> str:
    """Thay mọi lần xuất hiện của từng `term` trong `text` (không phân biệt hoa thường, đúng ranh giới chữ, như
    `find_term_spans`) bằng `BLANK`; các vị trí chồng nhau được gộp. Phần còn lại giữ nguyên văn.

    Giới hạn: chỉ khớp đúng chuỗi `term`; dạng biến hình hay số nhiều ("Process" trong "Processes") không bị che. Cố ý
    không tách từ hay biến hình theo ngôn ngữ để giữ hàm thuần và không rẽ nhánh theo ngôn ngữ của tài liệu.
    """
    return _replace_terms(text, terms, BLANK)


def _replace_terms(text: str, terms: Sequence[str], replacement: str) -> str:
    """Như `mask_terms` nhưng thay bằng `replacement` tuỳ ý (dùng để thử điền một thuật ngữ vào chỗ trống)."""
    spans = sorted(span for term in terms for span in find_term_spans(term, text))
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    pieces: list[str] = []
    cursor = 0
    for start, end in merged:
        pieces.append(text[cursor:start])
        pieces.append(replacement)
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _citation(pair: ConceptPair, chunks: Mapping[str, KnowledgeChunk]) -> Citation:
    """Trích dẫn của cặp: `definition_quote` nguyên văn, kiểm lại I3 với chunk nguồn trước khi dùng.

    `page_number` là trang đầu của chunk (chunk không lưu ranh giới trang bên trong `raw_text`).
    """
    chunk = chunks.get(pair.chunk_id)
    if chunk is None:
        raise ValueError(f"thiếu chunk {pair.chunk_id} của cặp khái niệm {pair.pair_id}")
    if chunk.subject_code != pair.subject_code:
        raise ValueError(f"cặp khái niệm {pair.pair_id} và chunk {chunk.chunk_id} khác môn")
    check_definition_quote(chunk.chunk_id, chunk.raw_text, pair.definition_quote)
    return Citation(
        doc_id=chunk.doc_id,
        page_number=chunk.page_number,
        verbatim_quote=pair.definition_quote,
        chunk_id=chunk.chunk_id,
    )


def _rationale(pair: ConceptPair) -> str:
    """Giải thích phương án nhiễu: định nghĩa nguyên văn của khái niệm ứng với phương án (ADR-007 mục 1), kèm tên
    khái niệm khi còn chỗ."""
    labelled = f"«{pair.term}»: {pair.definition_quote}"
    return labelled if len(labelled) <= MAX_RATIONALE_CHARS else pair.definition_quote


# --------------------------------------------------------------------------- #
# Chọn phương án nhiễu và ghép câu
# --------------------------------------------------------------------------- #

_Distractor = tuple[ConceptPair, str, str]  # (cặp, mã misconception, văn bản phương án)


def _pick_distractors(
    rng: random.Random,
    pool: Sequence[ConceptPair],
    *,
    chapter_code: str,
    excluded_concepts: set[str],
    taken_options: set[str],
    option_text: Callable[[ConceptPair], str],
    also_correct: Callable[[ConceptPair, str], bool],
    allow_other_chapters: bool,
) -> list[_Distractor] | None:
    """3 phương án nhiễu khác khái niệm và khác văn bản (sau chuẩn hoá): cùng chương trước, rồi cùng môn.

    `also_correct(cặp, văn bản phương án)` đúng khi phương án đó cũng là một đáp án đúng theo nhóm ứng viên; cặp như
    vậy bị bỏ qua. Luôn xáo cả hai nhóm để lượng số ngẫu nhiên tiêu thụ không phụ thuộc kết quả chọn. Không đủ 3 ⇒
    `None`.
    """
    same_chapter = [pair for pair in pool if pair.chapter_code == chapter_code]
    other_chapters = [pair for pair in pool if pair.chapter_code != chapter_code] if allow_other_chapters else []
    rng.shuffle(same_chapter)
    rng.shuffle(other_chapters)
    concepts = set(excluded_concepts)
    options = set(taken_options)
    chosen: list[_Distractor] = []
    for code, group in ((THEORY_SAME_CHAPTER_CONCEPT, same_chapter), (THEORY_SAME_SUBJECT_CONCEPT, other_chapters)):
        for pair in group:
            if len(chosen) == DISTRACTOR_COUNT:
                break
            text = option_text(pair)
            concept, option = _option_key(pair.term), _option_key(text)
            if concept in concepts or option in options or also_correct(pair, text):
                continue
            concepts.add(concept)
            options.add(option)
            chosen.append((pair, code, text))
    return chosen if len(chosen) == DISTRACTOR_COUNT else None


def _assemble(
    rng: random.Random,
    *,
    form: TheoryQuestionForm,
    subject_code: str,
    seed: int,
    pool_digest: str,
    anchor: ConceptPair,
    stem: str,
    correct_text: str,
    explanation: str,
    distractors: list[_Distractor],
    chunks: Mapping[str, KnowledgeChunk],
) -> TheoryQuestion:
    entries: list[tuple[str, _Distractor | None]] = [(correct_text, None)]
    entries.extend((text, (pair, code, text)) for pair, code, text in distractors)
    rng.shuffle(entries)
    options: dict[OptionKey, str] = {}
    misconceptions: dict[OptionKey, MisconceptionRef] = {}
    citations: dict[OptionKey, Citation] = {}
    correct_key = OptionKey.A
    for key, (text, distractor) in zip(OptionKey, entries, strict=True):
        options[key] = text
        if distractor is None:
            correct_key = key
            continue
        pair, code, _ = distractor
        label = _SAME_CHAPTER_LABEL if code == THEORY_SAME_CHAPTER_CONCEPT else _SAME_SUBJECT_LABEL
        misconceptions[key] = MisconceptionRef(misconception_code=code, label_vi=label, rationale_vi=_rationale(pair))
        citations[key] = _citation(pair, chunks)
    return TheoryQuestion(
        form=form,
        subject_code=subject_code,
        chapter_code=anchor.chapter_code,
        seed=seed,
        generator_version=GENERATOR_VERSION,
        pool_sha256=pool_digest,
        stem=stem,
        options=options,
        correct_key=correct_key,
        distractor_to_misconception_map=misconceptions,
        distractor_citations=citations,
        solution_explanation_vi=explanation,
        citation=_citation(anchor, chunks),
    )


def _definition_explanation(pair: ConceptPair) -> str:
    return f"Định nghĩa của «{pair.term}» trong tài liệu: “{pair.definition_quote}”"


def _quote_conflict(anchor: ConceptPair) -> Callable[[ConceptPair, str], bool]:
    """Cặp có trích dẫn trùng, chứa hoặc nằm trong trích dẫn của cặp đáp án cũng là đáp án đúng: không làm nhiễu."""

    def conflict(pair: ConceptPair, _text: str) -> bool:
        return _overlaps(pair.definition_quote, anchor.definition_quote)

    return conflict


def _fill_blank_conflict(
    anchor: ConceptPair, hidden: str, pool_texts: Sequence[str]
) -> Callable[[ConceptPair, str], bool]:
    """Quy tắc tương đương cho phương án là thuật ngữ (dạng điền khuyết).

    Loại cặp có trích dẫn lồng với trích dẫn đáp án, thuật ngữ trùng/chứa/nằm trong thuật ngữ bị che, và thuật ngữ mà
    khi điền vào chỗ trống cho ra một đoạn có trong `pool_texts` (trích dẫn của nhóm ứng viên, đã qua `_option_key`).
    """
    quote_conflict = _quote_conflict(anchor)

    def conflict(pair: ConceptPair, text: str) -> bool:
        if quote_conflict(pair, text) or _overlaps(text, hidden):
            return True
        filled = _option_key(_replace_terms(anchor.definition_quote, [hidden], text))
        return any(filled in pool_text for pool_text in pool_texts)

    return conflict


def _pool_texts(pool: Sequence[ConceptPair]) -> list[str]:
    """Văn bản dùng để nhận ra đáp án đúng thứ hai của dạng điền khuyết: chỉ `definition_quote` của các cặp trong nhóm
    ứng viên, tức đúng dữ liệu đã nằm trong `pool_sha256`. `raw_text` của chunk không được dùng: chunk không thuộc
    `pool_sha256`, nên nếu dùng thì thêm một chunk cùng môn sẽ làm câu đổi dù `(generator_version, pool_sha256, seed)`
    giữ nguyên (I4)."""
    return [_option_key(pair.definition_quote) for pair in pool]


def _fill_blank_candidates(
    anchors: Sequence[ConceptPair], pool: Sequence[ConceptPair]
) -> list[tuple[ConceptPair, str]]:
    """`(cặp chứa trích dẫn, term bị che)`: term là `term` của một khái niệm khác đã xác nhận và xuất hiện trong
    trích dẫn; mỗi khái niệm chỉ một lần cho mỗi trích dẫn, theo thứ tự chuẩn."""
    found: list[tuple[ConceptPair, str]] = []
    for anchor in anchors:
        seen = {_option_key(anchor.term)}
        for other in pool:
            concept = _option_key(other.term)
            if concept in seen or not find_term_spans(other.term, anchor.definition_quote):
                continue
            seen.add(concept)
            found.append((anchor, other.term))
    return found


# --------------------------------------------------------------------------- #
# API của service
# --------------------------------------------------------------------------- #


def generate_theory_question(
    pairs: Sequence[ConceptPair],
    chunks: Mapping[str, KnowledgeChunk],
    *,
    subject_code: str,
    form: TheoryQuestionForm,
    seed: int,
    blueprint_id: str,
    slot_index: int,
    topic_key: str,
    chapter_code: str | None = None,
) -> TheoryQuestion | ReviewTicket:
    """Sinh một câu lý thuyết MCQ4, hoặc `ReviewTicket` khi Vault không đủ căn cứ.

    `pairs` có thể gồm cặp của môn khác hay chưa xác nhận; chúng bị bỏ qua. `chunks` ánh xạ `chunk_id` → chunk nguồn
    của các cặp. `chapter_code` (tuỳ chọn) giới hạn cặp đúng vào một chương; phương án nhiễu vẫn có thể lấy cùng môn.
    `blueprint_id`, `slot_index`, `topic_key` chỉ dùng để ghi `ReviewTicket`.

    Cặp được chọn mà thiếu chunk, khác môn với chunk hoặc trích dẫn không còn khớp nguyên văn chunk (I3) ⇒ lỗi
    (`ValueError`/`QuoteMismatchError`): đó là dữ liệu hỏng, không phải thiếu căn cứ.
    """
    if not 0 <= seed < SEED_SPACE:
        raise ValueError(f"seed phải trong [0, 2**63): {seed}")
    pool = candidate_pool(pairs, subject_code)
    digest = pool_sha256(pairs, subject_code)
    rng = theory_rng(digest, seed)

    def ticket(detail: str) -> ReviewTicket:
        return ReviewTicket(
            blueprint_id=blueprint_id,
            slot_index=slot_index,
            topic_key=topic_key,
            reason=f"{NOT_ENOUGH_EVIDENCE}: {detail}",
            attempts=[],
        )

    anchors = [pair for pair in pool if chapter_code is None or pair.chapter_code == chapter_code]
    if not anchors:
        scope = f"chương {chapter_code} của môn {subject_code}" if chapter_code else f"môn {subject_code}"
        return ticket(f"không có cặp khái niệm đã xác nhận nào thuộc {scope}")

    if form is TheoryQuestionForm.FILL_BLANK:
        candidates = _fill_blank_candidates(anchors, pool)
        if not candidates:
            return ticket("không có trích dẫn định nghĩa nào chứa thuật ngữ của một khái niệm khác đã xác nhận")
        rng.shuffle(candidates)
        pool_texts = _pool_texts(pool)
        for anchor, hidden in candidates:
            distractors = _pick_distractors(
                rng,
                pool,
                chapter_code=anchor.chapter_code,
                excluded_concepts={_option_key(anchor.term), _option_key(hidden)},
                taken_options={_option_key(hidden)},
                option_text=_term_of,
                also_correct=_fill_blank_conflict(anchor, hidden, pool_texts),
                allow_other_chapters=True,
            )
            if distractors is not None:
                masked = mask_terms(anchor.definition_quote, [hidden])
                return _assemble(
                    rng,
                    form=form,
                    subject_code=subject_code,
                    seed=seed,
                    pool_digest=digest,
                    anchor=anchor,
                    stem=f"Điền thuật ngữ thích hợp vào chỗ trống trong đoạn trích sau:\n\n> {masked}",
                    correct_text=hidden,
                    explanation=f"Thuật ngữ bị che là «{hidden}». Đoạn trích nguyên văn: “{anchor.definition_quote}”",
                    distractors=distractors,
                    chunks=chunks,
                )
        return ticket("không chọn được 3 thuật ngữ khác nhau làm phương án nhiễu")

    order = list(anchors)
    rng.shuffle(order)
    for anchor in order:
        if form is TheoryQuestionForm.CONCEPT_MATCH:
            correct_text = anchor.term
            stem = f"Khái niệm nào ứng với đoạn trích sau?\n\n> {mask_terms(anchor.definition_quote, [anchor.term])}"
            option_text: Callable[[ConceptPair], str] = _term_of
        else:
            correct_text = mask_terms(anchor.definition_quote, [anchor.term])
            stem = f"Phát biểu nào là định nghĩa của «{anchor.term}»?"
            option_text = _masked_definition(anchor.term)
        distractors = _pick_distractors(
            rng,
            pool,
            chapter_code=anchor.chapter_code,
            excluded_concepts={_option_key(anchor.term)},
            taken_options={_option_key(correct_text)},
            option_text=option_text,
            also_correct=_quote_conflict(anchor),
            allow_other_chapters=form is TheoryQuestionForm.CONCEPT_MATCH,
        )
        if distractors is not None:
            return _assemble(
                rng,
                form=form,
                subject_code=subject_code,
                seed=seed,
                pool_digest=digest,
                anchor=anchor,
                stem=stem,
                correct_text=correct_text,
                explanation=_definition_explanation(anchor),
                distractors=distractors,
                chunks=chunks,
            )
    if form is TheoryQuestionForm.DEFINITION_CHOICE:
        return ticket("không đủ 3 khái niệm khác cùng chương với định nghĩa khác nhau để làm phương án nhiễu")
    return ticket("không chọn được 3 khái niệm khác nhau cùng môn làm phương án nhiễu")


def _term_of(pair: ConceptPair) -> str:
    return pair.term


def _masked_definition(asked_term: str) -> Callable[[ConceptPair], str]:
    """Phương án dạng định nghĩa: che tên khái niệm của chính nó và tên khái niệm đang hỏi (ADR-007 mục 1)."""

    def text(pair: ConceptPair) -> str:
        return mask_terms(pair.definition_quote, [pair.term, asked_term])

    return text


def generate_theory_question_from_vault(
    vault: VaultRepository,
    *,
    subject_code: str,
    form: TheoryQuestionForm,
    seed: int,
    blueprint_id: str,
    slot_index: int,
    topic_key: str,
    chapter_code: str | None = None,
) -> TheoryQuestion | ReviewTicket:
    """Như `generate_theory_question`, với cặp đã xác nhận của môn `subject_code` đọc từ Vault.

    Chỉ đọc chunk nguồn của các cặp đó, và chỉ để dựng và kiểm lại trích dẫn (I3); chunk khác của môn không ảnh hưởng
    tới câu sinh ra (I4).
    """
    pairs = vault.list_concept_pairs(subject_code, status=ConceptPairStatus.CONFIRMED)
    sources = {pair.chunk_id for pair in pairs}
    chunks = {chunk.chunk_id: chunk for chunk in vault.list_chunks(subject_code) if chunk.chunk_id in sources}
    return generate_theory_question(
        pairs,
        chunks,
        subject_code=subject_code,
        form=form,
        seed=seed,
        blueprint_id=blueprint_id,
        slot_index=slot_index,
        topic_key=topic_key,
        chapter_code=chapter_code,
    )
