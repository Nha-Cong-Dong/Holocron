"""Kho file gốc của bộ nhập PDF (SPECIFICATION §5.13, §6.5): vị trí, ghi nguyên tử, giới hạn và đường dẫn an toàn."""

import hashlib
from pathlib import Path

import pytest

from holocron_core.infrastructure.storage.original_files import (
    FILES_DIRNAME,
    OriginalFileError,
    OriginalFileStore,
    StagedFile,
)

CONTENT = b"%PDF-1.4\n% mau tu tao cho test\n"
SHA = hashlib.sha256(CONTENT).hexdigest()


def _files(root: Path) -> list[str]:
    return sorted(path.name for path in root.iterdir())


def test_stage_then_commit_writes_sha256_named_file_inside_the_data_dir(tmp_path: Path) -> None:
    files = OriginalFileStore.open(tmp_path)
    assert files.root == (tmp_path / FILES_DIRNAME).resolve() and not files.temporary
    staged = files.stage(CONTENT)
    assert staged.sha256 == SHA and staged.size_bytes == len(CONTENT)
    assert staged.path.parent == files.root and not files.exists(SHA)
    final = files.commit(staged)
    assert final == files.root / f"{SHA}.pdf" and final.read_bytes() == CONTENT
    assert _files(files.root) == [f"{SHA}.pdf"], "không còn file tạm sau khi commit"
    files.close()
    assert final.is_file(), "thư mục dữ liệu không bị xoá khi tắt app"


def test_without_data_dir_files_live_in_a_temp_dir_removed_on_close() -> None:
    files = OriginalFileStore.open(None)
    assert files.temporary
    files.commit(files.stage(CONTENT))
    assert files.exists(SHA)
    root = files.root
    files.close()
    assert not root.exists()


def test_commit_of_an_already_stored_content_keeps_one_file(tmp_path: Path) -> None:
    files = OriginalFileStore(tmp_path)
    first = files.commit(files.stage(CONTENT))
    assert files.commit(files.stage(CONTENT)) == first
    assert _files(tmp_path) == [f"{SHA}.pdf"]


@pytest.mark.parametrize(("content", "message"), [(b"", "rỗng"), (b"x" * 11, "vượt")])
def test_empty_or_oversized_content_is_refused_without_writing(tmp_path: Path, content: bytes, message: str) -> None:
    files = OriginalFileStore(tmp_path, max_bytes=10)
    with pytest.raises(OriginalFileError, match=message):
        files.stage(content)
    assert _files(tmp_path) == []
    assert files.stage(b"x" * 10).size_bytes == 10, "đúng giới hạn vẫn được nhận"


@pytest.mark.parametrize("sha256", ["../x", "../" + "a" * 61, "A" * 64, "a" * 63, "a" * 64 + "/..", ""])
def test_paths_only_come_from_a_well_formed_sha256(tmp_path: Path, sha256: str) -> None:
    files = OriginalFileStore(tmp_path)
    with pytest.raises(OriginalFileError):
        files.path_for(sha256)
    with pytest.raises(OriginalFileError):
        files.delete(sha256)


def test_commit_and_discard_refuse_files_not_staged_by_the_store(tmp_path: Path) -> None:
    files = OriginalFileStore(tmp_path / "files")
    outside = tmp_path / ".staging-evil.part"
    outside.write_bytes(CONTENT)
    wrong_name = files.root / "x.pdf"
    wrong_name.write_bytes(CONTENT)
    for path in (outside, wrong_name, files.root / ".." / ".staging-evil.part"):
        forged = StagedFile(path=path, sha256=SHA, size_bytes=len(CONTENT))
        with pytest.raises(OriginalFileError):
            files.commit(forged)
        with pytest.raises(OriginalFileError):
            files.discard(forged)
    assert outside.is_file() and wrong_name.is_file() and not files.exists(SHA)


def test_discard_and_delete_remove_only_their_own_file(tmp_path: Path) -> None:
    files = OriginalFileStore(tmp_path)
    files.discard(files.stage(CONTENT))
    assert _files(tmp_path) == []
    files.commit(files.stage(CONTENT))
    other = files.commit(files.stage(CONTENT + b"khac"))
    assert files.delete(SHA) is True
    assert files.delete(SHA) is False
    assert _files(tmp_path) == [other.name]


def test_leftover_staging_files_are_cleaned(tmp_path: Path) -> None:
    files = OriginalFileStore(tmp_path)
    files.stage(CONTENT)
    files.stage(CONTENT)
    kept = files.commit(files.stage(CONTENT + b"giu"))
    assert files.clean_staging() == 2
    assert _files(tmp_path) == [kept.name]


def test_max_bytes_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(OriginalFileError):
        OriginalFileStore(tmp_path, max_bytes=0)
