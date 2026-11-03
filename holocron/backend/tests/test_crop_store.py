"""Bảo mật và vòng đời file ảnh cắt của kho SQLite (SPECIFICATION §5.13 "Ảnh cắt"; ADR-010 mục 19).

File ở `<data_dir>/crops/<document_id>/<crop_ref>.png`; đường dẫn chỉ dựng từ id đã kiểm mẫu và không thoát thư mục
dữ liệu; file dùng chung giữ tới hàng cuối; mở kho dọn mồ côi; kho mở không có thư mục ảnh không tạo/xoá file nào.
PNG tự tạo, ghi qua method của kho.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from holocron_core.infrastructure.storage import SQLiteStore
from holocron_core.infrastructure.storage.crop_files import CropFiles
from tests.test_block_store import DOC, OTHER, crop_of, layout, make_block, seed, tiny_png

REF = "a" * 64


def _open(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore.open(tmp_path / "kho.sqlite3", crops_dir=tmp_path / "data" / "crops")


def _files(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*")} if root.exists() else set()


@pytest.mark.parametrize(
    ("document_id", "crop_ref"),
    [
        ("../doc_0000000000000001", REF),
        ("doc_000000000000000g", REF),
        ("doc_00000000000000001", REF),
        ("doc_0000000000000001\n", REF),
        ("DOC_0000000000000001", REF),
        ("doc_0000000000000001/..", REF),
        ("doc_0000000000000001", "../" + REF[3:]),
        ("doc_0000000000000001", REF.upper()),
        ("doc_0000000000000001", REF + "\n"),
        ("doc_0000000000000001", REF[:63]),
        ("doc_0000000000000001", "..\\" + REF[3:]),
        ("", REF),
    ],
)
def test_crop_path_stays_in_data_dir(tmp_path: Path, document_id: str, crop_ref: str) -> None:
    root = tmp_path / "data" / "crops"
    files = CropFiles(root)
    with pytest.raises(ValueError):
        files.path_for(document_id, crop_ref)
    with pytest.raises(ValueError):
        files.write(document_id, crop_ref, b"x")
    if crop_ref == REF:  # id tài liệu sai
        with pytest.raises(ValueError):
            files.delete_document(document_id)
    assert not root.exists()
    good = files.path_for("doc_0000000000000001", REF)
    assert good.is_relative_to(root.resolve())
    assert good.name == f"{REF}.png" and good.parent.name == "doc_0000000000000001"


def test_crop_file_written_only_inside_data_dir(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        seed(store)
        figure = make_block(DOC, 1, 0, "figure")
        store.save_document_layout(DOC, layout({1: [figure]}))
        png = tiny_png(21)
        assert store.save_crop_image(crop_of(figure, png), png) == "stored"
        ref = hashlib.sha256(png).hexdigest()
        assert _files(tmp_path / "data") == {"crops", f"crops/{DOC}", f"crops/{DOC}/{ref}.png"}
        assert (tmp_path / "data" / "crops" / DOC / f"{ref}.png").read_bytes() == png
    finally:
        store.close()


def test_crop_dir_symlink_outside_data_dir_rejected(tmp_path: Path) -> None:
    root = tmp_path / "data" / "crops"
    root.mkdir(parents=True)
    outside = tmp_path / "ngoai"
    outside.mkdir()
    link = root / "doc_0000000000000001"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        return  # nền tảng không cho tạo liên kết (Windows không quyền): không có cách thoát nào để kiểm
    files = CropFiles(root)
    with pytest.raises(ValueError):
        files.write("doc_0000000000000001", REF, b"x")
    assert list(outside.iterdir()) == []
    files.delete_document("doc_0000000000000001")  # chỉ gỡ liên kết, không xoá thư mục đích
    assert outside.is_dir() and not link.exists()


def test_orphan_crop_files_removed_on_store_open(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        seed(store)
        figure = make_block(DOC, 1, 0, "figure")
        store.save_document_layout(DOC, layout({1: [figure]}))
        png = tiny_png(22)
        store.save_crop_image(crop_of(figure, png), png)
    finally:
        store.close()
    root = tmp_path / "data" / "crops"
    ref = hashlib.sha256(png).hexdigest()
    (root / DOC / f"{'b' * 64}.png").write_bytes(b"mo coi")
    (root / DOC / f".{ref}.123.tmp").write_bytes(b"tam")
    (root / DOC / "con").mkdir()
    (root / DOC / "con" / "x.png").write_bytes(b"x")
    (root / OTHER).mkdir()
    (root / OTHER / f"{ref}.png").write_bytes(png)
    (root / "lac.txt").write_bytes(b"lac")
    (root / "khong_phai_doc").mkdir()
    reopened = _open(tmp_path)
    try:
        assert _files(root) == {DOC, f"{DOC}/{ref}.png"}
        assert reopened.get_crop_bytes(DOC, ref) == png
    finally:
        reopened.close()


def test_shared_crop_file_kept_until_last_row_deleted(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        seed(store)
        first = make_block(DOC, 1, 0, "figure", key="mot")
        second = make_block(DOC, 2, 0, "figure", key="hai")
        store.save_document_layout(DOC, layout({1: [first], 2: [second]}))
        png = tiny_png(23)
        store.save_crop_image(crop_of(first, png), png)
        store.save_crop_image(crop_of(second, png), png)
        ref = hashlib.sha256(png).hexdigest()
        path = tmp_path / "data" / "crops" / DOC / f"{ref}.png"
        assert len(store.list_crop_images(DOC)) == 2 and path.is_file()
        store.save_document_layout(DOC, layout({1: [make_block(DOC, 1, 0, key="moi")]}))
        assert [row.block_id for row in store.list_crop_images(DOC)] == [second.block_id]
        assert path.is_file() and store.get_crop_bytes(DOC, ref) == png
        store.save_document_layout(DOC, layout({2: [make_block(DOC, 2, 0, key="moi")]}))
        assert store.list_crop_images(DOC) == []
        assert not path.exists()
        assert store.get_crop_bytes(DOC, ref) is None
    finally:
        store.close()


def test_replacing_crop_of_block_releases_old_file(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        seed(store)
        figure = make_block(DOC, 1, 0, "figure")
        store.save_document_layout(DOC, layout({1: [figure]}))
        old, new = tiny_png(24), tiny_png(25)
        store.save_crop_image(crop_of(figure, old), old)
        store.save_crop_image(crop_of(figure, new), new)
        root = tmp_path / "data" / "crops" / DOC
        assert _files(root) == {f"{hashlib.sha256(new).hexdigest()}.png"}
    finally:
        store.close()


def test_row_without_file_reads_none(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        seed(store)
        figure = make_block(DOC, 1, 0, "figure")
        store.save_document_layout(DOC, layout({1: [figure]}))
        png = tiny_png(26)
        store.save_crop_image(crop_of(figure, png), png)
        ref = hashlib.sha256(png).hexdigest()
        (tmp_path / "data" / "crops" / DOC / f"{ref}.png").unlink()
        assert len(store.list_crop_images(DOC)) == 1
        assert store.get_crop_bytes(DOC, ref) is None
    finally:
        store.close()


@pytest.mark.parametrize("memory_db", [True, False])
def test_store_without_crops_dir_creates_and_deletes_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_db: bool
) -> None:
    monkeypatch.chdir(tmp_path)
    stray = tmp_path / "crops" / DOC
    stray.mkdir(parents=True)
    (stray / f"{'c' * 64}.png").write_bytes(b"cua nguoi dung")
    (tmp_path / "data").mkdir()
    before = _files(tmp_path)
    store = SQLiteStore.open(":memory:" if memory_db else tmp_path / "data" / "kho.sqlite3")
    try:
        seed(store)
        figure = make_block(DOC, 1, 0, "figure")
        store.save_document_layout(DOC, layout({1: [figure]}))
        png = tiny_png(27)
        assert store.save_crop_image(crop_of(figure, png), png) == "capped"
        (stored,) = store.list_blocks(DOC)
        assert stored.flags == ["crop_pending", "crop_capped"] and stored.crop_ref is None
        assert store.list_crop_images(DOC) == []
        assert store.get_crop_bytes(DOC, "c" * 64) is None
        assert store.delete_document(DOC)
    finally:
        store.close()
    after = {name for name in _files(tmp_path) if not name.startswith("data/kho.sqlite3")}
    assert after == before
