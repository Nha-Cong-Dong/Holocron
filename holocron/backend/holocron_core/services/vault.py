"""Knowledge Vault (SPECIFICATION §5.8, §6.6, §12; ADR-006, ADR-007): nhập chunk, liệt kê, tìm kiếm BM25, sửa nhãn,
cặp khái niệm và nguồn trích dẫn theo môn.

- **Nhập:** chỉ từ văn bản người dùng đã xác nhận (`ConfirmedDocumentText`), cắt tất định ở `vault_chunking`, ghi
  nguyên tử theo lô (`VaultRepository.add_chunks`).
- **Tìm kiếm (ADR-006 mục 4–5):** truy vấn và văn bản chunk qua cùng bộ tách từ (`domain.search_text`). Tập ứng viên
  lấy từ FTS5 khi kho có (`match_chunk_ids`), ngược lại so token trực tiếp; điểm luôn là BM25 tự tính trên cùng bộ
  token, nên kho SQLite có hay không có FTS5 và kho in-memory cho cùng kết quả: FTS5 chỉ lọc ứng viên và nhận đúng
  token Python (kể cả token đã bỏ dấu của chỉ mục phụ), nên không loại sót chunk nào mà BM25 tự cài chấm điểm > 0. Chỉ mục chính (giữ dấu) chạy trước;
  chỉ mục không dấu chỉ chạy khi chỉ mục chính không trả kết quả nào. Hoà điểm thì xếp theo `chunk_id`.
- **Cặp khái niệm (ADR-007 mục 1):** `definition_quote` PHẢI là chuỗi con của `raw_text` của chunk sau NFC và gộp
  khoảng trắng (I3); sai thì `QuoteMismatchError`, không lưu và không sửa hộ. Repository kiểm lại trong giao dịch
  ghi, nên bất biến đúng cả khi gọi thẳng repository.
- **Người sửa:** mọi thao tác của người dùng nhận `VaultEdit` (người sửa, thời điểm) và repository lưu kèm bản ghi
  (hàng Repudiation của §13).
- **`VaultCitationProvider` (ADR-006 mục 6):** chỉ trả trích dẫn của chunk cùng `subject_code` với blueprint.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Literal

from ..domain.repositories import RecordNotFoundError, SearchIndex, VaultRepository
from ..domain.schemas import BlueprintSlot, ChunkCategory, Citation, KnowledgeChunk
from ..domain.search_text import no_diacritics_tokens, search_tokens
from ..domain.vault import (
    ConceptPair,
    ConceptPairStatus,
    ConfirmedDocumentText,
    QuoteMismatchError,
    SearchHit,
    VaultEdit,
)
from ..domain.vault import check_definition_quote as check_quote_text
from .vault_chunking import build_chunks

__all__ = [
    "QuoteMismatchError",
    "VaultCitationProvider",
    "bm25_scores",
    "check_definition_quote",
    "confirm_concept_pair",
    "create_concept_pair",
    "fts_query",
    "ingest_confirmed_text",
    "list_chunks",
    "reject_concept_pair",
    "search_chunks",
    "update_chunk_labels",
]

BM25_K1 = 1.2
BM25_B = 0.75
SCORE_DECIMALS = 6
CATEGORY_TAGS = frozenset({"#theory", "#practice_example"})

LabelStatus = Literal["proposed", "confirmed"]

_TOKENIZERS: tuple[tuple[SearchIndex, Callable[[str], list[str]]], ...] = (
    ("primary", search_tokens),
    ("no_diacritics", no_diacritics_tokens),
)


# --------------------------------------------------------------------------- #
# Nhập và liệt kê chunk
# --------------------------------------------------------------------------- #


def ingest_confirmed_text(vault: VaultRepository, document: ConfirmedDocumentText) -> list[KnowledgeChunk]:
    """Cắt văn bản đã xác nhận rồi ghi mọi chunk cùng chỉ mục tìm kiếm trong một giao dịch; trả bản đang lưu của
    từng chunk theo thứ tự cắt (kèm `linked_chunk_ids` đã tính lại).

    Nguyên tử: lỗi ở bất kỳ chunk nào thì không chunk nào được ghi. Nhập lại cùng văn bản là idempotent: chunk đã có
    (cùng `chunk_id`) giữ nguyên bản đang lưu, kể cả nhãn đã sửa. Tài liệu không có ⇒ `MissingReferenceError` từ kho.
    """
    return vault.add_chunks(build_chunks(document))


def _matches_topics(chunk: KnowledgeChunk, topic_keys: Sequence[str]) -> bool:
    """Không lọc khi `topic_keys` rỗng; ngược lại chunk có ít nhất một khoá khớp (§6.6)."""
    return not topic_keys or not set(topic_keys).isdisjoint(chunk.topic_keys)


def list_chunks(
    vault: VaultRepository,
    subject_code: str,
    *,
    chapter_code: str | None = None,
    topic_keys: Sequence[str] = (),
    label_status: LabelStatus | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[int, list[KnowledgeChunk]]:
    """`(tổng số chunk khớp bộ lọc, trang kết quả)`, sắp theo `(doc_id, page_number, chunk_id)`."""
    found = [
        chunk
        for chunk in vault.list_chunks(subject_code, chapter_code)
        if _matches_topics(chunk, topic_keys) and (label_status is None or chunk.label_status == label_status)
    ]
    return len(found), found[offset : offset + limit]


def update_chunk_labels(
    vault: VaultRepository,
    chunk_id: str,
    *,
    expected_version: int,
    chapter_code: str | None = None,
    category: ChunkCategory | None = None,
    topic_keys: Sequence[str] | None = None,
    edit: VaultEdit | None = None,
) -> KnowledgeChunk:
    """Sửa nhãn (field nào `None` thì giữ nguyên) và đánh dấu `label_status = "confirmed"` (§6.6).

    Đổi `category` thì tag `#theory`/`#practice_example` được dựng lại theo loại mới. Văn bản không đổi. Cùng giao
    dịch với nhãn, kho cập nhật `chapter_code` của cặp khái niệm thuộc chunk, `linked_chunk_ids` của môn và `edit`.
    """
    current = vault.get_chunk(chunk_id)
    if current is None:
        raise RecordNotFoundError("chunk", chunk_id)
    updated = KnowledgeChunk.model_validate(
        {
            **current.model_dump(),
            "chapter_code": current.chapter_code if chapter_code is None else chapter_code,
            "category": current.category if category is None else category,
            "tags": [tag for tag in current.tags if tag not in CATEGORY_TAGS],
            "topic_keys": current.topic_keys if topic_keys is None else list(topic_keys),
            "label_status": "confirmed",
        }
    )
    return vault.update_chunk_labels(updated, expected_version, edit)


# --------------------------------------------------------------------------- #
# Tìm kiếm BM25
# --------------------------------------------------------------------------- #


def _unique(tokens: list[str]) -> list[str]:
    return list(dict.fromkeys(tokens))


def fts_query(tokens: Sequence[str]) -> str:
    """Biểu thức FTS5 `"t1" OR "t2" …`; token chỉ gồm chữ và số nên đặt trong ngoặc kép là an toàn."""
    return " OR ".join(f'"{token}"' for token in tokens)


def bm25_scores(query: Sequence[str], documents: dict[str, list[str]]) -> dict[str, float]:
    """Điểm BM25 (k1 = 1.2, b = 0.75, IDF `ln(1 + (N - n + 0.5) / (n + 0.5))`) của mọi tài liệu có ít nhất một token
    của truy vấn; làm tròn 6 chữ số để thứ tự tất định."""
    total = len(documents)
    if total == 0:
        return {}
    lengths = {doc_id: len(tokens) for doc_id, tokens in documents.items()}
    average = sum(lengths.values()) / total or 1.0
    frequencies = {doc_id: Counter(tokens) for doc_id, tokens in documents.items()}
    scores: dict[str, float] = {}
    for term in sorted(set(query)):
        containing = [doc_id for doc_id, counts in frequencies.items() if counts[term] > 0]
        if not containing:
            continue
        idf = math.log(1.0 + (total - len(containing) + 0.5) / (len(containing) + 0.5))
        for doc_id in containing:
            tf = frequencies[doc_id][term]
            norm = BM25_K1 * (1.0 - BM25_B + BM25_B * lengths[doc_id] / average)
            scores[doc_id] = scores.get(doc_id, 0.0) + idf * tf * (BM25_K1 + 1.0) / (tf + norm)
    return {doc_id: round(score, SCORE_DECIMALS) for doc_id, score in scores.items()}


def _candidates(
    vault: VaultRepository, subject_code: str, query: list[str], index: SearchIndex, documents: dict[str, list[str]]
) -> set[str]:
    if vault.vault_search_available:
        return set(vault.match_chunk_ids(subject_code, fts_query(query), index))
    wanted = set(query)
    return {chunk_id for chunk_id, tokens in documents.items() if not wanted.isdisjoint(tokens)}


def search_chunks(
    vault: VaultRepository, subject_code: str, query: str, *, topic_keys: Sequence[str] = (), limit: int = 20
) -> list[SearchHit]:
    """Tìm chunk của môn `subject_code` theo BM25; chỉ mục không dấu chỉ dùng khi chỉ mục chính rỗng.

    IDF và độ dài trung bình tính trên mọi chunk của môn; lọc `topic_keys` áp dụng sau khi chấm điểm.
    """
    chunks = {chunk.chunk_id: chunk for chunk in vault.list_chunks(subject_code)}
    for index, tokenize in _TOKENIZERS:
        terms = _unique(tokenize(query))
        if not terms:
            return []
        documents = {chunk_id: tokenize(chunk.raw_text) for chunk_id, chunk in chunks.items()}
        candidates = _candidates(vault, subject_code, terms, index, documents)
        scores = bm25_scores(terms, documents)
        hits = [
            SearchHit(chunk=chunks[chunk_id], score=score, index=index)
            for chunk_id, score in scores.items()
            if chunk_id in candidates and score > 0 and _matches_topics(chunks[chunk_id], topic_keys)
        ]
        if hits:
            hits.sort(key=lambda hit: (-hit.score, hit.chunk.chunk_id))
            return hits[:limit]
    return []


# --------------------------------------------------------------------------- #
# Cặp khái niệm (ADR-007 mục 1)
# --------------------------------------------------------------------------- #


def _chunk_of(vault: VaultRepository, chunk_id: str) -> KnowledgeChunk:
    chunk = vault.get_chunk(chunk_id)
    if chunk is None:
        raise RecordNotFoundError("chunk", chunk_id)
    return chunk


def check_definition_quote(chunk: KnowledgeChunk, definition_quote: str) -> None:
    """I3: `definition_quote` là chuỗi con của `chunk.raw_text` sau NFC và gộp khoảng trắng; sai ⇒ `QuoteMismatchError`.

    Kiểm sớm để báo lỗi trước khi dựng model; repository vẫn kiểm lại trong giao dịch ghi.
    """
    check_quote_text(chunk.chunk_id, chunk.raw_text, definition_quote)


def create_concept_pair(
    vault: VaultRepository,
    *,
    chunk_id: str,
    term: str,
    definition_quote: str,
    now: datetime,
    editor_id: str | None = None,
) -> ConceptPair:
    """Người dùng tự tạo cặp bằng cách bôi đoạn trong chunk: `proposed_by = "user"`, `status = "confirmed"` (§6.6).

    `editor_id` (nếu có) được lưu làm người sửa gần nhất, thời điểm `now`.
    """
    chunk = _chunk_of(vault, chunk_id)
    check_definition_quote(chunk, definition_quote)
    pair = ConceptPair(
        chunk_id=chunk.chunk_id,
        subject_code=chunk.subject_code,
        chapter_code=chunk.chapter_code,
        term=term,
        definition_quote=definition_quote,
        status=ConceptPairStatus.CONFIRMED,
        proposed_by="user",
        created_at=now,
        confirmed_at=now,
    )
    return vault.add_concept_pair(pair, None if editor_id is None else VaultEdit(editor_id=editor_id, at=now))


def _pair_of(vault: VaultRepository, pair_id: str) -> ConceptPair:
    pair = vault.get_concept_pair(pair_id)
    if pair is None:
        raise RecordNotFoundError("cặp khái niệm", pair_id)
    return pair


def confirm_concept_pair(
    vault: VaultRepository,
    pair_id: str,
    *,
    expected_version: int,
    now: datetime,
    term: str | None = None,
    definition_quote: str | None = None,
    editor_id: str | None = None,
) -> ConceptPair:
    """Sửa (nếu có) rồi xác nhận; trích dẫn luôn được kiểm lại với văn bản chunk (I3). `editor_id` như ở tạo cặp."""
    pair = _pair_of(vault, pair_id)
    quote = pair.definition_quote if definition_quote is None else definition_quote
    check_definition_quote(_chunk_of(vault, pair.chunk_id), quote)
    updated = ConceptPair.model_validate(
        {
            **pair.model_dump(),
            "term": pair.term if term is None else term,
            "definition_quote": quote,
            "status": ConceptPairStatus.CONFIRMED,
            "confirmed_at": now,
        }
    )
    edit = None if editor_id is None else VaultEdit(editor_id=editor_id, at=now)
    return vault.update_concept_pair(updated, expected_version, edit)


def reject_concept_pair(
    vault: VaultRepository, pair_id: str, *, expected_version: int, edit: VaultEdit | None = None
) -> ConceptPair:
    """Từ chối: cặp không còn vào nhóm ứng viên sinh câu (ADR-007 mục 1)."""
    pair = _pair_of(vault, pair_id)
    updated = pair.model_copy(update={"status": ConceptPairStatus.REJECTED, "confirmed_at": None})
    return vault.update_concept_pair(updated, expected_version, edit)


# --------------------------------------------------------------------------- #
# Nguồn trích dẫn theo môn (ADR-006 mục 6)
# --------------------------------------------------------------------------- #


class VaultCitationProvider:
    """Trích dẫn cho slot tính toán: chunk `THEORY` đã xác nhận nhãn, cùng `subject_code` với blueprint, có
    `topic_keys` chứa `topic_key` của slot; nhiều chunk thì chọn trang nhỏ nhất rồi `chunk_id` nhỏ nhất.

    Không có chunk phù hợp ⇒ `None` (slot vào `review_tickets`). Chunk của môn khác không bao giờ được trả.
    """

    def __init__(self, vault: VaultRepository) -> None:
        self._vault = vault

    def citation_for(self, subject_code: str, slot: BlueprintSlot) -> Citation | None:
        candidates = [
            chunk
            for chunk in self._vault.list_chunks(subject_code)
            if chunk.subject_code == subject_code
            and chunk.category is ChunkCategory.THEORY
            and chunk.label_status == "confirmed"
            and slot.topic_key in chunk.topic_keys
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda chunk: (chunk.page_number, chunk.chunk_id)).to_citation()
