"""Lọc trích dẫn theo môn qua luồng twin (SPECIFICATION §5.8, §12.3; ADR-006 mục 6).

`generate_twin_batch` truyền `blueprint.subject_code` cho `CitationProvider.citation_for`; với `VaultCitationProvider`
thật trên kho in-memory, chunk của môn khác không bao giờ thành trích dẫn của twin, kể cả khi `topic_keys` khớp.
Văn bản chunk là mẫu tự viết cho test (`tests/vault_support.py`).
"""

from holocron_core.domain.repositories import AppStore
from holocron_core.domain.schemas import BlueprintSlot, ExamBlueprint, KnowledgeChunk
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.template_library import InMemoryTemplateRegistry
from holocron_core.services.twin_batch import (
    CitationProvider,
    NoCitationProvider,
    TwinBatch,
    TwinBatchRequest,
    UnconfiguredTemplateAuthor,
    generate_twin_batch,
)
from holocron_core.services.vault import VaultCitationProvider, ingest_confirmed_text, update_chunk_labels
from tests.vault_support import ARM_PAGES, PHILOSOPHY_PAGES, add_document, confirmed, document_id

BLUEPRINT_SUBJECT = "IT3070"
OTHER_SUBJECT = "SSH1110"
BASE_SEED = 20260419


def _blueprint(slot: BlueprintSlot) -> ExamBlueprint:
    return ExamBlueprint(
        blueprint_id="bp_vault_subject",
        source_exam_doc_id="doc_exam_1",
        subject_code=BLUEPRINT_SUBJECT,
        total_questions=1,
        slots=[slot.model_copy(update={"slot_index": 1})],
    )


def _confirmed_theory(store: AppStore, seed: str, pages: list[list[str]], subject: str, topic: str) -> KnowledgeChunk:
    """Nhập một tài liệu THEORY của `subject` gắn `topic`, xác nhận nhãn mọi chunk; trả chunk đầu tiên."""
    doc_id = document_id(seed)
    add_document(store, doc_id)
    chunks = ingest_confirmed_text(store, confirmed(doc_id, pages, subject_code=subject, topic_keys=[topic]))
    for chunk in chunks:
        update_chunk_labels(store, chunk.chunk_id, expected_version=1)
    first = store.get_chunk(chunks[0].chunk_id)
    assert first is not None and first.label_status == "confirmed"
    return first


def _batch(store: AppStore, slot: BlueprintSlot) -> TwinBatch:
    return generate_twin_batch(
        _blueprint(slot),
        TwinBatchRequest(count=1, base_seed=BASE_SEED),
        author=UnconfiguredTemplateAuthor(),
        registry=InMemoryTemplateRegistry(),
        citations=VaultCitationProvider(store),
    )


def test_vault_citation_provider_satisfies_the_protocol() -> None:
    """Gán vào biến kiểu `CitationProvider`: mypy kiểm chữ ký `citation_for(subject_code, slot)` của cả hai lớp."""
    providers: list[CitationProvider] = [VaultCitationProvider(IngestionStore()), NoCitationProvider()]
    assert len(providers) == 2


def test_twin_never_cites_a_chunk_of_another_subject(rr_slot: BlueprintSlot) -> None:
    store = IngestionStore()
    _confirmed_theory(store, "b", PHILOSOPHY_PAGES, OTHER_SUBJECT, rr_slot.topic_key)

    without_own = _batch(store, rr_slot)
    assert without_own.twins == []
    assert [(t.slot_index, t.topic_key) for t in without_own.review_tickets] == [(1, rr_slot.topic_key)]
    assert "trích dẫn" in without_own.review_tickets[0].reason

    own = _confirmed_theory(store, "a", ARM_PAGES, BLUEPRINT_SUBJECT, rr_slot.topic_key)
    with_own = _batch(store, rr_slot)
    assert with_own.review_tickets == []
    assert len(with_own.twins) == 1
    assert with_own.twins[0].citation == own.to_citation()
    assert with_own.twins[0].citation.doc_id == document_id("a")
