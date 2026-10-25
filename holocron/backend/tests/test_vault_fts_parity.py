"""FTS5 chỉ lọc ứng viên và không bao giờ loại sót so với bộ tách từ Python (ADR-006 mục 4–5).

Kho in-memory, SQLite có FTS5 và SQLite thiếu FTS5 phải cho cùng kết quả tìm kiếm với chữ Việt, Kirin, Hy Lạp và chữ
ở dạng tổ hợp (NFD). Trước đây chỉ mục không dấu dùng `remove_diacritics 2`, vốn chỉ bỏ dấu chữ Latin, nên `иод` tìm
được `йод` ở Python mà FTS5 không trả.

Văn bản mẫu tự viết cho test (không phải tài liệu môn học có bản quyền).
"""

import re
import sqlite3
import unicodedata
from pathlib import Path

import pytest

from holocron_core.domain.repositories import AppStore
from holocron_core.domain.search_text import TOKEN_RE, no_diacritics_tokens, search_tokens
from holocron_core.domain.vault import SearchHit
from holocron_core.infrastructure.storage.connection import fts5_available
from holocron_core.infrastructure.storage.migrator import load_fts5_script
from holocron_core.services.vault import ingest_confirmed_text, search_chunks
from tests.vault_support import STORE_KINDS, add_document, confirmed, document_id, open_store

DOC = document_id("d")
PAGES = [
    ["1.1 Tiếng Việt", "Hoà tan muối trong nước tạo thành dung dịch điện li."],
    ["1.2 Кириллица", "Раствор йода в спирте используют как антисептик."],
    ["1.3 Ελληνικά", "Η άλφα ακτινοβολία αποτελείται από πυρήνες ηλίου."],
    ["1.4 Tổ hợp", unicodedata.normalize("NFD", "Cường độ dòng điện đo bằng ampe kế mắc nối tiếp.")],
    ["1.5 Latin", "Die Lösung enthält Natriumchlorid und Wasser."],
]
QUERIES = [
    "hòa tan",  # vị trí dấu thanh khác với văn bản (`Hoà`)
    "hoa tan",  # `tan` khớp ở chỉ mục chính
    "йода",
    "иода",  # Kirin: `й` = `и` + dấu breve, chỉ chỉ mục phụ bỏ được
    "άλφα",
    "αλφα",  # Hy Lạp: bỏ dấu tonos
    "ΑΛΦΑ",  # chữ hoa không dấu: casefold rồi khớp ở chỉ mục phụ
    unicodedata.normalize("NFD", "cường độ"),
    "cuong do",
    "lösung",
    "losung",
    "ηλιου",
    "không có từ này",
]


def _results(kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[list[SearchHit]]:
    folder = tmp_path / kind
    folder.mkdir()
    with monkeypatch.context() as patch:
        for store in open_store(kind, folder, patch):
            add_document(store, DOC)
            ingest_confirmed_text(store, confirmed(DOC, PAGES))
            return [search_chunks(store, "ET4010", query) for query in QUERIES]
    raise AssertionError("open_store không trả kho nào")


def test_every_store_returns_the_same_hits_for_every_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    memory, with_fts, without_fts = (_results(kind, tmp_path, monkeypatch) for kind in STORE_KINDS)
    assert memory == with_fts == without_fts
    found = dict(zip(QUERIES, memory, strict=True))
    for query in ("иода", "αλφα", "ΑΛΦΑ", "cuong do", "losung", "ηλιου"):
        assert found[query], f"{query!r} phải tìm thấy qua chỉ mục không dấu"
        assert all(hit.index == "no_diacritics" for hit in found[query])
    for query in ("йода", "άλφα", "hòa tan", "hoa tan", "lösung", QUERIES[7]):
        assert found[query] and all(hit.index == "primary" for hit in found[query]), query
    assert found["không có từ này"] == []


def test_fts_candidates_cover_python_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mọi chunk có chung ít nhất một token với truy vấn (theo Python) đều nằm trong tập FTS5 trả về."""
    with monkeypatch.context() as patch:
        for store in open_store("sqlite", tmp_path, patch):
            _check_candidates(store)


def _check_candidates(store: AppStore) -> None:
    assert store.vault_search_available
    add_document(store, DOC)
    chunks = ingest_confirmed_text(store, confirmed(DOC, PAGES))
    for index, tokenize in (("primary", search_tokens), ("no_diacritics", no_diacritics_tokens)):
        for query in QUERIES:
            terms = set(tokenize(query))
            expected = {chunk.chunk_id for chunk in chunks if not terms.isdisjoint(tokenize(chunk.raw_text))}
            fts = " OR ".join(f'"{term}"' for term in sorted(terms))
            matched = set(store.match_chunk_ids("ET4010", fts, index)) if terms else set()
            assert expected <= matched, (index, query)


def _fts_tokenizer() -> str:
    match = re.search(r"tokenize = '((?:[^']|'')*)'", load_fts5_script())
    assert match is not None
    return match.group(1).replace("''", "'")


def test_fts_tokenizer_never_splits_a_python_token() -> None:
    """Mọi ký tự Python coi là chữ (`TOKEN_RE`) cũng là ký tự token của tokenizer FTS5 đang dùng.

    Nhờ đó mỗi token Python là đúng một token FTS5, và cụm từ `"token"` trong truy vấn không bao giờ rỗng. Test chạy
    trên SQLite và bảng Unicode của môi trường hiện tại, nên phát hiện được khi một bản SQLite/Python khác lệch.
    """
    conn = sqlite3.connect(":memory:")
    try:
        if not fts5_available(conn):
            pytest.skip("SQLite của môi trường không có FTS5")
        tokenizer = _fts_tokenizer().replace("'", "''")
        conn.execute(f"CREATE VIRTUAL TABLE probe USING fts5 (body, tokenize = '{tokenizer}')")
        letters = [
            char
            for code in range(0x80, 0x110000)
            if not 0xD800 <= code <= 0xDFFF and TOKEN_RE.fullmatch(char := chr(code))
        ]
        conn.executemany(
            "INSERT INTO probe (rowid, body) VALUES (?, ?)", [(i + 1, f"x{c}x") for i, c in enumerate(letters)]
        )
        # Ký tự bị coi là dấu phân cách thì "x<c>x" thành hai token "x" và khớp truy vấn "x".
        split = [
            f"U+{ord(letters[row[0] - 1]):04X}"
            for row in conn.execute("SELECT rowid FROM probe WHERE probe MATCH '\"x\"'")
        ]
        assert split == []
    finally:
        conn.close()


def test_both_fts_tables_keep_diacritics() -> None:
    """Chỉ mục không dấu nhận văn bản Python đã bỏ dấu; FTS5 không được tự bỏ dấu (lệch giữa các hệ chữ)."""
    tokenizers = re.findall(r"tokenize = '((?:[^']|'')*)'", load_fts5_script())
    assert len(tokenizers) == 2
    assert all(tokenizer.startswith("unicode61 remove_diacritics 0 ") for tokenizer in tokenizers)
    assert tokenizers[0] == tokenizers[1]
