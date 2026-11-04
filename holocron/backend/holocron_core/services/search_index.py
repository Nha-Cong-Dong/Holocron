"""Tra cứu khối offline (SPECIFICATION §6.10, §12.3; ADR-010 mục 18). Deterministic Core: không LLM, không mạng.

Luồng một truy vấn (`search_blocks`), cùng kết quả ở mọi đường (FTS5, FTS5 + trigram, Python):

1. `parse_query`: NFC, bỏ khoảng trắng đầu/cuối, gấp dấu (`domain.search_text.fold_text`), tách token. Mọi ký tự đặc biệt của
   FTS5 (`"`, `*`, `NEAR`, `-`, `:`) chỉ là chữ thường hoặc dấu câu bị bỏ: token đi vào FTS5 luôn nằm trong ngoặc kép.
2. Với MỖI token: tập khối khớp chặt (token gấp dấu bằng token của khối). FTS5 lấy từ `blocks_fts`; đường lùi Python quét
   mọi khối đủ điều kiện của tài liệu trong phạm vi.
3. Token KHÔNG có khối khớp chặt nào và đủ dài (`fuzzy_min_len`) mới được khớp mờ (khoảng cách sửa ≤ `fuzzy_max_edit`
   trên token gấp dấu). Token ≥ `fuzzy_trigram_min_len` ký tự: ứng viên lấy từ `blocks_fts_trigram` (sai một thao tác vẫn
   còn ít nhất một trigram chung); token ngắn hơn: trigram không bắt được ⇒ quét mọi khối trong phạm vi.
4. Chấm điểm bằng cùng một hàm Python trên khối ứng viên (`_match_block`): hạng khớp `exact` > `folded` > `fuzzy`,
   token kỹ thuật (chữ lẫn số, chữ hoa ≥ 2 ký tự, mã `0x…`) khớp `exact` được cộng thêm nên xếp trên mọi kết quả mờ.
   Thứ tự kết quả (§6.10): `match_kind` (exact > folded > fuzzy) → điểm giảm dần → (tài liệu, trang, `reading_order`,
   `block_id`). Điểm chỉ phân hạng trong cùng `match_kind`.
5. Khối figure/table/formula/code còn được tìm qua chữ kề (`domain.vault.block_contexts`: caption kề, đoạn văn quanh,
   cùng trang theo `reading_order`); khớp chỉ ở chữ kề thì hit không có `match_spans` (chuỗi đích nằm ngoài khối).
6. Khoảng khớp trả theo chữ GỐC (đơn vị UTF-16 code unit), ánh xạ qua bảng vị trí của `fold_text`.

Chỉ tập khối đủ điều kiện của tài liệu ĐÃ ĐÓNG (`status = "confirmed"` hoặc Vault đã có chunk, §5.15) và không phải `past_exam`.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from pydantic import ConfigDict, Field

from ..domain.blocks import Block, BlockType
from ..domain.documents import PageExtraction, PageSummary
from ..domain.repositories import AppStore, ImportedDocument, RecordNotFoundError
from ..domain.schemas import SourceKind, StrictModel
from ..domain.search_text import TOKEN_RE, FoldedText, fold_text, normalize_search_text, utf16_offsets
from ..domain.vault import (
    BLOCK_CONTEXT_MAX_CHARS,
    BLOCK_CONTEXT_TYPES,
    BLOCK_SEARCH_EXCLUDED_TYPES,
    BlockSearchHit,
    BlockSearchPage,
    MatchSpan,
    SearchField,
    block_contexts,
    block_search_fields,
    section_path_of,
)

__all__ = [
    "DEFAULT_SETTINGS",
    "BlockIndexSource",
    "QueryError",
    "QueryToken",
    "SearchSettings",
    "parse_query",
    "search_blocks",
]

MatchKind = Literal["exact", "folded", "fuzzy"]
_TIER_RANK: dict[MatchKind, int] = {"exact": 0, "folded": 1, "fuzzy": 2}


class SearchSettings(StrictModel):
    """Mẫu hình thức và ngưỡng của tra cứu khối; tổng quát, không theo môn (ADR-010 mục 18)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_query_chars: int = Field(default=200, ge=1)
    max_query_tokens: int = Field(default=32, ge=1)
    max_results: int = Field(default=1000, ge=1)
    max_page_size: int = Field(default=50, ge=1)  # trần `limit` và số giá trị lọc lặp của endpoint
    context_max_chars: int = Field(default=BLOCK_CONTEXT_MAX_CHARS, ge=1)  # mỗi đoạn chữ kề (xem `block_contexts`)
    technical_patterns: tuple[str, ...] = (
        r"^(?=\w*\d)(?=\w*[^\W\d_])[^\W_]+$",  # chữ lẫn số: R13, int32, mp3
        r"^[A-Z]{2,}$",  # chữ hoa ≥ 2 ký tự: CPSR, MOV
        r"^0[xX][0-9a-fA-F]+$",  # mã thập lục phân: 0x1F
    )
    fuzzy_min_len: int = Field(default=2, ge=1)  # token ngắn hơn chỉ khớp chặt
    fuzzy_max_edit: int = Field(default=1, ge=1, le=1)  # đảm bảo trigram ở `fuzzy_trigram_min_len` luôn còn ứng viên
    fuzzy_trigram_min_len: int = Field(default=7, ge=7)  # < giá trị này: quét thay vì trigram (xem docstring module)
    min_token_coverage: float = Field(default=0.5, gt=0.0, le=1.0)
    exact_weight: float = 3.0
    folded_weight: float = 2.0
    fuzzy_weight: float = 1.0
    technical_bonus: float = 3.0
    density_weight: float = 0.5  # nhỏ hơn chênh lệch giữa hai hạng khớp


DEFAULT_SETTINGS = SearchSettings()


class QueryError(ValueError):
    """Truy vấn không hợp lệ (rỗng sau chuẩn hoá, quá dài, quá nhiều token); router trả 422."""


@runtime_checkable
class BlockIndexSource(Protocol):
    """Kho có chỉ mục FTS riêng cho khối (SQLite). Kho khác ⇒ đường lùi Python qua `list_eligible_blocks`."""

    def match_block_index(
        self, subject_code: str, terms: Sequence[str], *, trigram: bool
    ) -> list[tuple[str, str]] | None: ...

    def load_eligible_blocks(self, document_id: str, block_ids: Sequence[str]) -> list[Block]: ...


@dataclass(frozen=True)
class QueryToken:
    folded: str
    kept: str
    technical: bool


def parse_query(query: str, settings: SearchSettings = DEFAULT_SETTINGS) -> list[QueryToken]:
    """Token duy nhất (giữ thứ tự) của truy vấn. Rỗng sau khi bỏ khoảng trắng, quá dài hoặc quá nhiều token ⇒ `QueryError`.
    Truy vấn không có token chữ/số nào (ví dụ chỉ `*`) hợp lệ và cho danh sách rỗng."""
    text = unicodedata.normalize("NFC", query).strip()
    if not text:
        raise QueryError("truy vấn rỗng")
    if len(text) > settings.max_query_chars:
        raise QueryError(f"truy vấn tối đa {settings.max_query_chars} ký tự")
    folded_text = fold_text(text)
    folded = TOKEN_RE.findall(folded_text.folded)
    kept = [normalize_search_text(token) for token in TOKEN_RE.findall(folded_text.kept)]
    raw = TOKEN_RE.findall(text)
    if len(kept) != len(folded):
        kept = list(folded)
    if len(folded) > settings.max_query_tokens:
        raise QueryError(f"truy vấn tối đa {settings.max_query_tokens} token")
    patterns = [re.compile(pattern) for pattern in settings.technical_patterns]
    aligned = len(raw) == len(folded)
    seen: set[str] = set()
    tokens: list[QueryToken] = []
    for index, token in enumerate(folded):
        if token in seen:
            continue
        seen.add(token)
        technical = aligned and any(pattern.match(raw[index]) for pattern in patterns)
        tokens.append(QueryToken(folded=token, kept=kept[index], technical=technical))
    return tokens


def _within_edit(a: str, b: str, max_edit: int) -> bool:
    """Khoảng cách Damerau–Levenshtein (hoán vị kề tính một thao tác) ≤ `max_edit`."""
    if abs(len(a) - len(b)) > max_edit:
        return False
    previous2: list[int] = []
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                current[j] = min(current[j], previous2[j - 2] + 1)
        previous2, previous = previous, current
    return previous[len(b)] <= max_edit


@dataclass(frozen=True)
class _Tok:
    text: str
    start: int  # code point trong chữ gốc
    end: int


@dataclass(frozen=True)
class _Analyzed:
    field: SearchField
    folded: FoldedText
    kept_tokens: tuple[_Tok, ...]
    folded_tokens: tuple[_Tok, ...]


def _tokens(text: str, starts: tuple[int, ...], ends: tuple[int, ...], *, tone: bool) -> tuple[_Tok, ...]:
    found: list[_Tok] = []
    for match in TOKEN_RE.finditer(text):
        token = normalize_search_text(match.group(0)) if tone else match.group(0)
        found.append(_Tok(token, starts[match.start()], ends[match.end() - 1]))
    return tuple(found)


def _analyze(field: SearchField) -> _Analyzed:
    folded = fold_text(field.text)
    return _Analyzed(
        field=field,
        folded=folded,
        kept_tokens=_tokens(folded.kept, folded.kept_starts, folded.kept_ends, tone=True),
        folded_tokens=_tokens(folded.folded, folded.folded_starts, folded.folded_ends, tone=False),
    )


@dataclass(frozen=True)
class _Match:
    kind: MatchKind
    score: float
    spans: list[MatchSpan]


def _field_tier(
    analyzed: _Analyzed, token: QueryToken, *, fuzzy: bool, settings: SearchSettings
) -> tuple[MatchKind, list[_Tok]] | None:
    exact = [t for t in analyzed.kept_tokens if t.text == token.kept]
    if exact:
        return "exact", exact
    folded = [t for t in analyzed.folded_tokens if t.text == token.folded]
    if folded:
        return "folded", folded
    if fuzzy:
        near = [t for t in analyzed.folded_tokens if _within_edit(token.folded, t.text, settings.fuzzy_max_edit)]
        if near:
            return "fuzzy", near
    return None


def _spans(analyzed: _Analyzed, hits: list[_Tok]) -> list[MatchSpan]:
    field = analyzed.field
    target = field.target
    if target == "context":
        return []  # chữ kề không nằm trong khối: không có khoảng để tô
    offsets = utf16_offsets(analyzed.field.text)
    merged: list[list[int]] = []
    for tok in sorted(hits, key=lambda t: (t.start, t.end)):
        start, end = offsets[tok.start], offsets[tok.end]
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [
        MatchSpan(
            target=target,
            row=field.row,
            cell_index=field.cell_index,
            label_index=field.label_index,
            start=start,
            end=end,
        )
        for start, end in merged
        if start < end
    ]


def _match_block(
    analyzed: Sequence[_Analyzed], tokens: Sequence[QueryToken], fuzzy: Sequence[bool], settings: SearchSettings
) -> _Match | None:
    """Điểm, kiểu khớp và khoảng khớp của một khối; `None` khi không đủ `min_token_coverage` token."""
    weights: dict[MatchKind, float] = {
        "exact": settings.exact_weight,
        "folded": settings.folded_weight,
        "fuzzy": settings.fuzzy_weight,
    }
    total = 0.0
    matched = 0
    occurrences = 0
    kinds: list[MatchKind] = []
    spans: list[MatchSpan] = []
    for token, allow_fuzzy in zip(tokens, fuzzy, strict=True):
        per_field = [
            (item, tier)
            for item in analyzed
            if (tier := _field_tier(item, token, fuzzy=allow_fuzzy, settings=settings)) is not None
        ]
        if not per_field:
            continue
        best = min(_TIER_RANK[tier[0]] for _, tier in per_field)
        kind = next(name for name, rank in _TIER_RANK.items() if rank == best)
        matched += 1
        kinds.append(kind)
        total += weights[kind] + (settings.technical_bonus if token.technical and kind == "exact" else 0.0)
        for item, (field_kind, hit_tokens) in per_field:
            if field_kind == kind:
                occurrences += len(hit_tokens)
                spans.extend(_spans(item, hit_tokens))
    if matched == 0 or matched < math.ceil(settings.min_token_coverage * len(tokens)):
        return None
    block_tokens = sum(len(item.folded_tokens) for item in analyzed)
    total += settings.density_weight * occurrences / (occurrences + block_tokens)
    overall: MatchKind = "fuzzy" if "fuzzy" in kinds else ("exact" if all(k == "exact" for k in kinds) else "folded")
    order = {"text": 0, "cell": 1, "label": 2}
    spans.sort(key=lambda s: (order[s.target], s.row or 0, s.cell_index or 0, s.label_index or 0, s.start))
    return _Match(kind=overall, score=round(total, 6), spans=spans)


def _trigrams(token: str) -> list[str]:
    return sorted({token[i : i + 3] for i in range(len(token) - 2)})


def _closed_documents(
    store: AppStore, subject_code: str, document_ids: Sequence[str], chapter_codes: Sequence[str]
) -> list[ImportedDocument]:
    """Tài liệu đã đóng của môn (§5.15), không phải `past_exam`, khớp bộ lọc nguồn và chương."""
    found: list[ImportedDocument] = []
    for document in store.list_imported_documents(subject_code):
        doc_id = document.meta.doc_id
        if document.meta.source_kind == SourceKind.PAST_EXAM:
            continue
        if document_ids and doc_id not in document_ids:
            continue
        if chapter_codes and document.chapter_code not in chapter_codes:
            continue
        if document.status == "confirmed" or store.has_document_chunks(doc_id):
            found.append(document)
    return found


def _page_has_text(store: AppStore, document_id: str, page_number: int) -> bool:
    page = store.get_page(document_id, page_number, PageExtraction)
    return page is not None and any(line.text.strip() for line in page.lines)


def _page_counts(store: AppStore, documents: Sequence[ImportedDocument]) -> tuple[int, int, int]:
    """`(layout_failed_pages, pages_without_blocks, pages_unconfirmable_with_blocks)` của tài liệu trong phạm vi."""
    failed = without = unconfirmable = 0
    for document in documents:
        doc_id = document.meta.doc_id
        summaries: list[PageSummary] = store.list_page_summaries(doc_id)
        with_blocks = {block.page_number for block in store.list_blocks(doc_id)}
        for summary in summaries:
            if "LAYOUT_FAILED" in summary.review_reasons:
                failed += 1
            elif summary.dropped or summary.status == "failed":
                continue
            elif summary.confirmed and summary.page_number not in with_blocks:
                without += int(_page_has_text(store, doc_id, summary.page_number))
            elif not summary.confirmed and summary.page_number in with_blocks:
                unconfirmable += int(not _page_has_text(store, doc_id, summary.page_number))
    return failed, without, unconfirmable


def search_blocks(
    store: AppStore,
    *,
    subject_code: str,
    query: str,
    document_ids: Sequence[str] = (),
    chapter_codes: Sequence[str] = (),
    types: Sequence[BlockType] = (),
    offset: int = 0,
    limit: int = 20,
    settings: SearchSettings = DEFAULT_SETTINGS,
) -> BlockSearchPage:
    """Tra cứu khối đủ điều kiện của môn. Môn không có ⇒ `RecordNotFoundError`; truy vấn sai ⇒ `QueryError`."""
    if store.get_subject(subject_code) is None:
        raise RecordNotFoundError("môn", subject_code)
    tokens = parse_query(query, settings)
    wanted_types = set(types) - BLOCK_SEARCH_EXCLUDED_TYPES if types else None
    documents = _closed_documents(store, subject_code, document_ids, chapter_codes)
    failed, without, unconfirmable = _page_counts(store, documents)
    by_doc = {document.meta.doc_id: document for document in documents}

    blocks, fuzzy, engine, contexts = _gather(store, subject_code, tokens, list(by_doc), settings)
    scored: list[tuple[Block, _Match]] = []
    for block in blocks:
        if wanted_types is not None and block.type not in wanted_types:
            continue
        fields = block_search_fields(block, contexts.get(block.block_id, ()))
        match = _match_block([_analyze(f) for f in fields], tokens, fuzzy, settings)
        if match is not None:
            scored.append((block, match))
    scored.sort(
        key=lambda pair: (
            _TIER_RANK[pair[1].kind],
            -pair[1].score,
            pair[0].document_id,
            pair[0].page_number,
            pair[0].reading_order,
            pair[0].block_id,
        )
    )
    total_found = len(scored)
    visible = scored[: settings.max_results]
    sections = {
        doc_id: store.list_sections(doc_id) for doc_id in {b.document_id for b, _ in visible[offset : offset + limit]}
    }
    hits = [
        BlockSearchHit(
            block=block,
            document_title=by_doc[block.document_id].meta.title,
            chapter_code=by_doc[block.document_id].chapter_code or "",
            section_path=section_path_of(block.block_id, sections[block.document_id]),
            match_kind=match.kind,
            match_spans=match.spans,
            score=match.score,
        )
        for block, match in visible[offset : offset + limit]
    ]
    return BlockSearchPage(
        total=len(visible),
        total_capped=total_found > settings.max_results,
        hits=hits,
        engine=engine,
        layout_failed_pages=failed,
        pages_without_blocks=without,
        pages_unconfirmable_with_blocks=unconfirmable,
    )


def _gather(
    store: AppStore,
    subject_code: str,
    tokens: Sequence[QueryToken],
    doc_ids: Sequence[str],
    settings: SearchSettings,
) -> tuple[list[Block], list[bool], Literal["fts5", "fts5_trigram", "python"], dict[str, list[SearchField]]]:
    """Khối ứng viên, cờ "được khớp mờ" của từng token, đường đã dùng và chữ kề của khối ứng viên."""
    if not tokens or not doc_ids:
        return [], [False] * len(tokens), "python", {}
    scope = set(doc_ids)
    index = store if isinstance(store, BlockIndexSource) else None
    if index is not None:
        strict: list[set[tuple[str, str]]] = []
        for token in tokens:
            pairs = index.match_block_index(subject_code, [token.folded], trigram=False)
            if pairs is None:
                index = None
                break
            strict.append({pair for pair in pairs if pair[0] in scope})
        if index is not None:
            return _gather_indexed(store, index, subject_code, tokens, scope, strict, settings)
    return _gather_scan(store, tokens, doc_ids, settings)


def _gather_scan(
    store: AppStore, tokens: Sequence[QueryToken], doc_ids: Sequence[str], settings: SearchSettings
) -> tuple[list[Block], list[bool], Literal["python"], dict[str, list[SearchField]]]:
    blocks: list[Block] = []
    contexts: dict[str, list[SearchField]] = {}
    for doc_id in doc_ids:
        doc_blocks = store.list_eligible_blocks(doc_id)
        blocks.extend(doc_blocks)
        contexts.update(block_contexts(doc_blocks, settings.context_max_chars))
    present: set[str] = set()
    for block in blocks:
        for item in (_analyze(f) for f in block_search_fields(block, contexts.get(block.block_id, ()))):
            present.update(t.text for t in item.folded_tokens)
    fuzzy = [token.folded not in present and len(token.folded) >= settings.fuzzy_min_len for token in tokens]
    return blocks, fuzzy, "python", contexts


def _gather_indexed(
    store: AppStore,
    index: BlockIndexSource,
    subject_code: str,
    tokens: Sequence[QueryToken],
    scope: set[str],
    strict: Sequence[set[tuple[str, str]]],
    settings: SearchSettings,
) -> tuple[list[Block], list[bool], Literal["fts5", "fts5_trigram", "python"], dict[str, list[SearchField]]]:
    fuzzy = [
        not found and len(token.folded) >= settings.fuzzy_min_len for token, found in zip(tokens, strict, strict=True)
    ]
    candidates: set[tuple[str, str]] = set().union(*strict)
    scan = False
    used_trigram = False
    for token, needs in zip(tokens, fuzzy, strict=True):
        if not needs:
            continue
        pairs = None
        if len(token.folded) >= settings.fuzzy_trigram_min_len:
            pairs = index.match_block_index(subject_code, _trigrams(token.folded), trigram=True)
        if pairs is None:
            scan = True  # token ngắn, hoặc kho không có bảng trigram: đường lùi quét
        else:
            used_trigram = True
            candidates.update(pair for pair in pairs if pair[0] in scope)
    by_doc: dict[str, list[str]] = {}
    for doc_id, block_id in sorted(candidates):
        by_doc.setdefault(doc_id, []).append(block_id)
    if scan:
        blocks = [block for doc_id in sorted(scope) for block in store.list_eligible_blocks(doc_id)]
    else:
        blocks = [block for doc_id, ids in sorted(by_doc.items()) for block in index.load_eligible_blocks(doc_id, ids)]
    contexts: dict[str, list[SearchField]] = {}
    for doc_id in sorted({block.document_id for block in blocks if block.type in BLOCK_CONTEXT_TYPES}):
        contexts.update(block_contexts(store.list_eligible_blocks(doc_id), settings.context_max_chars))
    return blocks, fuzzy, ("fts5_trigram" if used_trigram else "fts5"), contexts
