"""Bộ sinh câu lý thuyết MCQ4 tất định (ADR-007; CLAUDE.md §9 dòng I2 và I4; I3).

- I2: mọi phương án nhiễu mang mã thuộc `THEORY_MISCONCEPTIONS`.
- I4: cùng `(generator_version, pool_sha256, seed)` thì cùng câu, giống từng byte; NFC và NFD cho cùng `pool_sha256`;
  thêm cặp ở môn khác (hay cặp chưa xác nhận) không đổi `pool_sha256`.
- I3: đáp án và mọi phương án là văn bản nguyên văn của cặp nguồn (ngoài chỗ che `____`), trích dẫn là chuỗi con của
  chunk nguồn.
- Thiếu căn cứ ⇒ `ReviewTicket`; property test 150 seed cho cả ba dạng câu.

Văn bản mẫu tự viết cho test (không phải tài liệu môn học có bản quyền).
"""

import random
import unicodedata
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from holocron_core.domain.schemas import (
    ChunkCategory,
    Citation,
    KnowledgeChunk,
    MisconceptionRef,
    OptionKey,
    normalize_quote,
    quote_in_text,
)
from holocron_core.domain.twins import ReviewTicket
from holocron_core.domain.vault import ConceptPair, ConceptPairStatus, QuoteMismatchError
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.services.theory_generator import (
    BLANK,
    GENERATOR_VERSION,
    NOT_ENOUGH_EVIDENCE,
    THEORY_MISCONCEPTIONS,
    THEORY_SAME_CHAPTER_CONCEPT,
    THEORY_SAME_SUBJECT_CONCEPT,
    TheoryQuestion,
    TheoryQuestionForm,
    candidate_pool,
    generate_theory_question,
    generate_theory_question_from_vault,
    mask_terms,
    pool_sha256,
    theory_rng,
)
from holocron_core.services.vault import create_concept_pair, ingest_confirmed_text
from tests.vault_support import add_document, confirmed, document_id

NOW = datetime(2026, 10, 25, 8, 0, tzinfo=UTC)
SUBJECT = "SSH1110"
OTHER_SUBJECT = "ET4010"
DOC = "doc_" + "a" * 16
OTHER_DOC = "doc_" + "b" * 16
FORMS = tuple(TheoryQuestionForm)
PROPERTY_SEEDS = range(150)
TICKET_CONTEXT = {"blueprint_id": "bp_theory_0001", "slot_index": 3, "topic_key": "phil.matter.definition"}

CH1_DEFINITIONS = [
    ("Vật chất", "Vật chất là phạm trù triết học dùng để chỉ thực tại khách quan."),
    ("Ý thức", "Ý thức là sự phản ánh năng động, sáng tạo thế giới khách quan vào bộ óc con người."),
    ("Vận động", "Vận động là phương thức tồn tại của vật chất."),
    ("Không gian", "Không gian là hình thức tồn tại của vật chất xét về quảng tính."),
    ("Thời gian", "Thời gian là hình thức tồn tại của vật chất xét về độ dài diễn biến."),
]
CH2_DEFINITIONS = [
    ("Thực tiễn", "Thực tiễn là toàn bộ hoạt động vật chất có mục đích của con người."),
    ("Nhận thức", "Nhận thức là quá trình phản ánh hiện thực khách quan vào bộ óc con người."),
    ("Chân lý", "Chân lý là tri thức phù hợp với hiện thực khách quan."),
]
OTHER_SUBJECT_DEFINITIONS = [
    ("Cờ Z", "Cờ Z được đặt khi kết quả phép toán bằng không."),
    ("Cờ C", "Cờ C được đặt khi phép cộng có nhớ ra khỏi bit cao nhất."),
    ("Cờ N", "Cờ N được đặt khi bit cao nhất của kết quả bằng một."),
    ("Cờ V", "Cờ V được đặt khi phép toán có dấu bị tràn."),
]


def _chunk(chunk_id: str, subject: str, chapter: str, definitions: list[tuple[str, str]], page: int) -> KnowledgeChunk:
    raw_text = "Mở đầu phần lý thuyết.\n" + "\n".join(quote for _, quote in definitions)
    return KnowledgeChunk(
        chunk_id=chunk_id,
        doc_id=DOC if subject == SUBJECT else OTHER_DOC,
        subject_code=subject,
        chapter_code=chapter,
        page_number=page,
        raw_text=raw_text,
        verbatim_quote=definitions[0][1],
        category=ChunkCategory.THEORY,
        token_count=60,
        label_status="confirmed",
    )


CHUNKS = {
    chunk.chunk_id: chunk
    for chunk in (
        _chunk("chk_phil_ch1", SUBJECT, "ch1", CH1_DEFINITIONS, 3),
        _chunk("chk_phil_ch2", SUBJECT, "ch2", CH2_DEFINITIONS, 9),
        _chunk("chk_arm_ch3", OTHER_SUBJECT, "ch3", OTHER_SUBJECT_DEFINITIONS, 5),
    )
}


def _pair(
    chunk_id: str,
    term: str,
    quote: str,
    *,
    index: int,
    status: ConceptPairStatus = ConceptPairStatus.CONFIRMED,
) -> ConceptPair:
    chunk = CHUNKS[chunk_id]
    return ConceptPair(
        pair_id=f"cp_{chunk_id}_{index:02d}",
        chunk_id=chunk_id,
        subject_code=chunk.subject_code,
        chapter_code=chunk.chapter_code,
        term=term,
        definition_quote=quote,
        status=status,
        proposed_by="user",
        created_at=NOW,
        confirmed_at=NOW if status is ConceptPairStatus.CONFIRMED else None,
    )


def _pairs(chunk_id: str, definitions: list[tuple[str, str]]) -> list[ConceptPair]:
    return [_pair(chunk_id, term, quote, index=i) for i, (term, quote) in enumerate(definitions)]


SUBJECT_PAIRS = _pairs("chk_phil_ch1", CH1_DEFINITIONS) + _pairs("chk_phil_ch2", CH2_DEFINITIONS)
OTHER_PAIRS = _pairs("chk_arm_ch3", OTHER_SUBJECT_DEFINITIONS)
ALL_TERMS = {term for term, _ in CH1_DEFINITIONS + CH2_DEFINITIONS}


def _generate(
    pairs: list[ConceptPair],
    form: TheoryQuestionForm,
    seed: int,
    *,
    chapter_code: str | None = None,
    subject_code: str = SUBJECT,
) -> TheoryQuestion | ReviewTicket:
    return generate_theory_question(
        pairs, CHUNKS, subject_code=subject_code, form=form, seed=seed, chapter_code=chapter_code, **TICKET_CONTEXT
    )


def _question(
    pairs: list[ConceptPair], form: TheoryQuestionForm, seed: int, *, chapter_code: str | None = None
) -> TheoryQuestion:
    result = _generate(pairs, form, seed, chapter_code=chapter_code)
    assert isinstance(result, TheoryQuestion), result
    return result


def _pieces(text: str) -> list[str]:
    """Các đoạn nguyên văn còn lại sau khi bỏ chỗ che `____`."""
    return [piece.strip() for piece in text.split(BLANK) if piece.strip()]


def _same_concept(first: str, second: str) -> bool:
    return normalize_quote(first).casefold() == normalize_quote(second).casefold()


# --------------------------------------------------------------------------- #
# I2: mã misconception
# --------------------------------------------------------------------------- #


def test_theory_misconceptions_are_the_two_codes_of_adr_007() -> None:
    assert THEORY_MISCONCEPTIONS == ("THEORY_SAME_CHAPTER_CONCEPT", "THEORY_SAME_SUBJECT_CONCEPT")


@pytest.mark.parametrize("form", FORMS)
def test_every_distractor_carries_a_code_from_theory_misconceptions(form: TheoryQuestionForm) -> None:
    for seed in PROPERTY_SEEDS:
        question = _question(SUBJECT_PAIRS, form, seed)
        for key, ref in question.distractor_to_misconception_map.items():
            assert key is not question.correct_key
            assert ref.misconception_code in THEORY_MISCONCEPTIONS


def test_model_rejects_a_code_outside_theory_misconceptions() -> None:
    question = _question(SUBJECT_PAIRS, TheoryQuestionForm.CONCEPT_MATCH, 7)
    data = question.model_dump()
    wrong = next(iter(question.distractor_to_misconception_map))
    data["distractor_to_misconception_map"][wrong]["misconception_code"] = "SJF_IGNORES_ARRIVAL"
    with pytest.raises(ValidationError, match="THEORY_MISCONCEPTIONS"):
        TheoryQuestion.model_validate(data)


def test_model_rejects_a_correct_key_with_a_misconception() -> None:
    question = _question(SUBJECT_PAIRS, TheoryQuestionForm.CONCEPT_MATCH, 7)
    data = question.model_dump()
    ref = MisconceptionRef(
        misconception_code=THEORY_SAME_CHAPTER_CONCEPT, label_vi="Nhầm", rationale_vi="định nghĩa khác"
    )
    data["distractor_to_misconception_map"][question.correct_key] = ref.model_dump()
    with pytest.raises(ValidationError, match="3 phương án sai"):
        TheoryQuestion.model_validate(data)


def test_model_rejects_missing_duplicate_or_uncited_options() -> None:
    question = _question(SUBJECT_PAIRS, TheoryQuestionForm.CONCEPT_MATCH, 7)
    wrong = next(iter(question.distractor_to_misconception_map))

    missing = question.model_dump()
    del missing["options"][OptionKey.D]
    with pytest.raises(ValidationError, match="đủ 4 options"):
        TheoryQuestion.model_validate(missing)

    duplicate = question.model_dump()
    duplicate["options"][wrong] = "  " + question.options[question.correct_key].upper() + " "
    with pytest.raises(ValidationError, match="khác nhau"):
        TheoryQuestion.model_validate(duplicate)

    uncited = question.model_dump()
    del uncited["distractor_citations"][wrong]
    with pytest.raises(ValidationError, match="distractor_citations"):
        TheoryQuestion.model_validate(uncited)


def test_definition_choice_only_uses_same_chapter_distractors() -> None:
    for seed in PROPERTY_SEEDS:
        question = _question(SUBJECT_PAIRS, TheoryQuestionForm.DEFINITION_CHOICE, seed)
        codes = {ref.misconception_code for ref in question.distractor_to_misconception_map.values()}
        assert codes == {THEORY_SAME_CHAPTER_CONCEPT}
        assert {citation.chunk_id for citation in question.distractor_citations.values()} == {
            question.citation.chunk_id
        }


def test_model_rejects_other_chapter_codes_in_definition_choice() -> None:
    question = _question(SUBJECT_PAIRS, TheoryQuestionForm.DEFINITION_CHOICE, 4)
    data = question.model_dump()
    wrong = next(iter(question.distractor_to_misconception_map))
    data["distractor_to_misconception_map"][wrong]["misconception_code"] = THEORY_SAME_SUBJECT_CONCEPT
    with pytest.raises(ValidationError, match="cùng chương"):
        TheoryQuestion.model_validate(data)


def test_concept_match_prefers_the_same_chapter_then_the_same_subject() -> None:
    """Chương ch2 chỉ có 3 khái niệm: 2 phương án nhiễu cùng chương, 1 cùng môn."""
    for seed in range(40):
        question = _question(SUBJECT_PAIRS, TheoryQuestionForm.CONCEPT_MATCH, seed, chapter_code="ch2")
        codes = sorted(ref.misconception_code for ref in question.distractor_to_misconception_map.values())
        assert codes == [THEORY_SAME_CHAPTER_CONCEPT, THEORY_SAME_CHAPTER_CONCEPT, THEORY_SAME_SUBJECT_CONCEPT]
        for key, ref in question.distractor_to_misconception_map.items():
            expected = "chk_phil_ch2" if ref.misconception_code == THEORY_SAME_CHAPTER_CONCEPT else "chk_phil_ch1"
            assert question.distractor_citations[key].chunk_id == expected


# --------------------------------------------------------------------------- #
# I4: tái lập và pool_sha256
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("form", FORMS)
def test_same_version_pool_and_seed_give_byte_identical_questions(form: TheoryQuestionForm) -> None:
    shuffled = list(SUBJECT_PAIRS)
    random.Random(2026).shuffle(shuffled)
    for seed in (0, 1, 42, 2**63 - 1):
        first = _question(SUBJECT_PAIRS, form, seed)
        second = _question(shuffled, form, seed)
        assert first.pool_sha256 == second.pool_sha256 == pool_sha256(SUBJECT_PAIRS, SUBJECT)
        assert first.generator_version == GENERATOR_VERSION
        assert first.model_dump_json() == second.model_dump_json()


def test_different_seeds_give_different_questions() -> None:
    dumps = {_question(SUBJECT_PAIRS, TheoryQuestionForm.CONCEPT_MATCH, seed).model_dump_json() for seed in range(30)}
    assert len(dumps) > 1


def test_rng_is_seeded_with_version_pool_and_seed() -> None:
    digest = pool_sha256(SUBJECT_PAIRS, SUBJECT)
    expected = random.Random(f"{GENERATOR_VERSION}:{digest}:17")
    assert theory_rng(digest, 17).random() == expected.random()


def test_pool_sha256_follows_adr_007() -> None:
    import hashlib
    import json

    records = sorted(
        (
            {
                "subject_code": pair.subject_code,
                "chapter_code": pair.chapter_code,
                "term": pair.term,
                "definition_quote": pair.definition_quote,
                "chunk_id": pair.chunk_id,
            }
            for pair in SUBJECT_PAIRS
        ),
        key=lambda record: (record["chapter_code"], record["term"], record["chunk_id"]),
    )
    payload = json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert pool_sha256(SUBJECT_PAIRS, SUBJECT) == hashlib.sha256(payload).hexdigest()


def _nfd(text: str) -> str:
    return unicodedata.normalize("NFD", text)


def test_nfc_and_nfd_pairs_give_the_same_pool_sha256_and_question() -> None:
    nfd_pairs = [
        _pair(pair.chunk_id, _nfd(pair.term), _nfd(pair.definition_quote), index=i)
        for i, pair in enumerate(SUBJECT_PAIRS)
    ]
    assert any(_nfd(pair.term) != pair.term for pair in SUBJECT_PAIRS)
    assert pool_sha256(nfd_pairs, SUBJECT) == pool_sha256(SUBJECT_PAIRS, SUBJECT)
    for form in FORMS:
        assert _question(nfd_pairs, form, 5).model_dump_json() == _question(SUBJECT_PAIRS, form, 5).model_dump_json()


def test_pool_sha256_normalizes_nfc_even_without_model_validation() -> None:
    """`pool_sha256` tự chuẩn hoá NFC từng trường, không dựa vào validator của `ConceptPair`."""
    raw_nfd = [
        pair.model_copy(update={"term": _nfd(pair.term), "definition_quote": _nfd(pair.definition_quote)})
        for pair in SUBJECT_PAIRS
    ]
    assert any(pair.term != original.term for pair, original in zip(raw_nfd, SUBJECT_PAIRS, strict=True))
    assert pool_sha256(raw_nfd, SUBJECT) == pool_sha256(SUBJECT_PAIRS, SUBJECT)


def test_pairs_of_another_subject_do_not_change_pool_sha256_or_question() -> None:
    mixed = OTHER_PAIRS[:2] + SUBJECT_PAIRS + OTHER_PAIRS[2:]
    assert pool_sha256(mixed, SUBJECT) == pool_sha256(SUBJECT_PAIRS, SUBJECT)
    assert pool_sha256(mixed, OTHER_SUBJECT) == pool_sha256(OTHER_PAIRS, OTHER_SUBJECT)
    for form in FORMS:
        for seed in (3, 11):
            assert (
                _question(mixed, form, seed).model_dump_json() == _question(SUBJECT_PAIRS, form, seed).model_dump_json()
            )


def test_unconfirmed_pairs_are_never_used_and_do_not_change_pool_sha256() -> None:
    extra = [
        _pair(
            "chk_phil_ch1", "Phạm trù", "Vật chất là phạm trù triết học", index=90, status=ConceptPairStatus.PROPOSED
        ),
        _pair(
            "chk_phil_ch1", "Phản ánh", "sự phản ánh năng động, sáng tạo", index=91, status=ConceptPairStatus.REJECTED
        ),
    ]
    with_extra = SUBJECT_PAIRS + extra
    assert candidate_pool(with_extra, SUBJECT) == candidate_pool(SUBJECT_PAIRS, SUBJECT)
    assert pool_sha256(with_extra, SUBJECT) == pool_sha256(SUBJECT_PAIRS, SUBJECT)
    for form in FORMS:
        for seed in PROPERTY_SEEDS:
            question = _question(with_extra, form, seed)
            assert "Phạm trù" not in question.options.values()
            assert "Phản ánh" not in question.options.values()


def test_editing_a_confirmed_pair_changes_pool_sha256() -> None:
    edited = [SUBJECT_PAIRS[0].model_copy(update={"term": "Thực tại khách quan"}), *SUBJECT_PAIRS[1:]]
    assert pool_sha256(edited, SUBJECT) != pool_sha256(SUBJECT_PAIRS, SUBJECT)


# --------------------------------------------------------------------------- #
# I3: nguyên văn
# --------------------------------------------------------------------------- #


def _assert_citation_verbatim(citation: Citation) -> None:
    assert citation.chunk_id is not None
    chunk = CHUNKS[citation.chunk_id]
    assert quote_in_text(citation.verbatim_quote, chunk.raw_text)
    assert citation.doc_id == chunk.doc_id
    assert citation.page_number == chunk.page_number


def _assert_masked_verbatim(text: str, source: str) -> None:
    pieces = _pieces(text)
    assert pieces, text
    for piece in pieces:
        assert normalize_quote(piece) in normalize_quote(source), (piece, source)


@pytest.mark.parametrize("form", FORMS)
def test_answer_and_options_are_verbatim_from_their_source_pairs(form: TheoryQuestionForm) -> None:
    for seed in PROPERTY_SEEDS:
        question = _question(SUBJECT_PAIRS, form, seed)
        _assert_citation_verbatim(question.citation)
        for citation in question.distractor_citations.values():
            _assert_citation_verbatim(citation)
        correct = question.options[question.correct_key]
        source_quote = question.citation.verbatim_quote
        if form is TheoryQuestionForm.DEFINITION_CHOICE:
            _assert_masked_verbatim(correct, source_quote)
            for key, citation in question.distractor_citations.items():
                _assert_masked_verbatim(question.options[key], citation.verbatim_quote)
        else:
            assert all(text in ALL_TERMS for text in question.options.values())
            for key, citation in question.distractor_citations.items():
                owner = next(p for p in SUBJECT_PAIRS if p.definition_quote == citation.verbatim_quote)
                assert question.options[key] == owner.term
                assert citation.verbatim_quote in question.distractor_to_misconception_map[key].rationale_vi
        stem_quote = question.stem.split("> ", 1)[1] if "> " in question.stem else ""
        if form is not TheoryQuestionForm.DEFINITION_CHOICE:
            _assert_masked_verbatim(stem_quote, source_quote)


def test_concept_match_answer_is_the_term_of_the_cited_pair_and_is_hidden_in_the_stem() -> None:
    for seed in PROPERTY_SEEDS:
        question = _question(SUBJECT_PAIRS, TheoryQuestionForm.CONCEPT_MATCH, seed)
        owner = next(p for p in SUBJECT_PAIRS if p.definition_quote == question.citation.verbatim_quote)
        assert question.options[question.correct_key] == owner.term
        assert owner.term.casefold() not in question.stem.casefold()
        assert BLANK in question.stem


def test_fill_blank_answer_is_the_hidden_term_of_another_confirmed_pair() -> None:
    for seed in PROPERTY_SEEDS:
        question = _question(SUBJECT_PAIRS, TheoryQuestionForm.FILL_BLANK, seed)
        answer = question.options[question.correct_key]
        owner = next(p for p in SUBJECT_PAIRS if p.definition_quote == question.citation.verbatim_quote)
        assert not _same_concept(answer, owner.term)
        assert BLANK in question.stem
        # Điền đáp án vào chỗ trống (không phân biệt hoa thường) cho lại đúng trích dẫn của cặp nguồn.
        stem_quote = question.stem.split("> ", 1)[1]
        assert stem_quote.replace(BLANK, answer).casefold() == owner.definition_quote.casefold()
        assert all(not _same_concept(text, owner.term) for text in question.options.values())


def test_definition_choice_hides_the_asked_term_in_every_option() -> None:
    for seed in PROPERTY_SEEDS:
        question = _question(SUBJECT_PAIRS, TheoryQuestionForm.DEFINITION_CHOICE, seed)
        owner = next(p for p in SUBJECT_PAIRS if p.definition_quote == question.citation.verbatim_quote)
        assert f"«{owner.term}»" in question.stem
        for text in question.options.values():
            assert owner.term.casefold() not in text.casefold()


def test_mask_terms_merges_overlaps_and_keeps_the_rest_verbatim() -> None:
    text = "Vật chất vận động; vận động là thuộc tính của vật chất."
    assert mask_terms(text, ["vật chất", "vận động"]) == "____ ____; ____ là thuộc tính của ____."
    assert mask_terms(text, ["Vật chất vận", "chất vận động"]) == "____; vận động là thuộc tính của vật chất."
    assert mask_terms(text, ["không có"]) == text


def test_quote_no_longer_in_its_chunk_is_an_error() -> None:
    tampered = SUBJECT_PAIRS[0].model_copy(update={"definition_quote": "Vật chất là phạm trù kinh tế học."})
    pairs = [tampered, *SUBJECT_PAIRS[1:]]
    with pytest.raises(QuoteMismatchError):
        for seed in PROPERTY_SEEDS:
            _generate(pairs, TheoryQuestionForm.CONCEPT_MATCH, seed, chapter_code="ch1")


def test_missing_or_foreign_chunk_is_an_error() -> None:
    orphan = SUBJECT_PAIRS[0].model_copy(update={"chunk_id": "chk_missing"})
    with pytest.raises(ValueError, match="thiếu chunk"):
        for seed in PROPERTY_SEEDS:
            _generate([orphan, *SUBJECT_PAIRS[1:]], TheoryQuestionForm.CONCEPT_MATCH, seed, chapter_code="ch1")
    foreign = SUBJECT_PAIRS[0].model_copy(update={"chunk_id": "chk_arm_ch3"})
    with pytest.raises(ValueError, match="khác môn"):
        for seed in PROPERTY_SEEDS:
            _generate([foreign, *SUBJECT_PAIRS[1:]], TheoryQuestionForm.CONCEPT_MATCH, seed, chapter_code="ch1")


# --------------------------------------------------------------------------- #
# Thiếu căn cứ ⇒ ReviewTicket
# --------------------------------------------------------------------------- #


def _assert_ticket(result: TheoryQuestion | ReviewTicket) -> ReviewTicket:
    assert isinstance(result, ReviewTicket), result
    assert result.reason.startswith(NOT_ENOUGH_EVIDENCE)
    assert result.blueprint_id == TICKET_CONTEXT["blueprint_id"]
    assert result.slot_index == TICKET_CONTEXT["slot_index"]
    assert result.topic_key == TICKET_CONTEXT["topic_key"]
    assert result.attempts == []
    return result


@pytest.mark.parametrize("form", FORMS)
def test_fewer_than_four_concepts_gives_a_review_ticket(form: TheoryQuestionForm) -> None:
    three = _pairs("chk_phil_ch2", CH2_DEFINITIONS)
    for seed in range(20):
        _assert_ticket(_generate(three, form, seed))


def test_duplicate_concepts_do_not_count_as_distinct_distractors() -> None:
    """4 cặp nhưng chỉ 3 khái niệm (một term lặp lại khác hoa thường) ⇒ không đủ 3 phương án nhiễu."""
    pairs = [*_pairs("chk_phil_ch2", CH2_DEFINITIONS), _pair("chk_phil_ch2", "chân lý", CH2_DEFINITIONS[2][1], index=9)]
    for seed in range(20):
        _assert_ticket(_generate(pairs, TheoryQuestionForm.CONCEPT_MATCH, seed))


def test_definition_choice_needs_three_other_concepts_in_the_same_chapter() -> None:
    """Môn có 8 khái niệm nhưng chương ch2 chỉ có 3: dạng chọn định nghĩa đúng không sinh ở ch2."""
    for seed in range(20):
        ticket = _assert_ticket(
            _generate(SUBJECT_PAIRS, TheoryQuestionForm.DEFINITION_CHOICE, seed, chapter_code="ch2")
        )
        assert "cùng chương" in ticket.reason
        assert isinstance(
            _generate(SUBJECT_PAIRS, TheoryQuestionForm.CONCEPT_MATCH, seed, chapter_code="ch2"), TheoryQuestion
        )


def test_chapter_without_confirmed_pairs_gives_a_review_ticket() -> None:
    ticket = _assert_ticket(_generate(SUBJECT_PAIRS, TheoryQuestionForm.CONCEPT_MATCH, 1, chapter_code="ch7"))
    assert "ch7" in ticket.reason
    ticket = _assert_ticket(_generate(OTHER_PAIRS, TheoryQuestionForm.CONCEPT_MATCH, 1))
    assert SUBJECT in ticket.reason


def test_fill_blank_without_a_shared_term_gives_a_review_ticket() -> None:
    """Không trích dẫn nào chứa term của khái niệm khác ⇒ không có chỗ để che."""
    for seed in range(20):
        ticket = _assert_ticket(_generate(OTHER_PAIRS, TheoryQuestionForm.FILL_BLANK, seed, subject_code=OTHER_SUBJECT))
        assert "thuật ngữ" in ticket.reason


def test_fill_blank_without_enough_distractors_gives_a_review_ticket() -> None:
    """Có chỗ che (vật chất trong định nghĩa vận động) nhưng chỉ còn 2 khái niệm khác làm phương án nhiễu."""
    pairs = _pairs("chk_phil_ch1", CH1_DEFINITIONS[:4])
    for seed in range(20):
        ticket = _assert_ticket(_generate(pairs, TheoryQuestionForm.FILL_BLANK, seed))
        assert "3 thuật ngữ" in ticket.reason


def test_seed_outside_the_seed_space_is_rejected() -> None:
    for seed in (-1, 2**63):
        with pytest.raises(ValueError, match="seed"):
            _generate(SUBJECT_PAIRS, TheoryQuestionForm.CONCEPT_MATCH, seed)


# --------------------------------------------------------------------------- #
# Property test: ≥ 100 seed cho mỗi dạng câu
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("chapter_code", [None, "ch1"])
def test_property_four_distinct_options_and_exactly_one_correct(
    form: TheoryQuestionForm, chapter_code: str | None
) -> None:
    for seed in PROPERTY_SEEDS:
        question = _question(SUBJECT_PAIRS, form, seed, chapter_code=chapter_code)
        assert set(question.options) == set(OptionKey)
        assert len({normalize_quote(text).casefold() for text in question.options.values()}) == 4
        wrong = set(OptionKey) - {question.correct_key}
        assert set(question.distractor_to_misconception_map) == wrong
        assert set(question.distractor_citations) == wrong
        if chapter_code is not None:
            assert question.chapter_code == chapter_code
        # Đúng một phương án ứng với cặp được trích dẫn; ba phương án còn lại ứng với khái niệm khác.
        cited = next(p for p in SUBJECT_PAIRS if p.definition_quote == question.citation.verbatim_quote)
        distractor_quotes = {citation.verbatim_quote for citation in question.distractor_citations.values()}
        assert cited.definition_quote not in distractor_quotes
        assert len(distractor_quotes) == 3


# --------------------------------------------------------------------------- #
# Đọc từ Vault
# --------------------------------------------------------------------------- #


def test_vault_backed_generation_matches_the_pure_function() -> None:
    store = IngestionStore()
    doc_id = document_id("e")
    add_document(store, doc_id)
    lines = ["Mở đầu phần lý thuyết."] + [quote for _, quote in CH1_DEFINITIONS]
    chunks = ingest_confirmed_text(store, confirmed(doc_id, [lines], subject_code=SUBJECT, chapter_code="ch1"))
    assert len(chunks) == 1
    for term, quote in CH1_DEFINITIONS:
        create_concept_pair(store, chunk_id=chunks[0].chunk_id, term=term, definition_quote=quote, now=NOW)
    pairs = store.list_concept_pairs(SUBJECT)
    context = {"subject_code": SUBJECT, "seed": 21, **TICKET_CONTEXT}
    for form in FORMS:
        from_vault = generate_theory_question_from_vault(store, form=form, **context)
        direct = generate_theory_question(pairs, {chunks[0].chunk_id: chunks[0]}, form=form, **context)
        assert isinstance(from_vault, TheoryQuestion)
        assert from_vault.model_dump_json() == direct.model_dump_json()
        assert from_vault.citation.doc_id == doc_id
