"""Kho file gốc (SPECIFICATION §5.13, §6.5): dọn thư mục tạm của tiến trình đã chết, cảnh báo khi `rmtree` lỗi, dọn
file tạm khi ghi lỗi, xoá file gốc mồ côi và giới hạn dung lượng lấy từ `PdfReaderConfig`.
"""

import hashlib
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from holocron_core.infrastructure.document_readers.config import PdfReaderConfig
from holocron_core.infrastructure.storage import original_files
from holocron_core.infrastructure.storage.original_files import (
    ERROR_INVALID_PARAMETER,
    TEMP_DIR_PREFIX,
    OriginalFileStore,
    open_process_error_means_dead,
    process_alive,
    remove_stale_temp_dirs,
)

CONTENT = b"%PDF-1.4\n% mau tu tao cho test don dep\n"
SHA = hashlib.sha256(CONTENT).hexdigest()


# Pid giả lập của một tiến trình đã chết, cho các test dọn thư mục: không dựa vào pid thật của tiến trình con đã
# thoát, vì hệ điều hành có thể cấp lại pid đó cho tiến trình khác giữa lúc tạo thư mục và lúc kiểm.
FAKE_DEAD_PID = 3_999_999_999


@pytest.fixture
def dead_pid(monkeypatch: pytest.MonkeyPatch) -> int:
    """Thay bộ kiểm pid của module: `FAKE_DEAD_PID` đã chết, pid khác kiểm thật như bình thường."""
    real_process_alive = original_files.process_alive
    monkeypatch.setattr(original_files, "process_alive", lambda pid: pid != FAKE_DEAD_PID and real_process_alive(pid))
    return FAKE_DEAD_PID


def _temp_dir(base: Path, name: str) -> Path:
    path = base / name
    path.mkdir()
    (path / f"{SHA}.pdf").write_bytes(CONTENT)
    return path


def test_process_alive_tells_live_and_dead_processes_apart() -> None:
    assert process_alive(os.getpid())
    assert process_alive(os.getppid()), "tiến trình cha của pytest còn chạy"
    with subprocess.Popen([sys.executable, "-c", "pass"]) as child:
        child.wait(timeout=30)
        # `child` còn giữ handle của tiến trình con (Windows), nên pid chưa thể được cấp lại trong lúc kiểm.
        assert not process_alive(child.pid)
    assert process_alive(os.getppid()), "lời gọi kiểm không kết thúc tiến trình được kiểm (Windows không dùng os.kill)"


@pytest.mark.parametrize(("error_code", "dead"), [(ERROR_INVALID_PARAMETER, True), (5, False), (8, False), (0, False)])
def test_only_invalid_parameter_from_open_process_means_dead(error_code: int, dead: bool) -> None:
    """Windows: từ chối quyền (5), thiếu bộ nhớ (8) hay lỗi lạ không chứng minh được tiến trình đã chết."""
    assert open_process_error_means_dead(error_code) is dead


def test_opening_without_data_dir_removes_only_dirs_of_dead_processes(tmp_path: Path, dead_pid: int) -> None:
    dead = _temp_dir(tmp_path, f"{TEMP_DIR_PREFIX}p{dead_pid}_abc")
    mine = _temp_dir(tmp_path, f"{TEMP_DIR_PREFIX}p{os.getpid()}_old")
    parent = _temp_dir(tmp_path, f"{TEMP_DIR_PREFIX}p{os.getppid()}_live")
    unknown = _temp_dir(tmp_path, f"{TEMP_DIR_PREFIX}abc123")
    stray_file = tmp_path / f"{TEMP_DIR_PREFIX}p1_file"
    stray_file.write_bytes(b"khong phai thu muc")

    files = OriginalFileStore.open(None, temp_base=tmp_path)
    try:
        assert not dead.exists(), "thư mục của tiến trình đã chết được dọn"
        assert mine.is_dir() and parent.is_dir() and unknown.is_dir() and stray_file.is_file()
        assert files.temporary and files.root.parent == tmp_path.resolve()
        assert files.root.name.startswith(f"{TEMP_DIR_PREFIX}p{os.getpid()}_")
    finally:
        files.close()
    assert not files.root.exists()


def test_failed_rmtree_on_close_is_logged_and_the_dir_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    files = OriginalFileStore.open(None, temp_base=tmp_path)
    files.commit(files.stage(CONTENT))

    def locked(path: Path) -> None:
        raise PermissionError(32, "file đang được tiến trình khác mở")

    monkeypatch.setattr(original_files.shutil, "rmtree", locked)
    with caplog.at_level(logging.WARNING, logger=original_files.__name__):
        files.close()
    assert files.root.is_dir() and files.exists(SHA)
    assert any(str(files.root) in record.getMessage() for record in caplog.records)
    monkeypatch.undo()
    files.close()
    assert not files.root.exists()


def test_failed_cleanup_of_a_stale_dir_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, dead_pid: int
) -> None:
    dead = _temp_dir(tmp_path, f"{TEMP_DIR_PREFIX}p{dead_pid}_x")
    real_rmtree = original_files.shutil.rmtree

    def locked(path: Path) -> None:
        raise PermissionError(32, "file đang được tiến trình khác mở")

    monkeypatch.setattr(original_files.shutil, "rmtree", locked)
    with caplog.at_level(logging.WARNING, logger=original_files.__name__):
        assert remove_stale_temp_dirs(tmp_path) == []
    assert dead.is_dir() and caplog.records
    monkeypatch.setattr(original_files.shutil, "rmtree", real_rmtree)
    assert remove_stale_temp_dirs(tmp_path) == [dead], "lần sau dọn được"


def test_failed_write_removes_the_staging_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = OriginalFileStore(tmp_path)

    def full_disk(descriptor: int) -> None:
        raise OSError(28, "hết chỗ trống giả lập")

    monkeypatch.setattr(original_files.os, "fsync", full_disk)
    with pytest.raises(OSError, match="hết chỗ"):
        files.stage(CONTENT)
    assert list(tmp_path.iterdir()) == []


def test_remove_orphans_keeps_referenced_files_and_ignores_other_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    files = OriginalFileStore(tmp_path)
    kept = files.commit(files.stage(CONTENT))
    orphan = files.commit(files.stage(CONTENT + b"mo coi"))
    other = tmp_path / "ghi_chu.pdf"
    other.write_bytes(b"khong phai sha256")
    staging = files.stage(CONTENT + b"dang ghi")
    assert files.remove_orphans({SHA}) == [orphan.stem]
    assert kept.is_file() and other.is_file() and staging.path.is_file() and not orphan.exists()

    locked = files.commit(files.stage(CONTENT + b"dang mo"))
    real_unlink = Path.unlink

    def unlink(path: Path, missing_ok: bool = False) -> None:
        if path == locked:
            raise PermissionError(32, "file đang mở")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    with caplog.at_level(logging.WARNING, logger=original_files.__name__):
        assert files.remove_orphans({SHA}) == []
    assert locked.is_file() and caplog.records


def test_size_limit_defaults_to_the_reader_config(tmp_path: Path) -> None:
    assert OriginalFileStore(tmp_path).max_bytes == PdfReaderConfig().max_bytes
    assert OriginalFileStore.open(tmp_path, max_bytes=7).max_bytes == 7
