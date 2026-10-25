"""Bất biến mà repository Vault tự giữ, không phụ thuộc service (ba loại kho: in-memory, SQLite có/không FTS5).

- I3 của cặp khái niệm được kiểm trong giao dịch ghi khi gọi thẳng repository, và kiểm lại khi nạp từ SQLite.
- Nhập chunk theo lô là nguyên tử.
- `linked_chunk_ids` theo §12.1, tính lại khi nhập và khi sửa nhãn.
- Đổi `chapter_code` của chunk thì cặp khái niệm của nó đổi theo trong cùng giao dịch.
- Người sửa và thời điểm được lưu kèm bản ghi (hàng Repudiation của §13).

Văn bản mẫu tự viết cho test (không phải tài liệu môn học có bản quyền).
"""

import logging
import sqlite3
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from holocron_core.domain.repositories import AppStore, MissingReferenceError, VersionConflictError
from holocron_core.domain.schemas import ChunkCategory, KnowledgeChunk
from holocron_core.domain.vault import (
    ConceptPair,
    ConceptPairStatus,
    QuoteMismatchError,
    VaultEdit,
    link_practice_chunks,
)
from holocron_core.infrastructure.storage import SQLiteStore
from holocron_core.infrastructure.storage.connection import connect
from holocron_core.services.vault import (
    confirm_concept_pair,
    create_concept_pair,
    ingest_confirmed_text,
    reject_concept_pair,
    update_chunk_labels,
)
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
EXTRA_DOC = document_id("c")
PHIL_QUOTE = "Vật chất là phạm trù triết học dùng để chỉ thực tại khách quan."
NOT_IN_CHUNK = "Vật chất là phạm trù kinh tế học."
TOPIC = "mp.arm.cpsr.flags"


@pytest.fixture(params=STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppStore]:
    yield from open_store(str(request.param), tmp_path, monkeypatch)


def _phil_chunk(store: AppStore) -> KnowledgeChunk:
    add_document(store, PHIL_DOC)
    chunks = ingest_confirmed_text(
        store, confirmed(PHIL_DOC, PHILOSOPHY_PAGES, subject_code="SSH1110", chapter_code="ch1")
    )
    return chunks[0]


def _pair(chunk: KnowledgeChunk, quote: str, *, pair_id: str = "cp_test_0001") -> ConceptPair:
    return ConceptPair(
        pair_id=pair_id,
        chunk_id=chunk.chunk_id,
        subject_code=chunk.subject_code,
        chapter_code=chunk.chapter_code,
        term="Vật chất",
        definition_quote=quote,
        status=ConceptPairStatus.PROPOSED,
        proposed_by="llm",
        created_at=NOW,
    )


# --------------------------------------------------------------------------- #
# I3 ở repository
# --------------------------------------------------------------------------- #


def test_repository_refuses_a_pair_whose_quote_is_not_in_the_chunk(store: AppStore) -> None:
    chunk = _phil_chunk(store)
    with pytest.raises(QuoteMismatchError):
        store.add_concept_pair(_pair(chunk, NOT_IN_CHUNK))
    assert store.get_concept_pair("cp_test_0001") is None
    assert store.list_concept_pairs("SSH1110") == []


def test_repository_refuses_a_quote_taken_from_another_chunk(store: AppStore) -> None:
    phil = _phil_chunk(store)
    add_document(store, ARM_DOC)
    arm = ingest_confirmed_text(store, confirmed(ARM_DOC, ARM_PAGES))
    with pytest.raises(QuoteMismatchError):
        store.add_concept_pair(_pair(phil, PHIL_QUOTE).model_copy(update={"chunk_id": arm[0].chunk_id}))
    assert store.list_concept_pairs("SSH1110") == []


def test_repository_refuses_an_update_whose_quote_is_not_in_the_chunk(store: AppStore) -> None:
    chunk = _phil_chunk(store)
    stored = store.add_concept_pair(_pair(chunk, PHIL_QUOTE))
    tampered = stored.model_copy(update={"definition_quote": NOT_IN_CHUNK})
    with pytest.raises(QuoteMismatchError):
        store.update_concept_pair(tampered, 1)
    assert store.get_concept_pair(stored.pair_id) == stored


def test_repository_update_checks_the_chunk_of_the_stored_pair(store: AppStore) -> None:
    """Trích dẫn khớp một chunk khác vẫn bị loại: kho kiểm theo chunk mà cặp đang lưu trỏ tới."""
    phil = _phil_chunk(store)
    add_document(store, ARM_DOC)
    arm = ingest_confirmed_text(store, confirmed(ARM_DOC, ARM_PAGES))
    stored = store.add_concept_pair(_pair(phil, PHIL_QUOTE))
    moved = stored.model_copy(update={"chunk_id": arm[0].chunk_id, "definition_quote": arm[0].verbatim_quote})
    with pytest.raises(QuoteMismatchError):
        store.update_concept_pair(moved, 1)
    assert store.get_concept_pair(stored.pair_id) == stored


def test_repository_still_reports_missing_chunk_and_stale_version(store: AppStore) -> None:
    chunk = _phil_chunk(store)
    with pytest.raises(MissingReferenceError):
        store.add_concept_pair(_pair(chunk, PHIL_QUOTE).model_copy(update={"chunk_id": "chk_missing"}))
    stored = store.add_concept_pair(_pair(chunk, PHIL_QUOTE))
    with pytest.raises(VersionConflictError):
        store.update_concept_pair(stored.model_copy(update={"definition_quote": NOT_IN_CHUNK}), 7)


def _sqlite_with_pair(tmp_path: Path) -> tuple[SQLiteStore, ConceptPair, Path]:
    path = tmp_path / "tampered.sqlite3"
    opened = SQLiteStore.open(path)
    chunk = _phil_chunk(opened)
    pair = create_concept_pair(opened, chunk_id=chunk.chunk_id, term="Vật chất", definition_quote=PHIL_QUOTE, now=NOW)
    return opened, pair, path


def test_hand_edited_sqlite_row_is_never_returned(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    opened, pair, path = _sqlite_with_pair(tmp_path)
    try:
        raw = connect(path)
        try:
            raw.execute("UPDATE concept_pairs SET definition_quote = ? WHERE pair_id = ?", (NOT_IN_CHUNK, pair.pair_id))
        finally:
            raw.close()
        with caplog.at_level(logging.WARNING, logger="holocron_core.infrastructure.storage.sqlite_store"):
            assert opened.get_concept_pair(pair.pair_id) is None
            assert opened.list_concept_pairs("SSH1110") == []
            assert opened.list_concept_pairs("SSH1110", status=ConceptPairStatus.CONFIRMED) == []
        assert pair.pair_id in caplog.text and "I3" in caplog.text
        assert NOT_IN_CHUNK not in caplog.text  # log không chép nội dung tài liệu
    finally:
        opened.close()


def test_hand_edited_row_pointing_to_another_chunk_is_never_returned(tmp_path: Path) -> None:
    opened, pair, path = _sqlite_with_pair(tmp_path)
    try:
        add_document(opened, ARM_DOC)
        arm = ingest_confirmed_text(opened, confirmed(ARM_DOC, ARM_PAGES))
        raw = connect(path)
        try:
            raw.execute("UPDATE concept_pairs SET chunk_id = ? WHERE pair_id = ?", (arm[0].chunk_id, pair.pair_id))
        finally:
            raw.close()
        assert opened.get_concept_pair(pair.pair_id) is None
        assert opened.list_concept_pairs("SSH1110") == []
    finally:
        opened.close()


def test_valid_sqlite_rows_still_load_next_to_a_tampered_one(tmp_path: Path) -> None:
    opened, pair, path = _sqlite_with_pair(tmp_path)
    try:
        chunk = opened.get_chunk(pair.chunk_id)
        assert chunk is not None
        kept = create_concept_pair(
            opened, chunk_id=chunk.chunk_id, term="Ý thức", definition_quote="Ý thức là sự phản ánh năng động", now=NOW
        )
        raw = connect(path)
        try:
            raw.execute("UPDATE concept_pairs SET definition_quote = ? WHERE pair_id = ?", (NOT_IN_CHUNK, pair.pair_id))
        finally:
            raw.close()
        assert opened.list_concept_pairs("SSH1110") == [kept]
    finally:
        opened.close()


# --------------------------------------------------------------------------- #
# Nhập theo lô, nguyên tử
# --------------------------------------------------------------------------- #


def test_batch_insert_is_atomic(store: AppStore) -> None:
    add_document(store, ARM_DOC)
    chunks = [
        KnowledgeChunk(
            chunk_id=f"chk_batch_{index}",
            doc_id=doc_id,
            subject_code="ET4010",
            chapter_code="ch3",
            page_number=1,
            raw_text="Cờ Z được đặt khi kết quả phép toán bằng không.",
            verbatim_quote="Cờ Z được đặt",
            category=ChunkCategory.THEORY,
            token_count=10,
        )
        for index, doc_id in enumerate((ARM_DOC, ARM_DOC, document_id("z")))
    ]
    with pytest.raises(MissingReferenceError):
        store.add_chunks(chunks)
    assert store.list_chunks("ET4010") == []
    assert store.get_chunk("chk_batch_0") is None
    assert store.add_chunks(chunks[:2]) == chunks[:2]


def test_batch_insert_rolls_back_on_a_late_invalid_chunk(store: AppStore) -> None:
    add_document(store, ARM_DOC)
    base = ingest_confirmed_text(store, confirmed(ARM_DOC, ARM_PAGES))
    fresh = base[0].model_copy(update={"chunk_id": "chk_fresh"})
    embedded = base[0].model_copy(update={"chunk_id": "chk_embedded", "embedding_model": "e5"})
    with pytest.raises(ValueError, match="embedding_model"):
        store.add_chunks([fresh, embedded])
    assert store.get_chunk("chk_fresh") is None
    assert [chunk.chunk_id for chunk in store.list_chunks("ET4010")] == [chunk.chunk_id for chunk in base]


def test_ingest_failure_leaves_nothing_behind(store: AppStore) -> None:
    with pytest.raises(MissingReferenceError):
        ingest_confirmed_text(store, confirmed(ARM_DOC, ARM_PAGES))
    add_document(store, ARM_DOC)
    assert store.list_chunks("ET4010") == []
    assert len(ingest_confirmed_text(store, confirmed(ARM_DOC, ARM_PAGES))) == 2


def test_batch_insert_keeps_existing_chunks(store: AppStore) -> None:
    add_document(store, ARM_DOC)
    first = ingest_confirmed_text(store, confirmed(ARM_DOC, ARM_PAGES))
    edited = update_chunk_labels(store, first[0].chunk_id, expected_version=1, chapter_code="ch4")
    again = store.add_chunks(first)
    assert again == [edited, first[1]]


# --------------------------------------------------------------------------- #
# linked_chunk_ids (§12.1)
# --------------------------------------------------------------------------- #


def _chunk(
    chunk_id: str, page: int, category: ChunkCategory, topics: list[str], doc_id: str = ARM_DOC
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        subject_code="ET4010",
        chapter_code="ch3",
        page_number=page,
        raw_text=f"Đoạn {chunk_id} ở trang {page}.",
        verbatim_quote=f"Đoạn {chunk_id}",
        category=category,
        topic_keys=topics,
        token_count=4,
    )


def test_link_rule_picks_the_nearest_theory_chunk_per_topic() -> None:
    other = "mp.arm.alu.add"
    chunks = [
        _chunk("chk_t1", 1, ChunkCategory.THEORY, [TOPIC]),
        _chunk("chk_t5", 5, ChunkCategory.THEORY, [TOPIC, other]),
        _chunk("chk_t9", 9, ChunkCategory.THEORY, [other]),
        _chunk("chk_tb", 7, ChunkCategory.THEORY, [TOPIC], doc_id=EXTRA_DOC),
        _chunk("chk_p6", 6, ChunkCategory.PRACTICE, [TOPIC, other]),
        _chunk("chk_p3", 3, ChunkCategory.PRACTICE, [TOPIC]),
        _chunk("chk_px", 2, ChunkCategory.PRACTICE, ["mp.arm.none.here"]),
        _chunk("chk_pn", 2, ChunkCategory.PRACTICE, []),
    ]
    links = link_practice_chunks(chunks)
    assert links["chk_p6"] == ["chk_t5"]  # trang 5 gần hơn trang 1 và trang 9; tài liệu khác xếp sau
    assert links["chk_p3"] == ["chk_t1"]  # trang 1 và trang 5 cách đều: trang nhỏ hơn
    assert links["chk_px"] == links["chk_pn"] == []
    assert all(links[chunk_id] == [] for chunk_id in ("chk_t1", "chk_t5", "chk_t9", "chk_tb"))


def test_link_rule_uses_page_ranges_and_other_documents_as_last_resort() -> None:
    wide = _chunk("chk_wide", 2, ChunkCategory.THEORY, [TOPIC]).model_copy(update={"page_end": 8})
    near = _chunk("chk_near", 9, ChunkCategory.THEORY, [TOPIC])
    practice = _chunk("chk_p", 8, ChunkCategory.PRACTICE, [TOPIC])
    assert link_practice_chunks([wide, near, practice])["chk_p"] == ["chk_wide"]
    elsewhere = _chunk("chk_else", 8, ChunkCategory.THEORY, [TOPIC], doc_id=EXTRA_DOC)
    lonely = _chunk("chk_lonely", 3, ChunkCategory.PRACTICE, [TOPIC])
    assert link_practice_chunks([elsewhere, lonely])["chk_lonely"] == ["chk_else"]


def test_links_are_computed_on_ingest(store: AppStore) -> None:
    add_document(store, ARM_DOC)
    add_document(store, EXTRA_DOC)
    theory = ingest_confirmed_text(store, confirmed(ARM_DOC, ARM_PAGES, topic_keys=[TOPIC]))
    practice = ingest_confirmed_text(
        store,
        confirmed(
            EXTRA_DOC,
            [["Bài 1", "Cho R1 = 1 và R2 = 2, tính cờ sau lệnh ADDS."]],
            category="PRACTICE",
            topic_keys=[TOPIC],
        ),
    )
    assert practice[0].linked_chunk_ids == [theory[0].chunk_id]
    assert store.get_chunk(practice[0].chunk_id) == practice[0]
    assert all(
        chunk.linked_chunk_ids == [] for chunk in store.list_chunks("ET4010") if chunk.category is ChunkCategory.THEORY
    )


def test_links_are_recomputed_when_category_or_topics_change(store: AppStore) -> None:
    add_document(store, ARM_DOC)
    chunks = ingest_confirmed_text(store, confirmed(ARM_DOC, ARM_PAGES, topic_keys=[TOPIC]))
    first, second = chunks
    assert first.linked_chunk_ids == second.linked_chunk_ids == []

    practice = update_chunk_labels(store, second.chunk_id, expected_version=1, category=ChunkCategory.PRACTICE)
    assert practice.linked_chunk_ids == [first.chunk_id]
    assert store.get_chunk(second.chunk_id) == practice

    unrelated = update_chunk_labels(store, second.chunk_id, expected_version=2, topic_keys=["mp.arm.alu.add"])
    assert unrelated.linked_chunk_ids == []

    linked_again = update_chunk_labels(store, second.chunk_id, expected_version=3, topic_keys=[TOPIC])
    assert linked_again.linked_chunk_ids == [first.chunk_id]
    # Đổi chunk lý thuyết thành bài tập: chunk bài tập kia mất liên kết, version của nó không đổi.
    update_chunk_labels(store, first.chunk_id, expected_version=1, category=ChunkCategory.PRACTICE)
    after = store.get_chunk(second.chunk_id)
    assert after is not None and after.linked_chunk_ids == [] and after.version == 4


# --------------------------------------------------------------------------- #
# chapter_code của cặp khái niệm theo chunk
# --------------------------------------------------------------------------- #


def test_chapter_change_moves_the_pairs_of_the_chunk(store: AppStore) -> None:
    chunk = _phil_chunk(store)
    pair = create_concept_pair(store, chunk_id=chunk.chunk_id, term="Vật chất", definition_quote=PHIL_QUOTE, now=NOW)
    update_chunk_labels(store, chunk.chunk_id, expected_version=1, chapter_code="ch2.1")
    moved = store.get_concept_pair(pair.pair_id)
    assert moved is not None
    assert (moved.chapter_code, moved.version) == ("ch2.1", 2)
    assert store.list_concept_pairs("SSH1110", chapter_code="ch1") == []
    assert store.list_concept_pairs("SSH1110", chapter_code="ch2.1") == [moved]
    # Sửa nhãn mà không đổi chương thì cặp giữ nguyên.
    update_chunk_labels(store, chunk.chunk_id, expected_version=2, topic_keys=["phil.matter.definition"])
    assert store.get_concept_pair(pair.pair_id) == moved
    # Người giữ version cũ của cặp nhận 409 thay vì ghi đè chương mới.
    with pytest.raises(VersionConflictError):
        confirm_concept_pair(store, pair.pair_id, expected_version=1, now=NOW)


def test_failed_chapter_change_does_not_move_pairs(store: AppStore) -> None:
    chunk = _phil_chunk(store)
    pair = create_concept_pair(store, chunk_id=chunk.chunk_id, term="Vật chất", definition_quote=PHIL_QUOTE, now=NOW)
    with pytest.raises(VersionConflictError):
        update_chunk_labels(store, chunk.chunk_id, expected_version=9, chapter_code="ch2")
    assert store.get_concept_pair(pair.pair_id) == pair


# --------------------------------------------------------------------------- #
# Người sửa (Repudiation, §13)
# --------------------------------------------------------------------------- #


def test_editor_is_stored_with_label_and_pair_changes(store: AppStore) -> None:
    chunk = _phil_chunk(store)
    assert store.get_last_edit("chunk", chunk.chunk_id) is None
    later = NOW + timedelta(minutes=5)
    update_chunk_labels(store, chunk.chunk_id, expected_version=1, edit=VaultEdit(editor_id="u_label", at=NOW))
    assert store.get_last_edit("chunk", chunk.chunk_id) == VaultEdit(editor_id="u_label", at=NOW)

    pair = create_concept_pair(
        store, chunk_id=chunk.chunk_id, term="Vật chất", definition_quote=PHIL_QUOTE, now=NOW, editor_id="u_create"
    )
    assert store.get_last_edit("concept_pair", pair.pair_id) == VaultEdit(editor_id="u_create", at=NOW)
    confirm_concept_pair(store, pair.pair_id, expected_version=1, now=later, editor_id="u_confirm")
    assert store.get_last_edit("concept_pair", pair.pair_id) == VaultEdit(editor_id="u_confirm", at=later)
    reject_concept_pair(store, pair.pair_id, expected_version=2, edit=VaultEdit(editor_id="u_reject", at=later))
    assert store.get_last_edit("concept_pair", pair.pair_id) == VaultEdit(editor_id="u_reject", at=later)

    # Ghi không kèm người sửa (ví dụ cặp do LLM đề xuất) không xoá người sửa trước đó.
    update_chunk_labels(store, chunk.chunk_id, expected_version=2)
    assert store.get_last_edit("chunk", chunk.chunk_id) == VaultEdit(editor_id="u_label", at=NOW)
    assert store.get_last_edit("chunk", "chk_missing") is None
    assert store.get_last_edit("concept_pair", "cp_missing") is None


def test_failed_edit_does_not_record_an_editor(store: AppStore) -> None:
    chunk = _phil_chunk(store)
    with pytest.raises(VersionConflictError):
        update_chunk_labels(store, chunk.chunk_id, expected_version=4, edit=VaultEdit(editor_id="u", at=NOW))
    assert store.get_last_edit("chunk", chunk.chunk_id) is None
    with pytest.raises(QuoteMismatchError):
        store.add_concept_pair(_pair(chunk, NOT_IN_CHUNK), VaultEdit(editor_id="u", at=NOW))
    assert store.get_last_edit("concept_pair", "cp_test_0001") is None


def test_sqlite_schema_keeps_editor_and_time_together(tmp_path: Path) -> None:
    opened = SQLiteStore.open(tmp_path / "check.sqlite3")
    try:
        chunk = _phil_chunk(opened)
    finally:
        opened.close()
    raw = connect(tmp_path / "check.sqlite3")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            raw.execute("UPDATE chunks SET edited_by = 'u' WHERE chunk_id = ?", (chunk.chunk_id,))
    finally:
        raw.close()
