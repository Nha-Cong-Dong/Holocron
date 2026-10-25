"""`services/vault.py` trên ba loại kho (in-memory, SQLite có FTS5, SQLite thiếu FTS5): nhập chunk, liệt kê, sửa nhãn,
tìm BM25, cặp khái niệm (I3) và `VaultCitationProvider` lọc theo môn (ADR-006 mục 6)."""

import unicodedata
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from holocron_core.domain.repositories import (
    AppStore,
    MissingReferenceError,
    RecordNotFoundError,
    StorageError,
    VaultSearchUnavailableError,
    VersionConflictError,
)
from holocron_core.domain.schemas import BlueprintSlot, ChunkCategory, KnowledgeChunk
from holocron_core.domain.vault import ConceptPair, ConceptPairStatus, SearchHit, find_term_spans
from holocron_core.services import vault as vault_service
from holocron_core.services.vault import (
    QuoteMismatchError,
    VaultCitationProvider,
    bm25_scores,
    confirm_concept_pair,
    create_concept_pair,
    fts_query,
    ingest_confirmed_text,
    reject_concept_pair,
    search_chunks,
    update_chunk_labels,
)
from tests.conftest import SLOT
from tests.vault_support import (
    ARM_PAGES,
    NOW,
    PHILOSOPHY_PAGES,
    STORE_KINDS,
    add_document,
    confirmed,
    document_id,
    open_store,
)

ARM_DOC = document_id("a")
PHIL_DOC = document_id("b")
ARM_TOPIC = "mp.arm.cpsr.flags"


@pytest.fixture(params=STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppStore]:
    yield from open_store(str(request.param), tmp_path, monkeypatch)


def _seed(store: AppStore) -> tuple[list[KnowledgeChunk], list[KnowledgeChunk]]:
    add_document(store, ARM_DOC)
    add_document(store, PHIL_DOC)
    arm = ingest_confirmed_text(store, confirmed(ARM_DOC, ARM_PAGES, topic_keys=[ARM_TOPIC]))
    phil = ingest_confirmed_text(
        store, confirmed(PHIL_DOC, PHILOSOPHY_PAGES, subject_code="SSH1110", chapter_code="ch1")
    )
    return arm, phil


# --------------------------------------------------------------------------- #
# Nhập, liệt kê, sửa nhãn
# --------------------------------------------------------------------------- #


def test_ingest_stores_chunks_and_is_idempotent(store: AppStore) -> None:
    arm, phil = _seed(store)
    assert len(arm) == 2 and len(phil) == 1
    for chunk in arm + phil:
        assert store.get_chunk(chunk.chunk_id) == chunk
    again = ingest_confirmed_text(store, confirmed(ARM_DOC, ARM_PAGES, topic_keys=[ARM_TOPIC]))
    assert again == arm
    assert len(store.list_chunks("ET4010")) == 2


def test_reingest_keeps_edited_labels(store: AppStore) -> None:
    arm, _ = _seed(store)
    edited = update_chunk_labels(store, arm[0].chunk_id, expected_version=1, chapter_code="ch4")
    again = ingest_confirmed_text(store, confirmed(ARM_DOC, ARM_PAGES, topic_keys=[ARM_TOPIC]))
    assert again[0] == edited


def test_ingest_needs_an_existing_document(store: AppStore) -> None:
    with pytest.raises(MissingReferenceError):
        ingest_confirmed_text(store, confirmed(ARM_DOC, ARM_PAGES))


def test_duplicate_chunk_id_is_a_storage_error(store: AppStore) -> None:
    arm, _ = _seed(store)
    with pytest.raises(StorageError):
        store.add_chunk(arm[0], "x")


def test_embedding_model_is_refused(store: AppStore) -> None:
    arm, _ = _seed(store)
    embedded = arm[0].model_copy(update={"embedding_model": "e5", "chunk_id": "chk_new"})
    with pytest.raises(ValueError, match="embedding_model"):
        store.add_chunk(embedded, "x")


def test_list_filters_and_paginates(store: AppStore) -> None:
    arm, phil = _seed(store)
    total, page = vault_service.list_chunks(store, "ET4010")
    assert total == 2 and page == arm
    assert vault_service.list_chunks(store, "SSH1110") == (1, phil)
    assert vault_service.list_chunks(store, "ET4010", chapter_code="ch9") == (0, [])
    assert vault_service.list_chunks(store, "ET4010", topic_keys=["x.y.z"]) == (0, [])
    assert vault_service.list_chunks(store, "ET4010", topic_keys=["x.y.z", ARM_TOPIC])[0] == 2
    assert vault_service.list_chunks(store, "ET4010", offset=1, limit=1) == (2, arm[1:])
    assert vault_service.list_chunks(store, "ET4010", label_status="confirmed") == (0, [])
    update_chunk_labels(store, arm[1].chunk_id, expected_version=1)
    total, confirmed_only = vault_service.list_chunks(store, "ET4010", label_status="confirmed")
    assert total == 1 and confirmed_only[0].chunk_id == arm[1].chunk_id


def test_update_labels_confirms_and_bumps_version(store: AppStore) -> None:
    arm, _ = _seed(store)
    chunk = arm[0]
    updated = update_chunk_labels(
        store,
        chunk.chunk_id,
        expected_version=1,
        chapter_code="ch3.1",
        category=ChunkCategory.PRACTICE,
        topic_keys=["mp.arm.alu.add", ARM_TOPIC],
    )
    assert updated.label_status == "confirmed"
    assert updated.version == 2
    assert updated.chapter_code == "ch3.1"
    assert updated.category is ChunkCategory.PRACTICE
    assert updated.tags == ["#practice_example"]
    assert updated.topic_keys == ["mp.arm.alu.add", ARM_TOPIC]
    assert (updated.raw_text, updated.verbatim_quote) == (chunk.raw_text, chunk.verbatim_quote)
    assert store.get_chunk(chunk.chunk_id) == updated
    assert store.list_chunks("ET4010", chapter_code="ch3.1") == [updated]

    kept = update_chunk_labels(store, chunk.chunk_id, expected_version=2)
    assert (kept.chapter_code, kept.category, kept.topic_keys, kept.version) == (
        "ch3.1",
        ChunkCategory.PRACTICE,
        updated.topic_keys,
        3,
    )


def test_update_labels_conflict_and_not_found(store: AppStore) -> None:
    arm, _ = _seed(store)
    with pytest.raises(VersionConflictError) as conflict:
        update_chunk_labels(store, arm[0].chunk_id, expected_version=5)
    assert conflict.value.current_version == 1
    with pytest.raises(RecordNotFoundError):
        update_chunk_labels(store, "chk_missing", expected_version=1)
    gone = arm[0].model_copy(update={"chunk_id": "chk_gone"})
    with pytest.raises(RecordNotFoundError):
        store.update_chunk_labels(gone, 1)


def test_update_labels_rejects_invalid_values(store: AppStore) -> None:
    arm, _ = _seed(store)
    with pytest.raises(ValidationError):
        update_chunk_labels(store, arm[0].chunk_id, expected_version=1, chapter_code="chuong3")
    assert store.get_chunk(arm[0].chunk_id) == arm[0]


# --------------------------------------------------------------------------- #
# Tìm kiếm BM25
# --------------------------------------------------------------------------- #


def _ids(hits: list[SearchHit]) -> list[str]:
    return [hit.chunk.chunk_id for hit in hits]


def test_search_ranks_by_bm25_within_the_subject(store: AppStore) -> None:
    arm, _ = _seed(store)
    hits = search_chunks(store, "ET4010", "cờ trạng thái")
    assert _ids(hits) == [arm[0].chunk_id, arm[1].chunk_id]
    assert all(hit.index == "primary" for hit in hits)
    assert hits[0].score > hits[1].score > 0
    assert search_chunks(store, "SSH1110", "cờ trạng thái") == []
    assert search_chunks(store, "ET4010", "lệnh ADDS", limit=1)[0].chunk.chunk_id == arm[1].chunk_id


def test_search_is_the_same_on_every_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FTS5 chỉ chọn ứng viên, điểm luôn là BM25 tự tính: ba loại kho cho cùng kết quả (ADR-006 mục 4)."""
    queries = ("cờ", "Thanh ghi", "co trang thai", "ADDS R0")
    results = []
    for kind in STORE_KINDS:
        with monkeypatch.context() as patch:
            for opened in (
                open_store(kind, tmp_path / kind, patch) if kind == "memory" else _sqlite(kind, tmp_path, patch)
            ):
                _seed(opened)
                results.append([search_chunks(opened, "ET4010", query) for query in queries])
    assert results[0] == results[1] == results[2]
    assert all(results[0])


def _sqlite(kind: str, tmp_path: Path, patch: pytest.MonkeyPatch) -> Iterator[AppStore]:
    folder = tmp_path / kind
    folder.mkdir()
    yield from open_store(kind, folder, patch)


def test_search_normalises_query_like_the_index(store: AppStore) -> None:
    arm, _ = _seed(store)
    nfd = unicodedata.normalize("NFD", "THANH GHI trạng thái")
    assert _ids(search_chunks(store, "ET4010", nfd)) == _ids(search_chunks(store, "ET4010", "thanh ghi trạng thái"))
    assert _ids(search_chunks(store, "ET4010", nfd))[0] == arm[0].chunk_id


def test_search_keeps_diacritics_in_the_primary_index(store: AppStore) -> None:
    add_document(store, ARM_DOC)
    chunks = ingest_confirmed_text(
        store,
        confirmed(ARM_DOC, [["1.1 Mục một", "Con mà đi qua cầu."], ["1.2 Mục hai", "Con ma đi qua cầu."]]),
    )
    hits = search_chunks(store, "ET4010", "ma")
    assert _ids(hits) == [chunks[1].chunk_id]
    assert hits[0].index == "primary"


def test_no_diacritics_index_is_only_a_fallback(store: AppStore) -> None:
    arm, _ = _seed(store)
    hits = search_chunks(store, "ET4010", "co trang thai")
    assert hits and all(hit.index == "no_diacritics" for hit in hits)
    assert _ids(hits)[0] == arm[0].chunk_id
    assert search_chunks(store, "ET4010", "xyzw qqq") == []


def test_search_filters_by_topic_and_handles_empty_queries(store: AppStore) -> None:
    _seed(store)
    assert _ids(search_chunks(store, "ET4010", "thanh ghi", topic_keys=[ARM_TOPIC])) == _ids(
        search_chunks(store, "ET4010", "thanh ghi")
    )
    assert search_chunks(store, "ET4010", "thanh ghi", topic_keys=["x.y.z"]) == []
    assert search_chunks(store, "ET4010", "!!! ...") == []
    assert search_chunks(store, "IT3070", "thanh") == []


def test_search_breaks_ties_by_chunk_id(store: AppStore) -> None:
    add_document(store, ARM_DOC)
    chunks = ingest_confirmed_text(
        store,
        confirmed(ARM_DOC, [["1.1 Một", "Bộ đệm vòng."], ["1.2 Hai", "Bộ đệm vòng."], ["1.3 Ba", "Bộ đệm vòng."]]),
    )
    hits = search_chunks(store, "ET4010", "đệm")
    assert len({hit.score for hit in hits}) == 1
    assert _ids(hits) == sorted(chunk.chunk_id for chunk in chunks)


def test_bm25_and_fts_query_helpers() -> None:
    assert bm25_scores(["a"], {}) == {}
    scores = bm25_scores(["a", "b"], {"d1": ["a", "a", "c"], "d2": ["b"], "d3": ["c"]})
    assert set(scores) == {"d1", "d2"}
    assert scores == bm25_scores(["b", "a"], {"d3": ["c"], "d2": ["b"], "d1": ["a", "a", "c"]})
    assert bm25_scores(["z"], {"d1": []}) == {}
    assert fts_query(["cờ", "z"]) == '"cờ" OR "z"'


# --------------------------------------------------------------------------- #
# Cặp khái niệm (ADR-007 mục 1, I3)
# --------------------------------------------------------------------------- #

PHIL_QUOTE = "Vật chất là phạm trù triết học dùng để chỉ thực tại khách quan."


def test_user_pair_is_confirmed_and_copies_labels_from_the_chunk(store: AppStore) -> None:
    _, phil = _seed(store)
    pair = create_concept_pair(store, chunk_id=phil[0].chunk_id, term="Vật chất", definition_quote=PHIL_QUOTE, now=NOW)
    assert pair.status is ConceptPairStatus.CONFIRMED
    assert pair.proposed_by == "user"
    assert (pair.subject_code, pair.chapter_code) == ("SSH1110", "ch1")
    assert pair.confirmed_at == pair.created_at == NOW
    assert pair.term_spans == [(0, 8)]
    assert store.get_concept_pair(pair.pair_id) == pair


def test_pair_quote_in_nfd_with_extra_spaces_is_accepted_and_stored_nfc(store: AppStore) -> None:
    _, phil = _seed(store)
    quote = unicodedata.normalize("NFD", "Vật  chất là\nphạm trù triết học")
    pair = create_concept_pair(store, chunk_id=phil[0].chunk_id, term="vật chất", definition_quote=quote, now=NOW)
    assert pair.definition_quote == "Vật chất là phạm trù triết học"
    assert pair.term_spans == [(0, 8)]


@pytest.mark.parametrize(
    "quote",
    [
        "Vật chất là phạm trù kinh tế học.",  # không có trong chunk
        "VẬT CHẤT LÀ PHẠM TRÙ TRIẾT HỌC",  # khác hoa thường
        "Vat chat la pham tru triet hoc",  # bỏ dấu
    ],
)
def test_pair_quote_not_verbatim_is_rejected_and_not_stored(store: AppStore, quote: str) -> None:
    _, phil = _seed(store)
    with pytest.raises(QuoteMismatchError):
        create_concept_pair(store, chunk_id=phil[0].chunk_id, term="Vật chất", definition_quote=quote, now=NOW)
    assert store.list_concept_pairs("SSH1110") == []


def test_pair_quote_from_another_chunk_is_rejected(store: AppStore) -> None:
    arm, _ = _seed(store)
    with pytest.raises(QuoteMismatchError):
        create_concept_pair(store, chunk_id=arm[0].chunk_id, term="Vật chất", definition_quote=PHIL_QUOTE, now=NOW)


def test_pair_needs_an_existing_chunk(store: AppStore) -> None:
    _seed(store)
    with pytest.raises(RecordNotFoundError):
        create_concept_pair(store, chunk_id="chk_missing", term="x", definition_quote=PHIL_QUOTE, now=NOW)
    orphan = ConceptPair(
        chunk_id="chk_missing",
        subject_code="SSH1110",
        chapter_code="ch1",
        term="x",
        definition_quote=PHIL_QUOTE,
        status=ConceptPairStatus.PROPOSED,
        proposed_by="llm",
        created_at=NOW,
    )
    with pytest.raises(MissingReferenceError):
        store.add_concept_pair(orphan)


def _proposed(store: AppStore, chunk: KnowledgeChunk, term: str, quote: str) -> ConceptPair:
    """Cặp do LLM đề xuất (bước propose thuộc nhiệm vụ sau); ghi thẳng vào kho để thử confirm/reject."""
    return store.add_concept_pair(
        ConceptPair(
            chunk_id=chunk.chunk_id,
            subject_code=chunk.subject_code,
            chapter_code=chunk.chapter_code,
            term=term,
            definition_quote=quote,
            status=ConceptPairStatus.PROPOSED,
            proposed_by="llm",
            mapping_confidence=0.8,
            created_at=NOW,
        )
    )


def test_confirm_with_edits_then_reject(store: AppStore) -> None:
    _, phil = _seed(store)
    pair = _proposed(store, phil[0], "Vật chất", PHIL_QUOTE)
    later = NOW + timedelta(minutes=5)
    confirmed_pair = confirm_concept_pair(
        store,
        pair.pair_id,
        expected_version=1,
        now=later,
        term="Ý thức",
        definition_quote="Ý thức là sự phản ánh năng động, sáng tạo thế giới khách quan",
    )
    assert confirmed_pair.status is ConceptPairStatus.CONFIRMED
    assert confirmed_pair.version == 2 and confirmed_pair.confirmed_at == later
    assert confirmed_pair.term == "Ý thức" and confirmed_pair.term_spans == [(0, 6)]
    assert (confirmed_pair.proposed_by, confirmed_pair.mapping_confidence) == ("llm", 0.8)
    assert store.get_concept_pair(pair.pair_id) == confirmed_pair

    rejected = reject_concept_pair(store, pair.pair_id, expected_version=2)
    assert rejected.status is ConceptPairStatus.REJECTED
    assert rejected.confirmed_at is None and rejected.version == 3


def test_confirm_without_edits_keeps_the_proposal(store: AppStore) -> None:
    _, phil = _seed(store)
    pair = _proposed(store, phil[0], "Vật chất", PHIL_QUOTE)
    confirmed_pair = confirm_concept_pair(store, pair.pair_id, expected_version=1, now=NOW)
    assert (confirmed_pair.term, confirmed_pair.definition_quote) == (pair.term, pair.definition_quote)


def test_confirm_rechecks_the_quote(store: AppStore) -> None:
    _, phil = _seed(store)
    pair = _proposed(store, phil[0], "Vật chất", PHIL_QUOTE)
    with pytest.raises(QuoteMismatchError):
        confirm_concept_pair(store, pair.pair_id, expected_version=1, now=NOW, definition_quote="không có trong chunk")
    assert store.get_concept_pair(pair.pair_id) == pair


def test_pair_version_conflicts_and_missing(store: AppStore) -> None:
    _, phil = _seed(store)
    pair = _proposed(store, phil[0], "Vật chất", PHIL_QUOTE)
    with pytest.raises(VersionConflictError):
        confirm_concept_pair(store, pair.pair_id, expected_version=2, now=NOW)
    with pytest.raises(VersionConflictError):
        reject_concept_pair(store, pair.pair_id, expected_version=2)
    with pytest.raises(RecordNotFoundError):
        reject_concept_pair(store, "cp_missing", expected_version=1)
    with pytest.raises(RecordNotFoundError):
        store.update_concept_pair(pair.model_copy(update={"pair_id": "cp_missing"}), 1)


def test_list_pairs_filters_and_sorts(store: AppStore) -> None:
    arm, phil = _seed(store)
    b = _proposed(store, phil[0], "Ý thức", "Ý thức là sự phản ánh năng động")
    a = _proposed(store, phil[0], "Vật chất", PHIL_QUOTE)
    arm_pair = _proposed(store, arm[0], "CPSR", "Thanh ghi CPSR chứa các cờ trạng thái")
    assert store.list_concept_pairs("SSH1110") == [a, b]
    assert store.list_concept_pairs("ET4010") == [arm_pair]
    assert store.list_concept_pairs("SSH1110", chapter_code="ch2") == []
    confirm_concept_pair(store, a.pair_id, expected_version=1, now=NOW)
    assert [p.pair_id for p in store.list_concept_pairs("SSH1110", status=ConceptPairStatus.CONFIRMED)] == [a.pair_id]
    assert store.list_concept_pairs("SSH1110", "ch1", ConceptPairStatus.PROPOSED) == [b]


def test_concept_pair_model_rules() -> None:
    base = {
        "chunk_id": "chk_a",
        "subject_code": "SSH1110",
        "chapter_code": "ch1",
        "term": "Vật chất",
        "definition_quote": PHIL_QUOTE,
        "proposed_by": "user",
        "created_at": NOW,
    }
    with pytest.raises(ValidationError):
        ConceptPair(**base, status=ConceptPairStatus.CONFIRMED)  # thiếu confirmed_at
    with pytest.raises(ValidationError):
        ConceptPair(**base, status=ConceptPairStatus.PROPOSED, confirmed_at=NOW)
    with pytest.raises(ValidationError):
        ConceptPair(**{**base, "definition_quote": "a      b       c"}, status=ConceptPairStatus.PROPOSED)
    spanned = ConceptPair(**base, status=ConceptPairStatus.PROPOSED, term_spans=[(5, 9)])
    assert spanned.term_spans == [(0, 8)]  # luôn do code tính lại


def test_find_term_spans() -> None:
    assert find_term_spans("tin", "tin học và tinh thần, Tin.") == [(0, 3), (22, 25)]
    assert find_term_spans("", "abc") == []
    assert find_term_spans("C++", "Ngôn ngữ C++ và C") == [(9, 12)]


# --------------------------------------------------------------------------- #
# VaultCitationProvider (ADR-006 mục 6)
# --------------------------------------------------------------------------- #


def _slot(topic_key: str) -> BlueprintSlot:
    return SLOT.model_copy(update={"topic_key": topic_key})


def test_citation_provider_returns_only_chunks_of_the_blueprint_subject(store: AppStore) -> None:
    add_document(store, ARM_DOC)
    add_document(store, PHIL_DOC)
    topic = "shared.topic.key"
    other_subject = ingest_confirmed_text(
        store, confirmed(PHIL_DOC, PHILOSOPHY_PAGES, subject_code="SSH1110", chapter_code="ch1", topic_keys=[topic])
    )
    # Chunk môn khác ở trang 1 (nhỏ hơn) và đã xác nhận nhãn: vẫn không bao giờ được trả cho môn ET4010.
    update_chunk_labels(store, other_subject[0].chunk_id, expected_version=1)
    provider = VaultCitationProvider(store)
    assert provider.citation_for("ET4010", _slot(topic)) is None

    own = ingest_confirmed_text(store, confirmed(ARM_DOC, ARM_PAGES, topic_keys=[topic], first_page=7))
    assert provider.citation_for("ET4010", _slot(topic)) is None  # nhãn còn "proposed"
    for chunk in own:
        update_chunk_labels(store, chunk.chunk_id, expected_version=1)
    citation = provider.citation_for("ET4010", _slot(topic))
    assert citation == own[0].to_citation()
    assert citation is not None and citation.page_number == 7
    assert provider.citation_for("SSH1110", _slot(topic)) == other_subject[0].to_citation()
    assert provider.citation_for("IT3070", _slot(topic)) is None


def test_citation_provider_needs_theory_and_matching_topic(store: AppStore) -> None:
    add_document(store, ARM_DOC)
    chunks = ingest_confirmed_text(store, confirmed(ARM_DOC, ARM_PAGES, topic_keys=[ARM_TOPIC]))
    update_chunk_labels(store, chunks[0].chunk_id, expected_version=1, category=ChunkCategory.PRACTICE)
    update_chunk_labels(store, chunks[1].chunk_id, expected_version=1)
    provider = VaultCitationProvider(store)
    assert provider.citation_for("ET4010", _slot("mp.arm.other.topic")) is None
    citation = provider.citation_for("ET4010", _slot(ARM_TOPIC))
    assert citation is not None and citation.chunk_id == chunks[1].chunk_id


def test_citation_provider_breaks_ties_by_page_then_chunk_id(store: AppStore) -> None:
    add_document(store, ARM_DOC)
    for chunk_id, page in (("chk_c", 3), ("chk_b", 3), ("chk_a", 4), ("chk_0", 5)):
        store.add_chunk(
            KnowledgeChunk(
                chunk_id=chunk_id,
                doc_id=ARM_DOC,
                subject_code="ET4010",
                chapter_code="ch3",
                page_number=page,
                raw_text=f"Định nghĩa {chunk_id} ở trang {page}.",
                verbatim_quote=f"Định nghĩa {chunk_id}",
                category="THEORY",
                topic_keys=[ARM_TOPIC],
                token_count=5,
                label_status="confirmed",
            ),
            "",
        )
    citation = VaultCitationProvider(store).citation_for("ET4010", _slot(ARM_TOPIC))
    assert citation is not None
    assert (citation.page_number, citation.chunk_id) == (3, "chk_b")


def test_duplicate_pair_id_is_a_storage_error(store: AppStore) -> None:
    _, phil = _seed(store)
    pair = _proposed(store, phil[0], "Vật chất", PHIL_QUOTE)
    with pytest.raises(StorageError):
        store.add_concept_pair(pair)


def test_memory_store_has_no_fts5() -> None:
    from holocron_core.infrastructure.memory_store import IngestionStore

    with pytest.raises(VaultSearchUnavailableError):
        IngestionStore().match_chunk_ids("ET4010", '"x"')
