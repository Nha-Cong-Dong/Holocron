"""Tra cứu khối offline: xếp hạng theo kiểu khớp, chữ kề của figure/table/formula/code, và các ca giết đột biến
(vòng sửa X10; SPECIFICATION §6.10, ADR-010 mục 18). Dữ liệu tự tạo, không mạng."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.blocks import Block, TableCell, WordRef
from holocron_core.domain.documents import PageExtraction
from holocron_core.domain.ingestion import UploadedDocument
from holocron_core.domain.schemas import SourceKind
from holocron_core.domain.search_text import fold_text, utf16_offsets
from holocron_core.domain.vault import BLOCK_CONTEXT_MAX_CHARS, block_contexts
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.storage import SQLiteStore
from holocron_core.routers.api import app, get_store
from holocron_core.services import search_index
from holocron_core.services.search_index import QueryError, SearchSettings, parse_query, search_blocks
from tests.test_block_store import NOW, layout, make_block, seed
from tests.test_offline_search import (
    ENGINES,
    SUBJECT,
    Engine,
    Scoped,
    _open_engines,
    confirm_document,
    new_doc,
)

TIER = {"exact": 0, "folded": 1, "fuzzy": 2}


@pytest.fixture(scope="module")
def engines(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Engine]]:
    yield from _open_engines(tmp_path_factory, with_corpus=False)


@pytest.fixture(params=ENGINES)
def eng(request: pytest.FixtureRequest, engines: dict[str, Engine]) -> Engine:
    return engines[str(request.param)]


def build_doc(
    engine: Engine,
    *,
    texts: Sequence[str] = (),
    source_kind: SourceKind = SourceKind.LECTURE_SLIDES,
    close_doc: bool = True,
) -> tuple[Scoped, str, list[Block]]:
    """Một tài liệu một trang, mỗi phần tử của `texts` là một đoạn văn."""
    store, doc = engine.store, new_doc(engine)
    seed(store, doc, pages=1, source_kind=source_kind)
    blocks = [make_block(doc, 1, order, text=text, key=f"{text}|{order}") for order, text in enumerate(texts)]
    store.save_document_layout(doc, layout({1: blocks}, doc_id=doc))
    store.refresh_eligible_blocks(doc)
    if close_doc:
        confirm_document(store, doc, title="Tài liệu", chapter_code="ch2")
    return Scoped(store, doc), doc, blocks


def put_blocks(engine: Engine, builder: Any, *, n_pages: int = 1) -> tuple[Scoped, str, dict[str, Block]]:
    """`builder(doc) -> dict[key, Block]`; khối đặt theo `block.page_number`."""
    store, doc = engine.store, new_doc(engine)
    seed(store, doc, pages=n_pages)
    named: dict[str, Block] = builder(doc)
    pages: dict[int, list[Block]] = {}
    for block in named.values():
        pages.setdefault(block.page_number, []).append(block)
    store.save_document_layout(doc, layout(pages, doc_id=doc))
    store.refresh_eligible_blocks(doc)
    confirm_document(store, doc, title="Tài liệu", chapter_code="ch2")
    return Scoped(store, doc), doc, named


# --------------------------------------------------------------------------- #
# Xếp hạng: match_kind → score → document_id, page_number, reading_order, block_id
# --------------------------------------------------------------------------- #


def test_kind_outranks_score(eng: Engine) -> None:
    scope, _, _ = build_doc(eng, texts=["dien ap dong", "điện áp", "điên áp dòng cao"])
    page = scope.search("điện áp dòng", limit=10)
    kinds = [hit.match_kind for hit in page.hits]
    assert kinds == sorted(kinds, key=TIER.__getitem__)
    assert set(kinds) >= {"exact", "folded"}


def test_tie_break_is_page_then_reading_order(eng: Engine) -> None:
    def builder(doc: str) -> dict[str, Block]:
        return {
            "p2": make_block(doc, 2, 0, text="gamma", key="p2"),
            "p1b": make_block(doc, 1, 5, text="gamma", key="p1b"),
            "p1a": make_block(doc, 1, 2, text="gamma", key="p1a"),
        }

    scope, _, named = put_blocks(eng, builder, n_pages=2)
    assert scope.ids("gamma", limit=10) == [named["p1a"].block_id, named["p1b"].block_id, named["p2"].block_id]


def test_equal_score_ties_by_document_id(eng: Engine) -> None:
    _, doc_a, _ = build_doc(eng, texts=["delta"])
    _, doc_b, _ = build_doc(eng, texts=["delta"])
    page = search_blocks(eng.store, subject_code=SUBJECT, query="delta", document_ids=[doc_b, doc_a], limit=10)
    assert [h.block.document_id for h in page.hits] == sorted([doc_a, doc_b])


# --------------------------------------------------------------------------- #
# Công thức điểm và kiểu khớp
# --------------------------------------------------------------------------- #


def test_score_formula_exact_values(eng: Engine) -> None:
    scope, _, _ = build_doc(eng, texts=["alpha", "alpha beta gamma delta"])
    scores = {h.block.text: h.score for h in scope.search("alpha", limit=10).hits}
    assert scores["alpha"] == pytest.approx(3.25)  # 3 + 0.5 * 1 / (1 + 1)
    assert scores["alpha beta gamma delta"] == pytest.approx(3.1)  # 3 + 0.5 * 1 / (1 + 4)


def test_denser_block_ranks_first_even_when_later(eng: Engine) -> None:
    scope, _, blocks = build_doc(eng, texts=["alpha beta gamma delta", "alpha"])
    assert scope.ids("alpha", limit=10) == [blocks[1].block_id, blocks[0].block_id]


def test_token_coverage_rounds_up(eng: Engine) -> None:
    scope, _, _ = build_doc(eng, texts=["alpha"])
    assert scope.ids("alpha beta gamma") == []  # 1/3 < 0.5, ceil(1,5) = 2
    assert scope.ids("alpha beta") != []  # 1/2 đủ


def test_overall_kind_folded_when_any_token_folded(eng: Engine) -> None:
    scope, _, _ = build_doc(eng, texts=["lịch điều phối"])
    assert scope.search("lịch dieu phoi").hits[0].match_kind == "folded"
    assert scope.search("lịch điều phối").hits[0].match_kind == "exact"


def test_technical_bonus_only_for_exact(eng: Engine) -> None:
    scope, _, blocks = build_doc(eng, texts=["thanh ghi CPSZ"])
    (hit,) = scope.search("CPSR").hits
    assert hit.block.block_id == blocks[0].block_id
    assert hit.match_kind == "fuzzy"
    assert hit.score < search_index.DEFAULT_SETTINGS.exact_weight  # không được cộng điểm token kỹ thuật


def test_duplicate_query_tokens_collapse(eng: Engine) -> None:
    scope, _, _ = build_doc(eng, texts=["lịch", "lịch thi"])
    once = scope.search("lịch", limit=10)
    twice = scope.search("lịch lịch", limit=10)
    assert [h.model_dump() for h in once.hits] == [h.model_dump() for h in twice.hits]


@pytest.mark.parametrize(("word", "typo"), [("xz", "xy"), ("abcdef", "abdcef"), ("abcdefg", "abdcefg")])
def test_fuzzy_boundaries_on_every_path(eng: Engine, word: str, typo: str) -> None:
    scope, _, blocks = build_doc(eng, texts=[word])
    page = scope.search(typo)
    assert [h.block.block_id for h in page.hits] == [blocks[0].block_id]
    assert page.hits[0].match_kind == "fuzzy"


def test_single_letter_never_fuzzy(eng: Engine) -> None:
    scope, _, _ = build_doc(eng, texts=["y"])
    assert scope.ids("x") == []


def test_trigram_body_drops_diacritics(eng: Engine) -> None:
    scope, _, blocks = build_doc(eng, texts=["đặcđiểm"])
    page = scope.search("dacdiwm")
    assert [h.block.block_id for h in page.hits] == [blocks[0].block_id]
    assert page.hits[0].match_kind == "fuzzy"


def test_trigram_candidates_stay_in_document_scope(eng: Engine) -> None:
    scope_a, _, blocks_a = build_doc(eng, texts=["photosynthesis"])
    build_doc(eng, texts=["photosynthesis"])
    page = scope_a.search("photosynthesys", limit=10)
    assert [h.block.block_id for h in page.hits] == [blocks_a[0].block_id]


def test_last_trigram_is_used(eng: Engine) -> None:
    scope, _, blocks = build_doc(eng, texts=["abcdefg"])
    assert scope.ids("abdcefg") == [blocks[0].block_id]


def test_past_exam_documents_are_not_searchable(eng: Engine) -> None:
    scope, doc, _ = build_doc(eng, texts=["dap an de cu"])
    assert scope.ids("dap an de cu") != []
    current = eng.store.get_imported_document(doc)
    assert current is not None
    meta = current.meta.model_copy(update={"source_kind": SourceKind.PAST_EXAM})
    eng.store.update_imported_document(current.model_copy(update={"meta": meta}), current.version)
    # Khối đã đủ điều kiện từ trước vẫn không được tra cứu khi tài liệu là đề cũ (§5.15).
    assert scope.ids("dap an de cu") == []


def test_no_closed_document_in_scope_uses_python_engine(eng: Engine) -> None:
    scope, _, _ = build_doc(eng, texts=["tai lieu chua dong"], close_doc=False)
    page = scope.search("tai lieu")
    assert page.hits == [] and page.engine == "python" and page.total == 0


def test_total_capped_only_above_cap(eng: Engine) -> None:
    scope, _, _ = build_doc(eng, texts=["omega a", "omega b", "omega c"])
    exact = scope.search("omega", settings=SearchSettings(max_results=3))
    assert (exact.total, exact.total_capped) == (3, False)
    over = scope.search("omega", settings=SearchSettings(max_results=2))
    assert (over.total, over.total_capped) == (2, True)


# --------------------------------------------------------------------------- #
# Truy vấn
# --------------------------------------------------------------------------- #


def test_query_length_boundary_and_nfc() -> None:
    assert len(parse_query("a" * 200)) == 1
    with pytest.raises(QueryError):
        parse_query("a" * 201)
    nfd = unicodedata.normalize("NFD", "ộ" * 150)
    assert len(nfd) == 450  # > 200 code point thô nhưng NFC chỉ 150
    assert len(parse_query(nfd)) == 1
    assert parse_query(unicodedata.normalize("NFD", "bộ2")) == parse_query("bộ2")
    assert len(parse_query("  " + "a" * 200 + "  ")) == 1


def test_query_with_unaligned_combining_mark_is_not_technical() -> None:
    tokens = parse_query("a̅b R13 thanh")
    assert [t.technical for t in tokens] == [False, False, False]
    assert [t.technical for t in parse_query("CPSR R13 thanh")] == [True, True, False]


def test_query_dotted_capital_i_falls_back_to_folded_token() -> None:
    (token,) = parse_query("aİa")
    assert token.kept == token.folded == "aia"


def test_dotted_capital_i_text_is_found_as_folded() -> None:
    scope, _, blocks = build_doc(Engine("memory", IngestionStore(), {}), texts=["aİa"])
    page = scope.search("aİa")
    assert [h.block.block_id for h in page.hits] == [blocks[0].block_id]
    assert page.hits[0].match_kind == "folded"


def test_touching_hits_merge_into_one_span() -> None:
    scope, _, _ = build_doc(Engine("memory", IngestionStore(), {}), texts=["a̅a"])
    page = scope.search("a")
    assert [(s.start, s.end) for s in page.hits[0].match_spans] == [(0, 3)]


def test_fold_text_cluster_and_utf16_boundaries() -> None:
    assert fold_text("x́y").folded_ends[0] == 2
    assert utf16_offsets("￿") == [0, 1]
    assert utf16_offsets("\U00010000") == [0, 2]


# --------------------------------------------------------------------------- #
# Đếm trang
# --------------------------------------------------------------------------- #


def _confirm_page(store: IngestionStore, doc: str, number: int, *, lines_text: str | None) -> None:
    current = store.get_page(doc, number, PageExtraction)
    assert current is not None
    update: dict[str, Any] = {
        "confirmed": True,
        "confirmed_by": "user",
        "confirmed_at": NOW,
        "version": current.version + 1,
    }
    if lines_text is not None:
        update["lines"] = [line.model_copy(update={"text": lines_text}) for line in current.lines]
    updated = current.model_copy(update=update)
    store.update_page(doc, number, updated, status=updated.status, confirmed=True, expected_version=current.version)


def test_page_counts_skip_dropped_failed_and_blank_pages() -> None:
    store = IngestionStore()
    doc = "doc_00000000000000e7"
    seed(store, doc, pages=5, page_state={2: {"dropped": True}, 3: {"status": "failed"}, 4: {"confirmed": False}})
    keep = make_block(doc, 1, 0, text="co chu rhinoceros", key="k")
    store.save_document_layout(doc, layout({1: [keep]}, doc_id=doc))
    store.refresh_eligible_blocks(doc)
    confirm_document(store, doc, title="T", chapter_code="ch2")
    _confirm_page(store, doc, 2, lines_text=None)  # bỏ mềm nhưng đã xác nhận: không đếm
    _confirm_page(store, doc, 5, lines_text="   \t")  # chỉ khoảng trắng: không đếm
    page = search_blocks(store, subject_code=SUBJECT, query="rhinoceros", document_ids=[doc])
    assert page.pages_without_blocks == 0
    assert page.layout_failed_pages == 0


# --------------------------------------------------------------------------- #
# Chữ kề của figure/table/formula/code
# --------------------------------------------------------------------------- #


def figure_page(doc: str) -> dict[str, Block]:
    return {
        "before": make_block(doc, 1, 0, text="Bộ nhớ đệm giảm độ trễ truy cập", key="before"),
        "figure": make_block(doc, 1, 1, "figure", key="figure"),
        "caption": make_block(doc, 1, 2, "caption", text="Hình 3: Sơ đồ khối của bộ vi xử lý", key="caption"),
        "after": make_block(doc, 1, 3, text="Khối điều khiển giải mã lệnh", key="after"),
    }


def test_figure_found_by_figure_number_in_caption(eng: Engine) -> None:
    scope, _, named = put_blocks(eng, figure_page)
    page = scope.search("Hình 3", types=["figure"])
    assert [h.block.block_id for h in page.hits] == [named["figure"].block_id]
    assert page.hits[0].match_spans == []  # khớp ở chữ kề: chuỗi đích nằm ngoài khối
    both = scope.search("Hình 3", limit=10)
    assert {h.block.block_id for h in both.hits} == {named["figure"].block_id, named["caption"].block_id}
    caption_hit = next(h for h in both.hits if h.block.block_id == named["caption"].block_id)
    assert caption_hit.match_spans  # caption có chữ riêng ⇒ có khoảng tô


def test_figure_found_by_neighbor_paragraph_words(eng: Engine) -> None:
    scope, _, named = put_blocks(eng, figure_page)
    assert scope.ids("trễ truy cập", types=["figure"]) == [named["figure"].block_id]
    assert scope.ids("giải mã", types=["figure"]) == [named["figure"].block_id]


def test_plain_paragraph_does_not_borrow_neighbor_text(eng: Engine) -> None:
    scope, _, named = put_blocks(eng, figure_page)
    assert scope.ids("giải mã", types=["paragraph"]) == [named["after"].block_id]
    assert scope.ids("Hình 3", types=["paragraph"]) == []


def test_context_reaches_table_formula_and_code(eng: Engine) -> None:
    def builder(doc: str) -> dict[str, Block]:
        return {
            "t": make_block(doc, 1, 0, "table", key="t"),
            "c1": make_block(doc, 1, 1, "caption", text="Bảng 7 thống kê lỗi", key="c1"),
            "f": make_block(doc, 1, 2, "formula", text="x = y + z", key="f"),
            "p": make_block(doc, 1, 3, text="Phương trình cân bằng khối lượng", key="p"),
            "k": make_block(doc, 1, 4, "code", text="mov r1, r2", key="k"),
        }

    scope, _, named = put_blocks(eng, builder)
    assert scope.ids("Bảng 7", types=["table"]) == [named["t"].block_id]
    assert scope.ids("cân bằng khối lượng", types=["formula"]) == [named["f"].block_id]
    assert scope.ids("cân bằng khối lượng", types=["code"]) == [named["k"].block_id]


def test_context_stays_on_the_same_page(eng: Engine) -> None:
    def builder(doc: str) -> dict[str, Block]:
        return {
            "figure": make_block(doc, 1, 0, "figure", key="figure"),
            "caption": make_block(doc, 2, 0, "caption", text="Hình 9 ở trang khác", key="caption"),
        }

    scope, _, _ = put_blocks(eng, builder, n_pages=2)
    assert scope.ids("Hình 9", types=["figure"]) == []


def test_context_text_is_capped() -> None:
    doc = "doc_00000000000000e8"
    far, near = "alpha", "omega"
    before = make_block(doc, 1, 0, text=far + " " + "x" * 400 + " " + near, key="b")
    figure = make_block(doc, 1, 1, "figure", key="f")
    after = make_block(doc, 1, 2, text=far + " " + "y" * 400 + " " + near, key="a")
    contexts = block_contexts([before, figure, after])
    texts = [field.text for field in contexts[figure.block_id]]
    assert len(texts) == 2
    assert near in texts[0] and far not in texts[0] and len(texts[0]) == BLOCK_CONTEXT_MAX_CHARS  # đoạn đuôi
    assert far in texts[1] and near not in texts[1] and len(texts[1]) == BLOCK_CONTEXT_MAX_CHARS  # đoạn đầu
    assert len(block_contexts([before, figure, after], 10)[figure.block_id][0].text) == 10


def test_caption_of_links_and_excludes() -> None:
    doc = "doc_00000000000000e9"
    figure_a = make_block(doc, 1, 0, "figure", key="a")
    figure_b = make_block(doc, 1, 2, "figure", key="b")
    caption = make_block(doc, 1, 1, "caption", text="Hình 1 của A", key="c").model_copy(
        update={"caption_of": figure_a.block_id}
    )
    contexts = block_contexts([figure_a, caption, figure_b])
    assert [f.text for f in contexts[figure_a.block_id]] == ["Hình 1 của A"]
    assert figure_b.block_id not in contexts  # caption đã trỏ khối khác
    unlinked = make_block(doc, 1, 1, "caption", text="Hình 2", key="u")
    both = block_contexts([figure_a, unlinked, figure_b])
    assert both[figure_a.block_id][0].text == both[figure_b.block_id][0].text == "Hình 2"


def test_table_context_keeps_neighbor_paragraph() -> None:
    doc = "doc_00000000000000ea"
    cell = TableCell(text="mười hai", line_refs=[WordRef(line_idx=0, word_idx=0)], row=0, col=0)
    table = make_block(doc, 1, 0, "table", key="t").model_copy(update={"cells": [[cell]]})
    contexts = block_contexts([table, make_block(doc, 1, 1, text="bảng nhiệt độ", key="p")])
    assert [f.text for f in contexts[table.block_id]] == ["bảng nhiệt độ"]


# --------------------------------------------------------------------------- #
# Endpoint, kho không có chỉ mục, chỉ mục SQLite
# --------------------------------------------------------------------------- #


@pytest.fixture
def client_with_doc() -> Iterator[TestClient]:
    store = IngestionStore()
    doc = "doc_00000000000000eb"
    seed(store, doc, pages=1)
    block = make_block(doc, 1, 0, text="ộ" * 5, key="x")
    store.save_document_layout(doc, layout({1: [block]}, doc_id=doc))
    store.refresh_eligible_blocks(doc)
    confirm_document(store, doc, title="T", chapter_code="ch2")
    app.dependency_overrides[get_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("q", "status"),
    [
        (unicodedata.normalize("NFD", "ộ" * 200), 200),  # 400 code point thô, 200 sau NFC
        (unicodedata.normalize("NFD", "ộ" * 201), 422),
        ("  " + "a" * 200, 200),  # khoảng trắng đầu không tính
        ("a" * 201, 422),
    ],
)
def test_endpoint_query_limit_counts_after_normalisation(client_with_doc: TestClient, q: str, status: int) -> None:
    response = client_with_doc.get("/v1/vault/blocks/search", params={"q": q, "subject_code": SUBJECT})
    assert response.status_code == status


def test_endpoint_rejects_excluded_types_derived_from_block_types(client_with_doc: TestClient) -> None:
    for kind in ("header_footer", "page_number"):
        params = {"q": "a", "subject_code": SUBJECT, "type": kind}
        assert client_with_doc.get("/v1/vault/blocks/search", params=params).status_code == 422
    ok = client_with_doc.get("/v1/vault/blocks/search", params={"q": "a", "subject_code": SUBJECT, "type": "caption"})
    assert ok.status_code == 200


class _ProxyStore:
    """Bọc kho in-memory; `match_block_index` trả `[]` ở lần gọi đầu rồi `None` (kho mất bảng giữa chừng)."""

    def __init__(self, inner: IngestionStore, *, none_after: int) -> None:
        self._inner = inner
        self._calls = 0
        self._none_after = none_after

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def match_block_index(
        self, subject_code: str, terms: Sequence[str], *, trigram: bool
    ) -> list[tuple[str, str]] | None:
        self._calls += 1
        return None if self._calls > self._none_after else []

    def load_eligible_blocks(self, document_id: str, block_ids: Sequence[str]) -> list[Block]:
        return self._inner.load_eligible_blocks(document_id, block_ids)


def test_index_returning_none_midway_falls_back_to_scan() -> None:
    inner = IngestionStore()
    _, doc, blocks = build_doc(Engine("memory", inner, {}), texts=["alpha beta"])
    proxy = _ProxyStore(inner, none_after=1)
    assert isinstance(proxy, search_index.BlockIndexSource)
    store: Any = proxy
    page = search_blocks(store, subject_code=SUBJECT, query="alpha beta", document_ids=[doc])
    assert [h.block.block_id for h in page.hits] == [blocks[0].block_id]
    assert page.engine == "python"


def test_store_without_block_index_uses_python_path() -> None:
    inner = IngestionStore()
    _, doc, blocks = build_doc(Engine("memory", inner, {}), texts=["alpha beta"])

    class Plain:
        def __getattr__(self, name: str) -> Any:
            if name in ("match_block_index", "load_eligible_blocks"):
                raise AttributeError(name)
            return getattr(inner, name)

    plain: Any = Plain()
    assert not isinstance(plain, search_index.BlockIndexSource)
    page = search_blocks(plain, subject_code=SUBJECT, query="alpha", document_ids=[doc])
    assert [h.block.block_id for h in page.hits] == [blocks[0].block_id]
    assert page.engine == "python"


# --------------------------------------------------------------------------- #
# Chỉ mục SQLite
# --------------------------------------------------------------------------- #


def index_rows(store: SQLiteStore, table: str) -> list[tuple[str, str]]:
    with store._read() as conn:
        rows = conn.execute(f"SELECT document_id, block_id FROM {table}").fetchall()
    return sorted((str(r["document_id"]), str(r["block_id"])) for r in rows)


@pytest.fixture
def sqlite_store(tmp_path: Path) -> Iterator[SQLiteStore]:
    store = SQLiteStore.open(tmp_path / "kho.sqlite3")
    yield store
    store.close()


@pytest.mark.parametrize("table", ["blocks_fts", "blocks_fts_trigram"])
def test_sqlite_index_tracks_layout_refresh_revert(sqlite_store: SQLiteStore, table: str) -> None:
    store, doc = sqlite_store, "doc_00000000000000ec"
    seed(store, doc, pages=1)
    keep = make_block(doc, 1, 0, text="giu lai narwhal", key="keep")
    drop = make_block(doc, 1, 1, "header_footer", text="tieu de trang", key="hf")
    other = make_block(doc, 1, 2, text="khoi thu ba", key="o")
    store.save_document_layout(doc, layout({1: [keep, drop, other]}, doc_id=doc))
    store.refresh_eligible_blocks(doc)
    store.refresh_eligible_blocks(doc)  # lần hai không nhân đôi dòng
    assert index_rows(store, table) == sorted([(doc, keep.block_id), (doc, other.block_id)])
    replacement = make_block(doc, 1, 2, text="khoi thay the", key="new")
    store.save_document_layout(doc, layout({1: [keep, drop, replacement]}, doc_id=doc))
    assert other.block_id not in [row[1] for row in index_rows(store, table)]  # dọn ngay lúc lưu trang
    store.refresh_eligible_blocks(doc)
    assert index_rows(store, table) == sorted([(doc, keep.block_id), (doc, replacement.block_id)])
    assert store.revert_imported_document(doc, None) is True
    assert index_rows(store, table) == []


@pytest.mark.parametrize("table", ["blocks_fts", "blocks_fts_trigram"])
def test_sqlite_revert_keeping_upload_clears_index(sqlite_store: SQLiteStore, table: str) -> None:
    store, doc = sqlite_store, "doc_00000000000000ed"
    created = seed(store, doc, pages=1)
    block = make_block(doc, 1, 0, text="noi dung narwhal", key="x")
    store.save_document_layout(doc, layout({1: [block]}, doc_id=doc))
    store.refresh_eligible_blocks(doc)
    assert index_rows(store, table) == [(doc, block.block_id)]
    upload = UploadedDocument(
        document_id=doc,
        sha256=created.meta.sha256,
        filename="de.pdf",
        media_type="application/pdf",
        size_bytes=10,
        uploaded_at=NOW,
    )
    store.save_document(upload)
    assert store.revert_imported_document(doc, upload) is True
    assert index_rows(store, table) == []


def test_sqlite_token_only_in_ineligible_block_is_not_indexed(sqlite_store: SQLiteStore) -> None:
    store, doc = sqlite_store, "doc_00000000000000ee"
    seed(store, doc, pages=1)
    visible = make_block(doc, 1, 0, text="chi co o khoi hien", key="v")
    hidden = make_block(doc, 1, 1, "header_footer", text="secretword", key="h")
    store.save_document_layout(doc, layout({1: [visible, hidden]}, doc_id=doc))
    store.refresh_eligible_blocks(doc)
    assert store.match_block_index(SUBJECT, ["secretword"], trigram=False) == []
    assert index_rows(store, "blocks_fts") == [(doc, visible.block_id)]
    assert store.load_eligible_blocks(doc, [hidden.block_id]) == []


def test_memory_load_eligible_blocks_skips_ineligible() -> None:
    store, doc = IngestionStore(), "doc_00000000000000ef"
    seed(store, doc, pages=1)
    visible = make_block(doc, 1, 0, text="hien thi", key="v")
    hidden = make_block(doc, 1, 1, "header_footer", text="an", key="h")
    store.save_document_layout(doc, layout({1: [visible, hidden]}, doc_id=doc))
    store.refresh_eligible_blocks(doc)
    assert store.load_eligible_blocks(doc, [hidden.block_id]) == []
    assert store.load_eligible_blocks(doc, [visible.block_id]) == [visible]


def test_sqlite_load_eligible_blocks_batches_over_400_ids(sqlite_store: SQLiteStore) -> None:
    store, doc = sqlite_store, "doc_00000000000000f0"
    seed(store, doc, pages=1)
    blocks = [make_block(doc, 1, n, text=f"khoi so {n}", key=str(n)) for n in range(430)]
    store.save_document_layout(doc, layout({1: blocks}, doc_id=doc))
    store.refresh_eligible_blocks(doc)
    loaded = store.load_eligible_blocks(doc, [b.block_id for b in blocks])
    assert len(loaded) == 430
    assert len(store.load_eligible_blocks(doc, [b.block_id for b in blocks[:400]])) == 400


def test_sqlite_backfill_skips_documents_without_subject(
    sqlite_store: SQLiteStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, doc = sqlite_store, "doc_00000000000000f1"
    seed(store, doc, pages=1)
    block = make_block(doc, 1, 0, text="noi dung", key="x")
    store.save_document_layout(doc, layout({1: [block]}, doc_id=doc))
    store.refresh_eligible_blocks(doc)
    with store._write() as conn:
        conn.execute("UPDATE documents SET subject_code = NULL WHERE document_id = ?", (doc,))
    calls: list[str] = []
    original = SQLiteStore._index_eligible_blocks

    def spy(self: SQLiteStore, conn: Any, document_id: str) -> None:
        calls.append(document_id)
        original(self, conn, document_id)

    monkeypatch.setattr(SQLiteStore, "_index_eligible_blocks", spy)
    store._backfill_block_index()
    assert calls == []


def test_sqlite_index_body_includes_context(sqlite_store: SQLiteStore) -> None:
    store, doc = sqlite_store, "doc_00000000000000f2"
    seed(store, doc, pages=1)
    figure = make_block(doc, 1, 0, "figure", key="f")
    caption = make_block(doc, 1, 1, "caption", text="Hình 5 sơ đồ", key="c")
    store.save_document_layout(doc, layout({1: [figure, caption]}, doc_id=doc))
    store.refresh_eligible_blocks(doc)
    hits = store.match_block_index(SUBJECT, ["hinh"], trigram=False)
    assert hits == sorted([(doc, figure.block_id), (doc, caption.block_id)])
    assert store.match_block_index(SUBJECT, ["hinh"], trigram=True) == hits
