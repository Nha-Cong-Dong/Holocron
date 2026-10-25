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
from holocron_core.services.vault import create_concept_pair, ingest_confirmed_text, update_chunk_labels
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


# --------------------------------------------------------------------------- #
# Đúng một đáp án: trích dẫn trùng hoặc lồng nhau, thuật ngữ điền được vào chỗ trống
# --------------------------------------------------------------------------- #

OVERLAP_SEEDS = range(300)

OVERLAP_CH1_LINES = [
    "Vật chất là phạm trù triết học dùng để chỉ thực tại khách quan.",
    "Vận động là phương thức tồn tại của vật chất.",
    "Không gian là hình thức tồn tại của vật chất.",
    "Thời gian là hình thức tồn tại của vật chất.",
    "Ý thức là sự phản ánh năng động, sáng tạo thế giới khách quan vào bộ óc con người.",
    "Ý thức xã hội là mặt tinh thần của đời sống xã hội.",
    "Tồn tại xã hội là mặt vật chất của đời sống xã hội.",
]
OVERLAP_CH2_LINES = [
    "Thực tiễn là toàn bộ hoạt động vật chất có mục đích của con người.",
    "Nhận thức là quá trình phản ánh hiện thực khách quan vào bộ óc con người.",
    "Chân lý là tri thức phù hợp với hiện thực khách quan.",
    "CHÂN LÝ LÀ TRI THỨC PHÙ HỢP VỚI HIỆN THỰC KHÁCH QUAN.",
]
OVERLAP_CH1_DEFINITIONS = [
    ("Vật chất", OVERLAP_CH1_LINES[0]),
    # Trùng trích dẫn với "Vật chất".
    ("Thực tại khách quan", OVERLAP_CH1_LINES[0]),
    ("Vận động", OVERLAP_CH1_LINES[1]),
    # Trích dẫn nằm trong trích dẫn của "Vận động".
    ("Phương thức tồn tại", "phương thức tồn tại của vật chất"),
    ("Không gian", OVERLAP_CH1_LINES[2]),
    # Trùng trích dẫn với "Không gian"; che "Không gian" rồi điền "Thời gian" cho ra một câu có trong Vault.
    ("Hình thức tồn tại", OVERLAP_CH1_LINES[2]),
    ("Thời gian", OVERLAP_CH1_LINES[3]),
    ("Ý thức", OVERLAP_CH1_LINES[4]),
    # Trích dẫn nằm trong trích dẫn của "Ý thức".
    ("Phản ánh năng động", "sự phản ánh năng động, sáng tạo thế giới khách quan"),
    # Thuật ngữ chứa thuật ngữ "Ý thức".
    ("Ý thức xã hội", OVERLAP_CH1_LINES[5]),
    ("Tồn tại xã hội", OVERLAP_CH1_LINES[6]),
]
OVERLAP_CH2_DEFINITIONS = [
    ("Thực tiễn", OVERLAP_CH2_LINES[0]),
    ("Nhận thức", OVERLAP_CH2_LINES[1]),
    ("Chân lý", OVERLAP_CH2_LINES[2]),
    # Trùng trích dẫn với "Chân lý", chỉ khác hoa thường và khoảng trắng.
    ("Tri thức đúng", "CHÂN LÝ LÀ  TRI THỨC PHÙ HỢP VỚI HIỆN THỰC KHÁCH QUAN."),
    # Trích dẫn nằm trong trích dẫn của "Nhận thức".
    ("Hiện thực khách quan", "phản ánh hiện thực khách quan vào bộ óc"),
]


def _lines_chunk(chunk_id: str, chapter: str, lines: list[str], page: int, *, subject: str = SUBJECT) -> KnowledgeChunk:
    raw_text = "Mở đầu phần lý thuyết.\n" + "\n".join(lines)
    return KnowledgeChunk(
        chunk_id=chunk_id,
        doc_id=DOC,
        subject_code=subject,
        chapter_code=chapter,
        page_number=page,
        raw_text=raw_text,
        verbatim_quote=lines[0],
        category=ChunkCategory.THEORY,
        token_count=80,
        label_status="confirmed",
    )


def _pairs_in(chunk: KnowledgeChunk, definitions: list[tuple[str, str]]) -> list[ConceptPair]:
    return [
        ConceptPair(
            pair_id=f"cp_{chunk.chunk_id}_{index:02d}",
            chunk_id=chunk.chunk_id,
            subject_code=chunk.subject_code,
            chapter_code=chunk.chapter_code,
            term=term,
            definition_quote=quote,
            status=ConceptPairStatus.CONFIRMED,
            proposed_by="user",
            created_at=NOW,
            confirmed_at=NOW,
        )
        for index, (term, quote) in enumerate(definitions)
    ]


OVERLAP_CHUNKS = {
    chunk.chunk_id: chunk
    for chunk in (
        _lines_chunk("chk_overlap_ch1", "ch1", OVERLAP_CH1_LINES, 4),
        _lines_chunk("chk_overlap_ch2", "ch2", OVERLAP_CH2_LINES, 12),
    )
}
OVERLAP_PAIRS = _pairs_in(OVERLAP_CHUNKS["chk_overlap_ch1"], OVERLAP_CH1_DEFINITIONS) + _pairs_in(
    OVERLAP_CHUNKS["chk_overlap_ch2"], OVERLAP_CH2_DEFINITIONS
)


def _overlap(first: str, second: str) -> bool:
    first_key, second_key = normalize_quote(first).casefold(), normalize_quote(second).casefold()
    return first_key in second_key or second_key in first_key


def _vault_correct_keys(question: TheoryQuestion) -> set[OptionKey]:
    """Các phương án đúng theo dữ liệu Vault (không dựa vào `correct_key` do bộ sinh ghi)."""
    cited = question.citation.verbatim_quote
    correct: set[OptionKey] = set()
    for key, text in question.options.items():
        if question.form is TheoryQuestionForm.CONCEPT_MATCH:
            # Đúng nếu có cặp mang thuật ngữ này mà trích dẫn trùng hoặc lồng với đoạn trích ở thân câu.
            hit = any(_same_concept(p.term, text) and _overlap(p.definition_quote, cited) for p in OVERLAP_PAIRS)
        elif question.form is TheoryQuestionForm.DEFINITION_CHOICE:
            # Đúng nếu định nghĩa nằm ở phương án này thuộc về một trích dẫn trùng hoặc lồng với trích dẫn đáp án.
            source = cited if key is question.correct_key else question.distractor_citations[key].verbatim_quote
            hit = _overlap(source, cited)
        else:
            # Đúng nếu điền thuật ngữ vào chỗ trống cho ra một đoạn có trong Vault, hoặc thuật ngữ lồng với đáp án.
            filled = normalize_quote(question.stem.split("> ", 1)[1].replace(BLANK, text)).casefold()
            in_vault = any(filled in normalize_quote(c.raw_text).casefold() for c in OVERLAP_CHUNKS.values())
            hit = in_vault or _overlap(text, question.options[question.correct_key])
        if hit:
            correct.add(key)
    return correct


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("chapter_code", [None, "ch1", "ch2"])
def test_overlapping_quotes_never_give_two_correct_answers(form: TheoryQuestionForm, chapter_code: str | None) -> None:
    generated = 0
    for seed in OVERLAP_SEEDS:
        result = generate_theory_question(
            OVERLAP_PAIRS,
            OVERLAP_CHUNKS,
            subject_code=SUBJECT,
            form=form,
            seed=seed,
            chapter_code=chapter_code,
            **TICKET_CONTEXT,
        )
        if isinstance(result, ReviewTicket):
            _assert_ticket(result)
            continue
        generated += 1
        assert _vault_correct_keys(result) == {result.correct_key}, (seed, result.stem, result.options)
        for citation in result.distractor_citations.values():
            assert not _overlap(citation.verbatim_quote, result.citation.verbatim_quote)
    assert generated > 0


def test_only_duplicate_or_nested_quotes_give_a_review_ticket() -> None:
    """4 khái niệm nhưng chỉ 2 định nghĩa độc lập: mỗi đáp án chỉ còn 2 phương án nhiễu hợp lệ."""
    four = [
        pair
        for pair in OVERLAP_PAIRS
        if pair.term in {"Vật chất", "Thực tại khách quan", "Vận động", "Phương thức tồn tại"}
    ]
    assert len(four) == 4
    for form in (TheoryQuestionForm.CONCEPT_MATCH, TheoryQuestionForm.DEFINITION_CHOICE):
        for seed in range(50):
            _assert_ticket(
                generate_theory_question(
                    four, OVERLAP_CHUNKS, subject_code=SUBJECT, form=form, seed=seed, **TICKET_CONTEXT
                )
            )


def test_fill_blank_skips_a_term_that_also_fills_the_blank() -> None:
    """Che "Không gian" trong "Không gian là hình thức tồn tại của vật chất.": "Thời gian" cũng điền đúng theo Vault."""
    seen = False
    for seed in OVERLAP_SEEDS:
        result = generate_theory_question(
            OVERLAP_PAIRS,
            OVERLAP_CHUNKS,
            subject_code=SUBJECT,
            form=TheoryQuestionForm.FILL_BLANK,
            seed=seed,
            **TICKET_CONTEXT,
        )
        assert isinstance(result, TheoryQuestion)
        if result.options[result.correct_key] == "Không gian":
            seen = True
            assert "Thời gian" not in result.options.values()
    assert seen


def test_model_rejects_a_distractor_citation_overlapping_the_answer_citation() -> None:
    question = _question(SUBJECT_PAIRS, TheoryQuestionForm.CONCEPT_MATCH, 7)
    wrong = next(iter(question.distractor_citations))
    for quote in (
        question.citation.verbatim_quote,
        "  " + question.citation.verbatim_quote.upper(),
        question.citation.verbatim_quote[:-1],
    ):
        data = question.model_dump()
        data["distractor_citations"][wrong]["verbatim_quote"] = quote
        with pytest.raises(ValidationError, match="hai đáp án đúng"):
            TheoryQuestion.model_validate(data)


def test_model_rejects_a_fill_blank_distractor_overlapping_the_hidden_term() -> None:
    question = _question(SUBJECT_PAIRS, TheoryQuestionForm.FILL_BLANK, 7)
    wrong = next(iter(question.distractor_citations))
    data = question.model_dump()
    data["options"][wrong] = question.options[question.correct_key] + " xã hội"
    with pytest.raises(ValidationError, match="hai đáp án đúng"):
        TheoryQuestion.model_validate(data)


# --------------------------------------------------------------------------- #
# Khoá phân xử khi hoà và giới hạn che tên khái niệm
# --------------------------------------------------------------------------- #


def test_pool_order_breaks_ties_on_definition_quote() -> None:
    """Hai cặp cùng `(chapter_code, term, chunk_id)`, khác `definition_quote`: đảo đầu vào vẫn cùng `pool_sha256`."""
    tied = [
        _pair("chk_phil_ch1", "Vật chất", "phạm trù triết học dùng để chỉ thực tại khách quan", index=80),
        _pair("chk_phil_ch1", "Vật chất", "phương thức tồn tại của vật chất", index=81),
    ]
    forward = SUBJECT_PAIRS + tied
    backward = list(reversed(tied)) + SUBJECT_PAIRS
    assert candidate_pool(forward, SUBJECT) == candidate_pool(backward, SUBJECT)
    assert pool_sha256(forward, SUBJECT) == pool_sha256(backward, SUBJECT)
    assert pool_sha256(tied, SUBJECT) == pool_sha256(list(reversed(tied)), SUBJECT)
    for form in FORMS:
        assert _question(forward, form, 9).model_dump_json() == _question(backward, form, 9).model_dump_json()


INFLECTION_SUBJECT = "IT3070"
INFLECTION_LINES = [
    "Processes are programs in execution.",
    "A thread is a basic unit of CPU utilization.",
    "The scheduler selects a process from the ready queue.",
    "A deadlock is a situation where processes wait for each other forever.",
    "A semaphore is an integer variable accessed only through atomic operations.",
]
INFLECTION_CHUNK = _lines_chunk("chk_inflection_ch2", "ch2", INFLECTION_LINES, 2, subject=INFLECTION_SUBJECT)
INFLECTION_PAIRS = _pairs_in(
    INFLECTION_CHUNK,
    [
        ("Process", INFLECTION_LINES[0]),
        ("Thread", INFLECTION_LINES[1]),
        ("Scheduler", INFLECTION_LINES[2]),
        ("Deadlock", INFLECTION_LINES[3]),
        ("Semaphore", INFLECTION_LINES[4]),
    ],
)


def test_inflected_term_is_not_masked_and_the_quote_stays_verbatim() -> None:
    """Giới hạn đã biết: "Process" không che được "Processes"; câu vẫn sinh, trích dẫn vẫn nguyên văn."""
    assert mask_terms(INFLECTION_LINES[0], ["Process"]) == INFLECTION_LINES[0]
    seen = False
    for seed in range(100):
        result = generate_theory_question(
            INFLECTION_PAIRS,
            {INFLECTION_CHUNK.chunk_id: INFLECTION_CHUNK},
            subject_code=INFLECTION_SUBJECT,
            form=TheoryQuestionForm.CONCEPT_MATCH,
            seed=seed,
            **TICKET_CONTEXT,
        )
        assert isinstance(result, TheoryQuestion)
        assert quote_in_text(result.citation.verbatim_quote, INFLECTION_CHUNK.raw_text)
        if result.options[result.correct_key] == "Process":
            seen = True
            assert result.citation.verbatim_quote == INFLECTION_LINES[0]
            assert result.stem.endswith(f"> {INFLECTION_LINES[0]}")
            assert BLANK not in result.stem
    assert seen


# --------------------------------------------------------------------------- #
# I4: giá trị ghim cứng (phát hiện khi Python đổi thuật toán `random` hoặc khi code đổi)
# --------------------------------------------------------------------------- #

PINNED_SEED = 2026
PINNED_POOL_SHA256 = "c6dbfff6b149b136a3160c265da2757b5b9832ec2d0c8e2cd64b9045cc5bc538"
PINNED_RNG_FIRST_BITS = 0x61E3CF69B9D394AE
PINNED_QUESTIONS: dict[tuple[TheoryQuestionForm, str | None], dict[str, object]] = {
    (TheoryQuestionForm.CONCEPT_MATCH, None): {
        "stem": "Khái niệm nào ứng với đoạn trích sau?\n\n> ____ là hình thức tồn tại của vật chất xét về quảng tính.",
        "options": ["Vận động", "Thời gian", "Không gian", "Ý thức"],
        "correct_key": "C",
        "misconceptions": {
            "A": THEORY_SAME_CHAPTER_CONCEPT,
            "B": THEORY_SAME_CHAPTER_CONCEPT,
            "D": THEORY_SAME_CHAPTER_CONCEPT,
        },
        "citation": "Không gian là hình thức tồn tại của vật chất xét về quảng tính.",
    },
    (TheoryQuestionForm.CONCEPT_MATCH, "ch2"): {
        "stem": "Khái niệm nào ứng với đoạn trích sau?\n\n> ____ là tri thức phù hợp với hiện thực khách quan.",
        "options": ["Chân lý", "Thực tiễn", "Nhận thức", "Không gian"],
        "correct_key": "A",
        "misconceptions": {
            "B": THEORY_SAME_CHAPTER_CONCEPT,
            "C": THEORY_SAME_CHAPTER_CONCEPT,
            "D": THEORY_SAME_SUBJECT_CONCEPT,
        },
        "citation": "Chân lý là tri thức phù hợp với hiện thực khách quan.",
    },
    (TheoryQuestionForm.FILL_BLANK, None): {
        "stem": "Điền thuật ngữ thích hợp vào chỗ trống trong đoạn trích sau:\n\n> Vận động là phương thức tồn tại của ____.",
        "options": ["Ý thức", "Vật chất", "Không gian", "Thời gian"],
        "correct_key": "B",
        "misconceptions": {
            "A": THEORY_SAME_CHAPTER_CONCEPT,
            "C": THEORY_SAME_CHAPTER_CONCEPT,
            "D": THEORY_SAME_CHAPTER_CONCEPT,
        },
        "citation": "Vận động là phương thức tồn tại của vật chất.",
    },
    (TheoryQuestionForm.DEFINITION_CHOICE, None): {
        "stem": "Phát biểu nào là định nghĩa của «Không gian»?",
        "options": [
            "____ là hình thức tồn tại của vật chất xét về độ dài diễn biến.",
            "____ là hình thức tồn tại của vật chất xét về quảng tính.",
            "____ là phương thức tồn tại của vật chất.",
            "____ là sự phản ánh năng động, sáng tạo thế giới khách quan vào bộ óc con người.",
        ],
        "correct_key": "B",
        "misconceptions": {
            "A": THEORY_SAME_CHAPTER_CONCEPT,
            "C": THEORY_SAME_CHAPTER_CONCEPT,
            "D": THEORY_SAME_CHAPTER_CONCEPT,
        },
        "citation": "Không gian là hình thức tồn tại của vật chất xét về quảng tính.",
    },
}


def test_pinned_pool_sha256_and_rng() -> None:
    assert GENERATOR_VERSION == "theory_mcq4_v1"
    assert pool_sha256(SUBJECT_PAIRS, SUBJECT) == PINNED_POOL_SHA256
    assert theory_rng(PINNED_POOL_SHA256, PINNED_SEED).getrandbits(64) == PINNED_RNG_FIRST_BITS


@pytest.mark.parametrize(("form", "chapter_code"), list(PINNED_QUESTIONS))
def test_pinned_question_for_version_pool_and_seed(form: TheoryQuestionForm, chapter_code: str | None) -> None:
    question = _question(SUBJECT_PAIRS, form, PINNED_SEED, chapter_code=chapter_code)
    assert question.generator_version == GENERATOR_VERSION
    assert question.pool_sha256 == PINNED_POOL_SHA256
    assert question.seed == PINNED_SEED
    actual = {
        "stem": question.stem,
        "options": [question.options[key] for key in OptionKey],
        "correct_key": question.correct_key.value,
        "misconceptions": {
            key.value: ref.misconception_code
            for key, ref in sorted(question.distractor_to_misconception_map.items(), key=lambda item: item[0].value)
        },
        "citation": question.citation.verbatim_quote,
    }
    assert actual == PINNED_QUESTIONS[(form, chapter_code)]


# --------------------------------------------------------------------------- #
# I4: chunk cùng môn không thuộc `pool_sha256` nên không được làm đổi câu (sửa sau review)
# --------------------------------------------------------------------------- #

# Mỗi dòng là trích dẫn mẫu của câu điền khuyết ghim cứng với một phương án nhiễu điền vào chỗ trống. Với luật cũ (so
# với `raw_text` của mọi chunk cùng môn), chunk này biến cả ba phương án nhiễu thành "đáp án đúng thứ hai" và làm câu
# đổi dù không cặp nào đổi.
EXTRA_LINES = [
    "Vận động là phương thức tồn tại của ý thức.",
    "Vận động là phương thức tồn tại của không gian.",
    "Vận động là phương thức tồn tại của thời gian.",
    "Ý thức là hình thức tồn tại của vật chất xét về quảng tính.",
    "Thực tiễn là tri thức phù hợp với hiện thực khách quan.",
]


def _extra_chunk(chunk_id: str, chapter: str, label_status: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        doc_id=DOC,
        subject_code=SUBJECT,
        chapter_code=chapter,
        page_number=40,
        raw_text="Ghi chú thêm.\n" + "\n".join(EXTRA_LINES),
        verbatim_quote=EXTRA_LINES[0],
        category=ChunkCategory.THEORY,
        token_count=50,
        label_status=label_status,
    )


EXTRA_CHUNKS = {
    chunk.chunk_id: chunk
    for chunk in (
        _extra_chunk("chk_phil_extra_proposed", "ch1", "proposed"),
        _extra_chunk("chk_phil_extra_confirmed", "ch2", "confirmed"),
    )
}


def test_the_extra_chunk_would_have_conflicted_under_the_old_rule() -> None:
    """Chứng minh test I4 bên dưới có ý nghĩa: mọi phương án nhiễu của câu ghim cứng điền vào chỗ trống đều cho ra một
    câu có trong chunk thêm vào."""
    question = _question(SUBJECT_PAIRS, TheoryQuestionForm.FILL_BLANK, PINNED_SEED)
    masked = question.stem.split("> ", 1)[1]
    extra_text = normalize_quote("\n".join(chunk.raw_text for chunk in EXTRA_CHUNKS.values())).casefold()
    for key in set(OptionKey) - {question.correct_key}:
        filled = normalize_quote(masked.replace(BLANK, question.options[key])).casefold()
        assert filled in extra_text, question.options[key]


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("chapter_code", [None, "ch1", "ch2"])
@pytest.mark.parametrize("label_status", ["proposed", "confirmed", "both"])
def test_adding_same_subject_chunks_without_pairs_keeps_questions_byte_identical(
    form: TheoryQuestionForm, chapter_code: str | None, label_status: str
) -> None:
    extra = {
        chunk_id: chunk
        for chunk_id, chunk in EXTRA_CHUNKS.items()
        if label_status == "both" or chunk.label_status == label_status
    }
    assert extra
    widened = {**CHUNKS, **extra}
    assert pool_sha256(SUBJECT_PAIRS, SUBJECT) == PINNED_POOL_SHA256
    for seed in [*range(60), PINNED_SEED]:
        context = {"subject_code": SUBJECT, "form": form, "seed": seed, "chapter_code": chapter_code, **TICKET_CONTEXT}
        before = generate_theory_question(SUBJECT_PAIRS, CHUNKS, **context)
        after = generate_theory_question(SUBJECT_PAIRS, widened, **context)
        assert after.model_dump_json() == before.model_dump_json(), (seed, form, chapter_code)


def test_pinned_questions_survive_extra_same_subject_chunks() -> None:
    widened = {**CHUNKS, **EXTRA_CHUNKS}
    for form, chapter_code in PINNED_QUESTIONS:
        result = generate_theory_question(
            SUBJECT_PAIRS,
            widened,
            subject_code=SUBJECT,
            form=form,
            seed=PINNED_SEED,
            chapter_code=chapter_code,
            **TICKET_CONTEXT,
        )
        assert isinstance(result, TheoryQuestion)
        assert result.pool_sha256 == PINNED_POOL_SHA256
        assert [result.options[key] for key in OptionKey] == PINNED_QUESTIONS[(form, chapter_code)]["options"]
        assert result.correct_key.value == PINNED_QUESTIONS[(form, chapter_code)]["correct_key"]


def test_vault_backed_questions_ignore_new_same_subject_chunks_without_pairs() -> None:
    """Qua `VaultRepository`: thêm chunk cùng môn (chưa xác nhận, rồi xác nhận nhãn), không thêm cặp ⇒ câu giống từng
    byte với cùng seed, cho cả ba dạng câu."""
    store = IngestionStore()
    doc_id, extra_doc = document_id("f"), document_id("d")
    add_document(store, doc_id)
    add_document(store, extra_doc)
    lines = ["Mở đầu phần lý thuyết.", *(quote for _, quote in CH1_DEFINITIONS)]
    [source] = ingest_confirmed_text(store, confirmed(doc_id, [lines], subject_code=SUBJECT, chapter_code="ch1"))
    for term, quote in CH1_DEFINITIONS:
        create_concept_pair(store, chunk_id=source.chunk_id, term=term, definition_quote=quote, now=NOW)
    seeds = [*range(40), PINNED_SEED]

    def snapshot() -> list[str]:
        results = [
            generate_theory_question_from_vault(store, subject_code=SUBJECT, form=form, seed=seed, **TICKET_CONTEXT)
            for form in FORMS
            for seed in seeds
        ]
        assert all(isinstance(result, TheoryQuestion) for result in results)
        return [result.model_dump_json() for result in results]

    baseline = snapshot()
    digest = pool_sha256(store.list_concept_pairs(SUBJECT), SUBJECT)
    [extra] = ingest_confirmed_text(
        store, confirmed(extra_doc, [["Ghi chú thêm.", *EXTRA_LINES]], subject_code=SUBJECT, chapter_code="ch1")
    )
    assert extra.label_status == "proposed"
    assert snapshot() == baseline
    confirmed_extra = update_chunk_labels(store, extra.chunk_id, expected_version=extra.version)
    assert confirmed_extra.label_status == "confirmed"
    assert snapshot() == baseline
    assert pool_sha256(store.list_concept_pairs(SUBJECT), SUBJECT) == digest
