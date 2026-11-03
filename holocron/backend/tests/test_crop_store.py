"""Bảo mật và vòng đời file ảnh cắt của kho SQLite (SPECIFICATION §5.13 "Ảnh cắt"; ADR-010 mục 19).

File ở `<data_dir>/crops/<document_id>/<crop_ref>.png`; đường dẫn chỉ dựng từ id đã kiểm mẫu và không thoát thư mục
dữ liệu; file dùng chung giữ tới hàng cuối; mở kho dọn mồ côi; kho mở không có thư mục ảnh không tạo/xoá file nào.
PNG tự tạo, ghi qua method của kho.
"""

from __future__ import annotations

import hashlib
import os
import sys
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


def _link_dir(target: Path, link: Path) -> None:
    """Liên kết thư mục `link` → `target`: junction trên Windows (không cần quyền admin), symlink trên Unix.
    Không tạo được ⇒ `pytest.skip` có lý do (không bao giờ PASS im lặng)."""
    try:
        if sys.platform == "win32":
            import _winapi

            _winapi.CreateJunction(str(target.resolve()), str(link.absolute()))
        else:
            os.symlink(target, link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"nền tảng không cho tạo liên kết thư mục: {exc}")
    assert link.is_symlink() or link.is_junction()


def _outside_with_file(tmp_path: Path) -> Path:
    outside = tmp_path / "ngoai"
    outside.mkdir()
    (outside / f"{'d' * 64}.png").write_bytes(b"file cua nguoi dung")
    return outside


def _outside_intact(outside: Path) -> bool:
    return (outside / f"{'d' * 64}.png").read_bytes() == b"file cua nguoi dung"


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
    _link_dir(outside, link)
    files = CropFiles(root)
    with pytest.raises(ValueError):
        files.write("doc_0000000000000001", REF, b"x")
    assert list(outside.iterdir()) == []
    files.delete_document("doc_0000000000000001")  # chỉ gỡ liên kết, không xoá thư mục đích
    assert outside.is_dir() and not link.exists()


def test_crop_write_failure_leaves_no_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "data" / "crops"
    files = CropFiles(root)

    def fail(*_: object) -> None:
        raise OSError("gia lap loi doi ten")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="gia lap"):
        files.write("doc_0000000000000001", REF, b"x")
    assert _files(root) == {"doc_0000000000000001"}


def test_delete_document_on_link_only_unlinks(tmp_path: Path) -> None:
    root = tmp_path / "data" / "crops"
    root.mkdir(parents=True)
    outside = _outside_with_file(tmp_path)
    link = root / DOC
    _link_dir(outside, link)
    CropFiles(root).delete_document(DOC)  # không ValueError: chạy sau commit
    assert not link.exists() and not link.is_junction() and not link.is_symlink()
    assert _outside_intact(outside)


def test_sweep_never_follows_links(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        seed(store)
        figure = make_block(DOC, 1, 0, "figure")
        store.save_document_layout(DOC, layout({1: [figure]}))
        png = tiny_png(28)
        store.save_crop_image(crop_of(figure, png), png)
    finally:
        store.close()
    root = tmp_path / "data" / "crops"
    ref = hashlib.sha256(png).hexdigest()
    outside = _outside_with_file(tmp_path)
    _link_dir(outside, root / "lien_ket_goc")  # liên kết ở gốc, không thuộc tài liệu nào
    (root / "thu_muc_la").mkdir()
    _link_dir(outside, root / "thu_muc_la" / "lien_ket_long")  # liên kết lồng trong thư mục bị xoá cả cây
    _link_dir(outside, root / DOC / "lien_ket_trong_tai_lieu")
    (root / DOC / ref).write_bytes(png)  # đúng crop_ref nhưng thiếu đuôi .png
    reopened = _open(tmp_path)
    try:
        assert _files(root) == {DOC, f"{DOC}/{ref}.png"}
        assert reopened.get_crop_bytes(DOC, ref) == png
    finally:
        reopened.close()
    assert _outside_intact(outside)


def test_sweep_unlinks_linked_document_dir_of_kept_document(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        seed(store)
        figure = make_block(DOC, 1, 0, "figure")
        store.save_document_layout(DOC, layout({1: [figure]}))
        png = tiny_png(29)
        store.save_crop_image(crop_of(figure, png), png)
    finally:
        store.close()
    root = tmp_path / "data" / "crops"
    real = root / DOC
    for item in real.iterdir():
        item.unlink()
    real.rmdir()
    outside = _outside_with_file(tmp_path)
    _link_dir(outside, real)  # tài liệu CÓ hàng ảnh cắt nhưng thư mục của nó là liên kết ra ngoài
    reopened = _open(tmp_path)
    try:
        assert _files(root) == set()
        assert not real.is_junction() and not real.is_symlink()
        assert reopened.get_crop_bytes(DOC, hashlib.sha256(png).hexdigest()) is None
    finally:
        reopened.close()
    assert _outside_intact(outside)


def test_sweep_never_traverses_link_to_other_kept_document(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        seed(store)
        seed(store, OTHER)
        mine, theirs = make_block(DOC, 1, 0, "figure"), make_block(OTHER, 1, 0, "figure")
        store.save_document_layout(DOC, layout({1: [mine]}))
        store.save_document_layout(OTHER, layout({1: [theirs]}, doc_id=OTHER))
        png_mine, png_theirs = tiny_png(30), tiny_png(31)
        store.save_crop_image(crop_of(mine, png_mine), png_mine)
        store.save_crop_image(crop_of(theirs, png_theirs), png_theirs)
    finally:
        store.close()
    root = tmp_path / "data" / "crops"
    ref_theirs = hashlib.sha256(png_theirs).hexdigest()
    real = root / DOC
    for item in real.iterdir():
        item.unlink()
    real.rmdir()
    _link_dir(root / OTHER, real)  # thư mục của DOC là liên kết TRONG root, trỏ sang tài liệu khác
    (root / "thu_muc_la").mkdir()
    _link_dir(root / OTHER, root / "thu_muc_la" / "lien_ket_long")
    reopened = _open(tmp_path)
    try:
        assert _files(root) == {OTHER, f"{OTHER}/{ref_theirs}.png"}
        assert reopened.get_crop_bytes(OTHER, ref_theirs) == png_theirs
    finally:
        reopened.close()


def test_sweep_unlinks_file_link_named_like_kept_crop(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        seed(store)
        figure = make_block(DOC, 1, 0, "figure")
        store.save_document_layout(DOC, layout({1: [figure]}))
        png = tiny_png(32)
        store.save_crop_image(crop_of(figure, png), png)
    finally:
        store.close()
    root = tmp_path / "data" / "crops"
    ref = hashlib.sha256(png).hexdigest()
    target = root / DOC / f"{ref}.png"
    target.unlink()
    outside = _outside_with_file(tmp_path)
    try:
        os.symlink(outside / f"{'d' * 64}.png", target)
    except OSError as exc:
        pytest.skip(f"nền tảng không cho tạo liên kết file: {exc}")
    reopened = _open(tmp_path)
    try:
        assert _files(root) == {DOC}
        assert reopened.get_crop_bytes(DOC, ref) is None
    finally:
        reopened.close()
    assert _outside_intact(outside)


def test_file_without_row_reads_none(tmp_path: Path) -> None:
    store = _open(tmp_path)
    try:
        seed(store)
        stray = tmp_path / "data" / "crops" / DOC
        stray.mkdir(parents=True)
        (stray / f"{REF}.png").write_bytes(b"dat tay")
        assert store.get_crop_bytes(DOC, REF) is None
    finally:
        store.close()


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
