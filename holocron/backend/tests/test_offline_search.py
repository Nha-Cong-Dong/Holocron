"""Tra cứu khối offline (SPECIFICATION §6.10, §12.3; ADR-010 mục 18; brief X10).

Dữ liệu TỰ TẠO (`tests/fixtures/search_queries.json`, sinh tất định, không phải tài liệu thật). Mỗi bài kiểm chạy trên các
đường: kho in-memory (Python), SQLite có FTS5 + trigram, SQLite chỉ FTS5 (không trigram), SQLite không FTS5 (đường lùi).
Ngưỡng p95 < 100 ms KHÔNG nằm ở đây mà ở `scripts/eval_search.py`; pytest chỉ kiểm tính đúng.
"""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.blocks import Block, TableCell, WordRef
from holocron_core.domain.documents import PageExtraction
from holocron_core.domain.repositories import AppStore, RecordNotFoundError
from holocron_core.domain.schemas import DocumentMeta
from holocron_core.domain.vault import BlockSearchPage, fold_text, utf16_offsets
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.storage import SQLiteStore, migrator, sqlite_store
from holocron_core.routers.api import app, get_store
from holocron_core.services import search_index
from holocron_core.services.search_index import QueryError, SearchSettings, parse_query, search_blocks
from tests.test_block_store import NOW, block_id, layout, make_block, seed

FIXTURE = Path(__file__).parent / "fixtures" / "search_queries.json"
SUBJECT = "IT3070"
ENGINES = ("memory", "fts5_trigram", "fts5", "sqlite_python")
DOC_PREFIX = "doc_00000000000000"
DOC_A, DOC_B, DOC_C = DOC_PREFIX + "0a", DOC_PREFIX + "0b", DOC_PREFIX + "0c"


def load_fixture() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def open_engine(engine: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppStore:
    if engine == "memory":
        return IngestionStore()
    tmp_path.mkdir(parents=True, exist_ok=True)
    if engine == "sqlite_python":
        monkeypatch.setattr(sqlite_store, "fts5_available", lambda conn: False)
    if engine == "fts5":
        monkeypatch.setattr(sqlite_store, "trigram_available", lambda conn: False)
    return SQLiteStore.open(tmp_path / "kho.sqlite3", crops_dir=tmp_path / "crops")


def close(store: AppStore) -> None:
    if isinstance(store, SQLiteStore):
        store.close()


def corpus_block(doc_id: str, spec: dict[str, Any]) -> Block:
    kind = spec["type"]
    data: dict[str, Any] = {
        "block_id": block_id(doc_id, spec["page"], spec["order"], kind, spec["key"]),
        "document_id": doc_id,
        "page_number": spec["page"],
        "type": kind,
        "bbox": None,
        "source": "text_layer",
        "reading_order": spec["order"],
        "text": spec.get("text"),
        "line_refs": [WordRef(line_idx=spec["order"], word_idx=0)] if kind != "figure" else [],
    }
    if kind == "table":
        data["text"] = None
        data["cells"] = [
            [
                TableCell(text=text, line_refs=[WordRef(line_idx=r, word_idx=c)], row=r, col=c)
                for c, text in enumerate(row)
            ]
            for r, row in enumerate(spec["cells"])
        ]
    if kind == "figure":
        data["text"] = None
        data["labels"] = spec["labels"]
    return Block.model_validate(data)


def confirm_document(store: AppStore, doc_id: str, *, title: str, chapter_code: str) -> None:
    current = store.get_imported_document(doc_id)
    assert current is not None
    meta: DocumentMeta = current.meta.model_copy(update={"title": title})
    store.update_imported_document(
        current.model_copy(update={"status": "confirmed", "chapter_code": chapter_code, "meta": meta}),
        current.version,
    )


def build_corpus(store: AppStore) -> dict[str, str]:
    """Nạp bộ dữ liệu của fixture; trả `key → block_id`."""
    ids: dict[str, str] = {}
    for doc in load_fixture()["docs"]:
        doc_id = DOC_PREFIX + str(doc["suffix"]).rjust(2, "0")
        seed(store, doc_id, pages=doc["pages"])
        pages: dict[int, list[Block]] = {}
        for spec in doc["blocks"]:
            block = corpus_block(doc_id, spec)
            ids[spec["key"]] = block.block_id
            pages.setdefault(block.page_number, []).append(block)
        store.save_document_layout(doc_id, layout(pages, doc_id=doc_id))
        store.refresh_eligible_blocks(doc_id)
        confirm_document(store, doc_id, title=doc["title"], chapter_code=doc["chapter_code"])
    return ids


@dataclass
class Engine:
    """Một kho đã mở theo một đường tra cứu; dùng chung cả module vì SQLite trên ổ này chậm mỗi lần mở/ghi."""

    name: str
    store: AppStore
    ids: dict[str, str]
    scratch_docs: int = 0


def _open_engines(tmp_path_factory: pytest.TempPathFactory, *, with_corpus: bool) -> Iterator[dict[str, Engine]]:
    engines: dict[str, Engine] = {}
    try:
        for name in ENGINES:
            with pytest.MonkeyPatch.context() as patch:
                store = open_engine(name, tmp_path_factory.mktemp(name), patch)
            engines[name] = Engine(name, store, build_corpus(store) if with_corpus else {})
        yield engines
    finally:
        for engine in engines.values():
            close(engine.store)


@pytest.fixture(scope="module")
def corpus_engines(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Engine]]:
    """Mỗi đường một kho chỉ đọc chứa bộ dữ liệu của fixture."""
    yield from _open_engines(tmp_path_factory, with_corpus=True)


@pytest.fixture(scope="module")
def scratch_engines(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Engine]]:
    """Mỗi đường một kho trống cho các bài kiểm riêng lẻ; mỗi bài thêm tài liệu của mình và LUÔN lọc theo tài liệu đó."""
    yield from _open_engines(tmp_path_factory, with_corpus=False)


@pytest.fixture(params=ENGINES)
def corpus(request: pytest.FixtureRequest, corpus_engines: dict[str, Engine]) -> tuple[AppStore, dict[str, str]]:
    engine = corpus_engines[str(request.param)]
    return engine.store, engine.ids


@pytest.fixture(params=ENGINES)
def scratch(request: pytest.FixtureRequest, scratch_engines: dict[str, Engine]) -> Engine:
    return scratch_engines[str(request.param)]


def new_doc(engine: Engine) -> str:
    engine.scratch_docs += 1
    return f"{DOC_PREFIX}{0x10 + engine.scratch_docs:02x}"


class Scoped:
    """Tra cứu giới hạn trong MỘT tài liệu của kho dùng chung."""

    def __init__(self, store: AppStore, doc_id: str) -> None:
        self.store = store
        self.doc_id = doc_id

    def search(self, query: str, **options: Any) -> BlockSearchPage:
        return search_blocks(self.store, subject_code=SUBJECT, query=query, document_ids=[self.doc_id], **options)

    def ids(self, query: str, limit: int = 3, **options: Any) -> list[str]:
        return [hit.block.block_id for hit in self.search(query, limit=limit, **options).hits]


def top_ids(store: AppStore, query: str, *, limit: int = 3, **options: Any) -> list[str]:
    page = search_blocks(store, subject_code=SUBJECT, query=query, limit=limit, **options)
    return [hit.block.block_id for hit in page.hits]


# --------------------------------------------------------------------------- #
# Độ đúng trên bộ truy vấn cố định
# --------------------------------------------------------------------------- #


def test_fixture_has_at_least_100_queries() -> None:
    assert len(load_fixture()["queries"]) >= 100


def test_fixed_query_set_top3_accuracy(corpus: tuple[AppStore, dict[str, str]]) -> None:
    store, ids = corpus
    queries = load_fixture()["queries"]
    misses = [q for q in queries if ids[q["expect"]] not in top_ids(store, q["q"])]
    assert 1 - len(misses) / len(queries) >= 0.90, misses[:5]


def test_engines_return_identical_rankings(corpus_engines: dict[str, Engine]) -> None:
    """Cùng thứ tự, điểm, kiểu khớp và khoảng khớp ở mọi đường (không chỉ top-3)."""
    queries = load_fixture()["queries"]
    results = {
        name: [
            [
                h.model_dump(mode="json")
                for h in search_blocks(e.store, subject_code=SUBJECT, query=q["q"], limit=50).hits
            ]
            for q in queries
        ]
        for name, e in corpus_engines.items()
    }
    for name in ENGINES[1:]:
        assert results[name] == results["memory"], name


def test_engine_label_matches_path(corpus_engines: dict[str, Engine]) -> None:
    expected = {"memory": "python", "sqlite_python": "python", "fts5": "fts5", "fts5_trigram": "fts5_trigram"}
    queries = load_fixture()["queries"]
    long_typo = next(q["q"] for q in queries if q["kind"] == "typo_long")
    folded = next(q["q"] for q in queries if q["kind"] == "folded")
    for name, engine in corpus_engines.items():
        strict = search_blocks(engine.store, subject_code=SUBJECT, query=folded)
        fuzzy = search_blocks(engine.store, subject_code=SUBJECT, query=long_typo)
        assert strict.engine == ("fts5" if name.startswith("fts5") else "python")
        assert fuzzy.engine == expected[name]


# --------------------------------------------------------------------------- #
# Các hành vi riêng lẻ
# --------------------------------------------------------------------------- #


TinyFactory = Callable[[list[str]], tuple[Scoped, list[Block]]]


@pytest.fixture
def tiny(scratch: Engine) -> TinyFactory:
    def factory(texts: list[str]) -> tuple[Scoped, list[Block]]:
        store, doc = scratch.store, new_doc(scratch)
        seed(store, doc, pages=1)
        blocks = [make_block(doc, 1, order, "paragraph", text=text, key=text) for order, text in enumerate(texts)]
        store.save_document_layout(doc, layout({1: blocks}, doc_id=doc))
        store.refresh_eligible_blocks(doc)
        confirm_document(store, doc, title="Tài liệu nhỏ", chapter_code="ch2")
        return Scoped(store, doc), blocks

    return factory


def test_unaccented_query_finds_accented_text(tiny: TinyFactory) -> None:
    scope, blocks = tiny(["Điện áp ra của bộ nguồn", "Dòng điện xoay chiều", "Ngắt cứng và ngắt mềm"])
    page = scope.search(query="dien ap")
    assert page.hits[0].block.block_id == blocks[0].block_id
    assert page.hits[0].match_kind == "folded"
    assert scope.search(query="ngat cung").hits[0].block.block_id == blocks[2].block_id


def test_exact_kind_when_diacritics_agree(tiny: TinyFactory) -> None:
    scope, blocks = tiny(["Điện áp ra của bộ nguồn", "Dien ap khong dau"])
    page = scope.search(query="điện áp")
    assert page.hits[0].block.block_id == blocks[0].block_id
    assert page.hits[0].match_kind == "exact"
    assert page.hits[1].match_kind == "folded"  # chữ không dấu vẫn tìm thấy, xếp sau


def test_one_character_typo_in_top3(tiny: TinyFactory) -> None:
    scope, blocks = tiny(["Semaphore bảo vệ vùng găng", "Pipelining trong kiến trúc máy", "Scheduler chọn tiến trình"])
    for query, index in (
        ("semaphire", 0),
        ("pipelinng", 1),
        ("schedular", 2),
        ("semaphroe", 0),
    ):  # thay, xoá, thay, hoán vị
        assert blocks[index].block_id in scope.ids(query), query
    short = scope.search(query="vung gnag")  # "gnag" ≈ "găng": token ngắn dùng khoảng cách sửa
    assert short.hits[0].block.block_id == blocks[0].block_id
    assert short.hits[0].match_kind == "fuzzy"


def test_technical_token_exact_case_insensitive_ranks_first(tiny: TinyFactory) -> None:
    scope, blocks = tiny(
        [
            "Thanh ghi R13 là con trỏ ngăn xếp",
            "Thanh ghi R14 là thanh ghi liên kết",
            "Thanh ghi chung R1 đến R12",
            "r130 gần giống",
        ]
    )
    page = scope.search(query="r13")
    assert page.hits[0].block.block_id == blocks[0].block_id
    assert page.hits[0].match_kind == "exact"
    assert [h.block.block_id for h in page.hits] == [blocks[0].block_id]  # đã có khớp chặt ⇒ không khớp mờ R14, r130
    assert scope.search(query="CPSR 0x1F MOV").hits == []


def test_hex_and_uppercase_technical_tokens(tiny: TinyFactory) -> None:
    scope, blocks = tiny(
        ["Lệnh MOV chép giá trị 0x1F vào thanh ghi", "Lệnh mov trong ví dụ khác", "Cờ CPSR gồm N Z C V"]
    )
    assert scope.ids("0x1f")[0] == blocks[0].block_id
    assert scope.ids("mov 0x1f")[0] == blocks[0].block_id
    assert scope.ids("MOV 0X1F")[0] == blocks[0].block_id
    assert scope.ids("cpsr")[0] == blocks[2].block_id


def test_nfd_and_nfc_queries_are_equivalent(tiny: TinyFactory) -> None:
    scope, blocks = tiny(
        ["Đường đi ngắn nhất", unicodedata.normalize("NFD", "Thuật toán Dijkstra ngắn nhất"), "Khác hẳn"]
    )
    for query in ("ngắn nhất", "thuật toán"):
        nfc = scope.search(query=unicodedata.normalize("NFC", query))
        nfd = scope.search(query=unicodedata.normalize("NFD", query))
        assert nfc.model_dump() == nfd.model_dump()
        assert len(nfc.hits) >= 1
    assert scope.ids("thuat toan")[0] == blocks[1].block_id


def test_empty_and_very_short_queries(tiny: TinyFactory) -> None:
    scope, blocks = tiny(["a b c", "ab cd", "Điện áp"])
    for blank in ("", "   ", "\t\n"):
        with pytest.raises(QueryError):
            scope.search(query=blank)
    one = scope.search(query="a")  # 1 ký tự: chỉ khớp chặt, không lỗi
    assert [h.block.block_id for h in one.hits] == [blocks[0].block_id]
    two = scope.search(query="ab")
    assert two.hits[0].block.block_id == blocks[1].block_id
    assert scope.search(query="zz").hits == []  # không token nào cách "zz" một thao tác


@pytest.mark.parametrize(
    "query",
    [
        '"',
        "*",
        "NEAR",
        "NEAR(a b)",
        "a AND b",
        'foo" OR "bar',
        "a* OR b*",
        "(a)",
        "- b",
        "x:y",
        "^a",
        "'; DROP TABLE blocks; --",
        "\\",
    ],
)
def test_fts_special_characters_are_inert(tiny: TinyFactory, query: str) -> None:
    scope, _ = tiny(["a b c", "NEAR x", "Điện áp"])
    page = scope.search(query=query)
    assert isinstance(page, BlockSearchPage)
    assert scope.search(query="dien").total == 1  # bảng vẫn nguyên vẹn


def test_query_limits(tiny: TinyFactory) -> None:
    scope, _ = tiny(["a b c"])
    with pytest.raises(QueryError):
        scope.search(query="x" * 201)
    with pytest.raises(QueryError):
        scope.search(query=" ".join(f"t{n}" for n in range(33)))
    assert len(parse_query(" ".join(f"t{n}" for n in range(32)))) == 32
    with pytest.raises(RecordNotFoundError):
        search_blocks(scope.store, subject_code="ZZ9999", query="a")


def test_result_cap_and_paging(tiny: TinyFactory) -> None:
    scope, _ = tiny([f"cung chu de so {n}" for n in range(12)])
    settings = SearchSettings(max_results=5)
    capped = scope.search(query="chu de", settings=settings, limit=50)
    assert (capped.total, capped.total_capped, len(capped.hits)) == (5, True, 5)
    page2 = scope.search(query="chu de", settings=settings, offset=3, limit=50)
    assert [h.block.block_id for h in page2.hits] == [h.block.block_id for h in capped.hits][3:]
    full = scope.search(query="chu de", limit=5)
    assert (full.total, full.total_capped, len(full.hits)) == (12, False, 5)
    beyond = scope.search(query="chu de", offset=100)
    assert beyond.hits == [] and beyond.total == 12


# --------------------------------------------------------------------------- #
# Khoảng khớp trong chữ gốc (j'')
# --------------------------------------------------------------------------- #


def slice_utf16(text: str, start: int, end: int) -> str:
    return text.encode("utf-16-le")[2 * start : 2 * end].decode("utf-16-le")


def test_spans_map_back_to_original_text_with_stroke_d_and_nfd(
    tiny: TinyFactory,
) -> None:
    original = "😀 " + unicodedata.normalize("NFD", "Đường đi ngắn nhất") + " và Đường khác"
    scope, blocks = tiny([original, "không liên quan"])
    page = scope.search(query="duong")
    hit = page.hits[0]
    assert hit.block.block_id == blocks[0].block_id
    assert hit.match_kind == "folded"
    cut = [slice_utf16(original, s.start, s.end) for s in hit.match_spans]
    assert cut == [unicodedata.normalize("NFD", "Đường"), "Đường"]
    assert hit.match_spans[0].start == 3  # emoji ngoài BMP chiếm 2 code unit, rồi một dấu cách
    assert all(s.target == "text" and s.start < s.end for s in hit.match_spans)


def test_spans_for_cells_and_labels(corpus: tuple[AppStore, dict[str, str]]) -> None:
    store, _ = corpus
    blocks = load_fixture()["docs"][0]["blocks"]
    table = next(b for b in blocks if b["type"] == "table")
    figure = next(b for b in blocks if b["type"] == "figure")
    cell_hit = search_blocks(store, subject_code=SUBJECT, query=fold_text(table["cells"][1][0]).folded).hits[0]
    figure_hit = search_blocks(store, subject_code=SUBJECT, query=fold_text(figure["labels"][0]).folded).hits[0]
    assert cell_hit.block.type == "table"
    assert {(s.target, s.row, s.cell_index) for s in cell_hit.match_spans} == {("cell", 1, 0)}
    assert figure_hit.block.type == "figure"
    assert {(s.target, s.label_index) for s in figure_hit.match_spans} == {("label", 0)}


def test_fold_text_maps_every_position() -> None:
    text = "Đạt: x́ ß 😀"
    folded = fold_text(text)
    assert len(folded.folded_starts) == len(folded.folded) == len(folded.folded_ends)
    assert all(0 <= s < e <= len(text) for s, e in zip(folded.folded_starts, folded.folded_ends, strict=True))
    assert folded.folded.startswith("dat: x")
    assert utf16_offsets("a😀b") == [0, 1, 3, 4]
    assert fold_text("").folded == ""


# --------------------------------------------------------------------------- #
# Tập khối đủ điều kiện, xoá khối, backfill
# --------------------------------------------------------------------------- #


def test_only_eligible_blocks_are_searchable(scratch: Engine) -> None:
    store, doc = scratch.store, new_doc(scratch)
    scope = Scoped(store, doc)
    seed(store, doc, pages=3, page_state={2: {"confirmed": False}, 3: {"dropped": True}})
    keep = make_block(doc, 1, 0, text="noi dung giu lai ky tu zebra", key="k")
    unconfirmed = make_block(doc, 2, 0, text="noi dung zebra chua xac nhan", key="u")
    dropped = make_block(doc, 3, 0, text="noi dung zebra bi bo", key="d")
    mismatch = make_block(doc, 1, 1, "table", flags=["table_token_mismatch"], key="m")
    store.save_document_layout(doc, layout({1: [keep, mismatch], 2: [unconfirmed], 3: [dropped]}, doc_id=doc))
    store.refresh_eligible_blocks(doc)
    assert scope.ids("zebra") == []  # khối đã đủ điều kiện nhưng tài liệu chưa đóng
    confirm_document(store, doc, title="Tài liệu", chapter_code="ch2")
    assert scope.ids("zebra", limit=10) == [
        keep.block_id
    ]  # trang chưa xác nhận, trang bỏ mềm, bảng lệch token: không có
    page = store.get_page(doc, 2, PageExtraction)
    assert page is not None
    confirmed = page.model_copy(
        update={"confirmed": True, "confirmed_by": "user", "confirmed_at": NOW, "version": page.version + 1}
    )
    store.update_page(doc, 2, confirmed, status=confirmed.status, confirmed=True, expected_version=page.version)
    store.refresh_eligible_blocks(doc)
    assert sorted(scope.ids("zebra", limit=10)) == sorted([keep.block_id, unconfirmed.block_id])  # confirm ⇒ có
    again = store.get_page(doc, 2, PageExtraction)
    assert again is not None
    reverted = again.model_copy(
        update={"confirmed": False, "confirmed_by": None, "confirmed_at": None, "version": again.version + 1}
    )
    store.update_page(doc, 2, reverted, status=reverted.status, confirmed=False, expected_version=again.version)
    store.refresh_eligible_blocks(doc)
    assert scope.ids("zebra", limit=10) == [keep.block_id]  # unconfirm ⇒ mất


def test_deleted_blocks_and_documents_leave_the_index(scratch: Engine) -> None:
    store, doc = scratch.store, new_doc(scratch)
    scope = Scoped(store, doc)
    seed(store, doc, pages=2)
    first = make_block(doc, 1, 0, text="giay gau truc narwhal", key="1")
    second = make_block(doc, 2, 0, text="giay gau truc narwhal hai", key="2")
    store.save_document_layout(doc, layout({1: [first], 2: [second]}, doc_id=doc))
    store.refresh_eligible_blocks(doc)
    confirm_document(store, doc, title="T", chapter_code="ch2")
    assert len(scope.ids("narwhal", limit=10)) == 2
    store.save_document_layout(doc, layout({2: []}, doc_id=doc))  # dựng lại trang 2 không còn khối
    assert scope.ids("narwhal", limit=10) == [first.block_id]
    assert store.delete_document(doc) is True
    if isinstance(store, SQLiteStore):
        for trigram in (False, True):
            assert store.match_block_index(SUBJECT, ["narwhal"], trigram=trigram) in ([], None)
    else:
        assert store.list_eligible_blocks(doc) == []


def test_index_rows_follow_refresh_in_sqlite(tmp_path: Path) -> None:
    store = SQLiteStore.open(tmp_path / "kho.sqlite3")
    try:
        doc = DOC_PREFIX + "e4"
        seed(store, doc, pages=1)
        block = make_block(doc, 1, 0, text="mot hai ba narwhal", key="x")
        store.save_document_layout(doc, layout({1: [block]}, doc_id=doc))
        assert store.match_block_index(SUBJECT, ["narwhal"], trigram=False) == []  # eligible = 0 ⇒ chưa lập chỉ mục
        store.refresh_eligible_blocks(doc)
        assert store.match_block_index(SUBJECT, ["narwhal"], trigram=False) == [(doc, block.block_id)]
        assert store.match_block_index(SUBJECT, ["arwh", "zzz"], trigram=True) == [(doc, block.block_id)]
        assert store.match_block_index("ZZ9999", ["narwhal"], trigram=False) == []
        assert store.match_block_index(SUBJECT, [], trigram=False) == []
        assert store.load_eligible_blocks(doc, [block.block_id]) == [block]
        assert store.load_eligible_blocks(doc, []) == []
        assert store.revert_imported_document(doc, None) is True
        assert store.match_block_index(SUBJECT, ["narwhal"], trigram=False) == []
    finally:
        store.close()


def test_backfill_when_fts_enabled_after_blocks_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "kho.sqlite3"
    doc = DOC_PREFIX + "e5"
    with monkeypatch.context() as patch:
        patch.setattr(sqlite_store, "fts5_available", lambda conn: False)
        old = SQLiteStore.open(path)
        try:
            seed(old, doc, pages=1)
            block = make_block(doc, 1, 0, text="chuyen gia narwhal", key="b")
            old.save_document_layout(doc, layout({1: [block]}, doc_id=doc))
            old.refresh_eligible_blocks(doc)
            confirm_document(old, doc, title="T", chapter_code="ch2")
            assert top_ids(old, "narwhal") == [block.block_id]
            assert old.match_block_index(SUBJECT, ["narwhal"], trigram=False) is None
        finally:
            old.close()
    reopened = SQLiteStore.open(path)
    try:
        assert reopened.match_block_index(SUBJECT, ["narwhal"], trigram=False) == [(doc, block.block_id)]
        page = search_blocks(reopened, subject_code=SUBJECT, query="narwahl")
        assert [h.block.block_id for h in page.hits] == [block.block_id]
        assert page.engine == "fts5_trigram"
    finally:
        reopened.close()


def test_trigram_probe_rejects_old_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = sqlite3.connect(":memory:")
    try:
        assert migrator.trigram_available(conn) is True
        monkeypatch.setattr(migrator, "TRIGRAM_MIN_SQLITE", (99, 0, 0))
        assert migrator.trigram_available(conn) is False
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Bộ lọc, đường dẫn mục, bộ đếm trang
# --------------------------------------------------------------------------- #


def test_filters_by_document_chapter_and_type(corpus: tuple[AppStore, dict[str, str]]) -> None:
    store, ids = corpus
    data = load_fixture()
    figure = next(b for b in data["docs"][1]["blocks"] if b["type"] == "figure")
    label = fold_text(figure["labels"][0]).folded
    assert top_ids(store, label)[0] == ids[figure["key"]]
    assert ids[figure["key"]] not in top_ids(store, label, document_ids=[DOC_A])
    assert top_ids(store, label, document_ids=[DOC_B])[0] == ids[figure["key"]]
    assert top_ids(store, label, chapter_codes=["ch3"])[0] == ids[figure["key"]]
    assert ids[figure["key"]] not in top_ids(store, label, chapter_codes=["ch2"])
    assert top_ids(store, label, types=["figure"])[0] == ids[figure["key"]]
    assert ids[figure["key"]] not in top_ids(store, label, types=["table"])
    assert top_ids(store, label, types=["header_footer"]) == []  # loại không tra cứu được bị bỏ khỏi bộ lọc


def test_section_path_document_title_and_page_counts(tmp_path: Path) -> None:
    from tests.test_block_store import section

    store = IngestionStore()
    doc = DOC_PREFIX + "e6"
    seed(store, doc, pages=3, page_state={3: {"confirmed": False}})
    target = make_block(doc, 1, 1, text="noi dung muc con axolotl", key="t")
    heading = make_block(doc, 1, 0, "heading", text="Tiêu đề chương", key="h")
    store.save_document_layout(doc, layout({1: [heading, target], 3: [make_block(doc, 3, 0, key="x")]}, doc_id=doc))
    root = section(doc, 0, parent=None, level=0, title="Chương hai", heading=[heading.block_id], chapter_code="ch2")
    child = section(doc, 1, parent=root.section_id, level=1, title="Mục con", blocks=[target.block_id])
    store.replace_sections(doc, [root, child])
    store.refresh_eligible_blocks(doc)
    confirm_document(store, doc, title="Giáo trình mẫu", chapter_code="ch2")
    page = search_blocks(store, subject_code=SUBJECT, query="axolotl")
    hit = page.hits[0]
    assert [item.title for item in hit.section_path] == ["Chương hai", "Mục con"]
    assert (hit.document_title, hit.chapter_code) == ("Giáo trình mẫu", "ch2")
    assert page.pages_without_blocks == 1  # trang 2: đã xác nhận, có chữ, không khối
    assert page.pages_unconfirmable_with_blocks == 0  # trang 3 có khối nhưng có chữ nên xác nhận được
    assert page.layout_failed_pages == 0


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #


@pytest.fixture
def api_client() -> Iterator[tuple[TestClient, dict[str, str]]]:
    store = IngestionStore()
    ids = build_corpus(store)
    app.dependency_overrides[get_store] = lambda: store
    yield TestClient(app), ids
    app.dependency_overrides.clear()


def test_endpoint_returns_block_search_page(api_client: tuple[TestClient, dict[str, str]]) -> None:
    client, ids = api_client
    query = load_fixture()["queries"][0]
    response = client.get("/v1/vault/blocks/search", params={"q": query["q"], "subject_code": SUBJECT, "limit": 5})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["hits"][0]["block"]["block_id"] == ids[query["expect"]]
    assert body["engine"] == "python"
    assert set(body) == {
        "total",
        "total_capped",
        "hits",
        "engine",
        "layout_failed_pages",
        "pages_without_blocks",
        "pages_unconfirmable_with_blocks",
    }
    assert set(body["hits"][0]) == {
        "block",
        "document_title",
        "chapter_code",
        "section_path",
        "match_kind",
        "match_spans",
        "score",
    }
    filtered = client.get(
        "/v1/vault/blocks/search",
        params={
            "q": query["q"],
            "subject_code": SUBJECT,
            "document_id": [DOC_C],
            "type": ["paragraph"],
            "chapter_code": ["ch5"],
        },
    )
    assert filtered.status_code == 200 and all(h["block"]["document_id"] == DOC_C for h in filtered.json()["hits"])


@pytest.mark.parametrize(
    ("params", "status"),
    [
        ({"q": "a", "subject_code": "ZZ9999"}, 404),
        ({"q": "", "subject_code": SUBJECT}, 422),
        ({"q": "   ", "subject_code": SUBJECT}, 422),
        ({"q": "x" * 201, "subject_code": SUBJECT}, 422),
        ({"q": " ".join(f"t{n}" for n in range(33)), "subject_code": SUBJECT}, 422),
        ({"q": "a"}, 422),
        ({"q": "a", "subject_code": "bad"}, 422),
        ({"q": "a", "subject_code": SUBJECT, "type": "header_footer"}, 422),
        ({"q": "a", "subject_code": SUBJECT, "type": "page_number"}, 422),
        ({"q": "a", "subject_code": SUBJECT, "limit": 51}, 422),
        ({"q": "a", "subject_code": SUBJECT, "limit": 0}, 422),
        ({"q": "a", "subject_code": SUBJECT, "offset": 1001}, 422),
        ({"q": "a", "subject_code": SUBJECT, "document_id": "nope"}, 422),
        ({"q": "a", "subject_code": SUBJECT, "chapter_code": "nope"}, 422),
        ({"q": "a", "subject_code": SUBJECT, "offset": 1000}, 200),
        ({"q": "*", "subject_code": SUBJECT}, 200),
    ],
)
def test_endpoint_validation(
    api_client: tuple[TestClient, dict[str, str]], params: dict[str, Any], status: int
) -> None:
    client, _ = api_client
    assert client.get("/v1/vault/blocks/search", params=params).status_code == status


def test_chunk_search_endpoint_unchanged(api_client: tuple[TestClient, dict[str, str]]) -> None:
    client, _ = api_client
    assert client.get("/v1/vault/search", params={"q": "a", "subject_code": SUBJECT}).status_code == 200


def test_non_indexed_store_uses_python_path() -> None:
    """Kho không cài `BlockIndexSource` vẫn tra cứu được (đường lùi qua `list_eligible_blocks`)."""
    assert not isinstance(object(), search_index.BlockIndexSource)
