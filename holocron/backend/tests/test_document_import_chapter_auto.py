"""Chương tự động khi nhập tài liệu (SPECIFICATION §5.7 "Chương tự động", §6.5 bước 3 và 5; GĐ1 1A).

- `chapter_code` tuỳ chọn ở form: không gửi hoặc chuỗi rỗng ⇒ số trong TÊN FILE, không có hoặc trùng chương đã có
  của môn ⇒ chương trống nhỏ nhất. Mã người dùng nhập vẫn được phép trùng chương của tài liệu khác.
- Nhập lại cùng nội dung không gửi `chapter_code` ⇒ 200 bản cũ (không tính lại, không `CHAPTER_CONFLICT`); gửi mã
  khác ⇒ 409 như cũ.
- Hai file nhập cùng lúc (luồng thật, `threading.Barrier`) không nhận cùng chương trống: chọn chương dưới khoá môn.

Chạy trên cả ba loại kho; nội dung PDF là byte tự tạo, chỉ `FakePdfReader` đọc.
"""

import contextlib
import hashlib
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pytest

from holocron_core.domain.repositories import AppStore, ImportedDocument
from holocron_core.domain.schemas import DocumentMeta, SourceKind
from holocron_core.infrastructure.storage.original_files import OriginalFileStore
from holocron_core.routers.api import app
from holocron_core.services.chapter_naming import ChapterChoice, auto_chapter
from holocron_core.services.document_import import (
    CHAPTER_MISSING_DETAIL,
    IMPORT_ERROR_MESSAGES,
    DocumentImportError,
    ImportOutcome,
    ImportRequest,
    ImportRuntime,
    KeyedLocks,
    SynchronousImportQueue,
    accept_document,
)
from tests.document_import_support import FakePdfReader, HoldingQueue, ImportEnv, install, pdf_bytes
from tests.vault_support import NOW, STORE_KINDS, open_store

WAIT_S = 10.0


@pytest.fixture(params=STORE_KINDS)
def env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportEnv]:
    for store in open_store(str(request.param), tmp_path, monkeypatch):
        yield install(store, tmp_path / "files", SynchronousImportQueue(), FakePdfReader(page_count=2))
        app.dependency_overrides.clear()


def _doc_id(content: bytes) -> str:
    return "doc_" + hashlib.sha256(content).hexdigest()[:16]


def _auto(env: ImportEnv, tag: str, filename: str, subject_code: str = "ET4010") -> str:
    response = env.post(pdf_bytes(tag), chapter_code=None, filename=filename, subject_code=subject_code)
    assert response.status_code == 202, response.text
    chapter: str = response.json()["chapter_code"]
    stored = env.store.get_imported_document(_doc_id(pdf_bytes(tag)))
    assert stored is not None and stored.chapter_code == chapter, "chương trả về là chương đã lưu"
    return chapter


# --------------------------------------------------------------------------- #
# Số chương từ tên file
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("filename", "chapter"),
    [
        ("Chuong-2_Vi-xu-ly-ARM.pdf", "ch2"),
        ("ch02.pdf", "ch2"),
        ("2020 - bai 4.pdf", "ch4"),
        ("tong-hop.pdf", "ch1"),
        ("2020-2021.pdf", "ch1"),
        ("chuong-0.pdf", "ch1"),
        ("ch00 mo dau.pdf", "ch1"),
    ],
)
def test_chapter_comes_from_the_file_name(env: ImportEnv, filename: str, chapter: str) -> None:
    assert _auto(env, f"ten-{filename}", filename) == chapter


def test_empty_chapter_field_means_automatic(env: ImportEnv) -> None:
    response = env.post(pdf_bytes("rong"), chapter_code="", filename="Chuong-7.pdf")
    assert response.status_code == 202, response.text
    assert response.json()["chapter_code"] == "ch7"


@pytest.mark.parametrize("chapter_code", ["3", "chuong3", "ch100", "ch3.", "CH3", " ch3"])
def test_invalid_chapter_field_is_still_422_and_stores_nothing(env: ImportEnv, chapter_code: str) -> None:
    response = env.post(pdf_bytes(f"sai-{chapter_code}"), chapter_code=chapter_code)
    assert response.status_code == 422
    assert env.reader.probed == [] and list(env.files.root.iterdir()) == []


def test_no_number_or_a_taken_number_takes_the_smallest_free_chapter(env: ImportEnv) -> None:
    assert _auto(env, "a", "chuong-3.pdf") == "ch3"
    assert _auto(env, "b", "khong-so.pdf") == "ch1"
    assert _auto(env, "c", "bai-3.pdf") == "ch2", "ch3 đã có, ch1 đã có ⇒ ch2"
    assert _auto(env, "d", "mo-dau.pdf") == "ch4"
    assert _auto(env, "e", "Chuong-5.pdf") == "ch5"


def test_chapters_of_other_subjects_do_not_count(env: ImportEnv) -> None:
    assert _auto(env, "mon-a", "ch2.pdf", subject_code="ET4010") == "ch2"
    assert _auto(env, "mon-b", "ch2.pdf", subject_code="IT3070") == "ch2"


def test_user_chapter_may_repeat_and_blocks_automatic_choices(env: ImportEnv) -> None:
    assert env.post(pdf_bytes("slide"), chapter_code="ch2").json()["chapter_code"] == "ch2"
    assert env.post(pdf_bytes("giao-trinh"), chapter_code="ch2").json()["chapter_code"] == "ch2"
    assert _auto(env, "tu-dong", "Chuong-2.pdf") == "ch1", "chương người dùng nhập cũng là chương đã có"


def test_sub_chapter_codes_do_not_take_the_whole_chapter(env: ImportEnv) -> None:
    assert env.post(pdf_bytes("muc"), chapter_code="ch3.2").status_code == 202
    assert _auto(env, "chuong", "Chuong-3.pdf") == "ch3"


# --------------------------------------------------------------------------- #
# Nhập lại cùng nội dung
# --------------------------------------------------------------------------- #


def test_reimport_without_chapter_returns_the_old_record(env: ImportEnv) -> None:
    content = pdf_bytes("nhap-lai")
    first = env.post(content, chapter_code=None, filename="Chuong-2.pdf")
    assert first.status_code == 202 and first.json()["chapter_code"] == "ch2"
    again = env.post(content, chapter_code=None, filename="Chuong-9.pdf")
    assert again.status_code == 200, again.text
    assert again.json()["chapter_code"] == "ch2" and again.json()["filename"] == "Chuong-2.pdf"
    assert len(env.reader.probed) == 1, "không mở thử lại"
    assert len(env.queue.submitted) == 1, "không đọc lại"


def test_reimport_without_chapter_of_a_user_chapter_is_200_not_a_conflict(env: ImportEnv) -> None:
    content = pdf_bytes("nguoi-dung")
    assert env.post(content, chapter_code="ch5.1", filename="Chuong-2.pdf").status_code == 202
    again = env.post(content, chapter_code=None, filename="Chuong-2.pdf")
    assert again.status_code == 200 and again.json()["chapter_code"] == "ch5.1"


def test_reimport_with_another_chapter_is_still_a_409(env: ImportEnv) -> None:
    content = pdf_bytes("doi-chuong")
    assert env.post(content, chapter_code=None, filename="Chuong-2.pdf").status_code == 202
    conflict = env.post(content, chapter_code="ch4", filename="Chuong-2.pdf")
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": IMPORT_ERROR_MESSAGES["CHAPTER_CONFLICT"]}
    same = env.post(content, chapter_code="ch2", filename="Chuong-2.pdf")
    assert same.status_code == 200 and same.json()["chapter_code"] == "ch2"


def test_reimport_without_chapter_of_a_legacy_record_is_409(env: ImportEnv) -> None:
    content = pdf_bytes("cu")
    assert env.post(content, chapter_code="ch3").status_code == 202
    document = env.store.get_imported_document(_doc_id(content))
    assert document is not None
    env.store.update_imported_document(document.model_copy(update={"chapter_code": None}), document.version)
    response = env.post(content, chapter_code=None)
    assert response.status_code == 409 and response.json() == {"detail": CHAPTER_MISSING_DETAIL}


def test_the_document_itself_is_not_a_taken_chapter() -> None:
    def imported(doc_id: str, chapter_code: str | None) -> ImportedDocument:
        return ImportedDocument(
            meta=DocumentMeta(
                doc_id=doc_id,
                subject_code="ET4010",
                title="t",
                source_kind=SourceKind.TEXTBOOK,
                sha256=doc_id[4:] * 4,
                page_count=1,
                has_text_layer=True,
                uploaded_at=NOW,
            ),
            filename="t.pdf",
            size_bytes=1,
            chapter_code=chapter_code,
            status="review",
        )

    others = [imported("doc_aaaaaaaaaaaaaaaa", "ch2"), imported("doc_bbbbbbbbbbbbbbbb", None)]
    assert auto_chapter("Chuong-2.pdf", others, "doc_aaaaaaaaaaaaaaaa") == ChapterChoice("ch2", "file_name", ())
    assert auto_chapter("Chuong-2.pdf", others, "doc_cccccccccccccccc") == ChapterChoice(
        "ch1", "next_free", ("CHAPTER_NUMBER_TAKEN",)
    )


# --------------------------------------------------------------------------- #
# Hai file nhập đồng thời
# --------------------------------------------------------------------------- #


@dataclass
class MeetingProbeReader(FakePdfReader):
    """`probe` chờ ở `Barrier`: mọi lần nhận phải cùng ở bước mở thử một lúc thì mới đi tiếp. Nếu khoá môn bọc bước
    mở thử, lần nhận thứ hai không vào được và `Barrier` hết giờ (`BrokenBarrierError`)."""

    meeting: threading.Barrier = field(default_factory=lambda: threading.Barrier(2, timeout=WAIT_S))

    def probe(self, path: Path) -> int:
        self.meeting.wait()
        return super().probe(path)


@dataclass(frozen=True)
class Importer:
    store: AppStore
    runtime: ImportRuntime
    reader: FakePdfReader
    start: threading.Barrier

    def __call__(
        self, tag: str, subject_code: str = "ET4010", filename: str | None = None, chapter_code: str | None = None
    ) -> ImportOutcome:
        request = ImportRequest(
            content=pdf_bytes(tag),
            filename=filename or f"{tag}.pdf",
            declared_type="application/pdf",
            subject_code=subject_code,
            source_kind=SourceKind.LECTURE_SLIDES,
            chapter_code=chapter_code,
        )
        self.start.wait(WAIT_S)
        return accept_document(
            request, store=self.store, runtime=self.runtime, reader=self.reader, now=NOW + timedelta(seconds=1)
        )


@pytest.fixture(params=STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppStore]:
    yield from open_store(str(request.param), tmp_path, monkeypatch)


def _importer(store: AppStore, files_dir: Path, reader: FakePdfReader, parties: int = 2) -> Importer:
    runtime = ImportRuntime(files=OriginalFileStore(files_dir), queue=HoldingQueue())
    return Importer(store, runtime, reader, threading.Barrier(parties))


def _run_two(importer: Importer, *calls: tuple[str, str, str | None]) -> list[ImportOutcome]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(importer, tag, subject, filename) for tag, subject, filename in calls]
        return [future.result(timeout=2 * WAIT_S) for future in futures]


def test_two_files_imported_at_once_get_different_free_chapters(store: AppStore, tmp_path: Path) -> None:
    importer = _importer(store, tmp_path / "files", MeetingProbeReader(page_count=1))
    outcomes = _run_two(importer, ("mot", "ET4010", None), ("hai", "ET4010", None))
    assert all(outcome.created for outcome in outcomes)
    assert sorted(outcome.document.chapter_code for outcome in outcomes) == ["ch1", "ch2"]
    assert sorted(d.chapter_code or "" for d in store.list_imported_documents("ET4010")) == ["ch1", "ch2"]
    runtime = importer.runtime
    assert len(runtime.subject_locks) == 0 and len(runtime.locks) == 0, "khoá được bỏ hết"
    assert len(runtime.chapter_reservations) == 0, "chỗ giữ chương được trả khi lần nhận kết thúc"


def test_two_files_with_the_same_number_at_once(store: AppStore, tmp_path: Path) -> None:
    importer = _importer(store, tmp_path / "files", MeetingProbeReader(page_count=1))
    outcomes = _run_two(importer, ("a", "ET4010", "Chuong-2.pdf"), ("b", "ET4010", "Chuong-2-ban-2.pdf"))
    chapters = sorted((o.document.chapter_code, o.document.chapter_source.code) for o in outcomes)
    assert chapters == [("ch1", "next_free"), ("ch2", "file_name")]


def test_imports_of_different_subjects_do_not_wait_for_each_other(store: AppStore, tmp_path: Path) -> None:
    importer = _importer(store, tmp_path / "files", MeetingProbeReader(page_count=1))
    outcomes = _run_two(importer, ("x", "ET4010", "Chuong-2.pdf"), ("y", "IT3070", "Chuong-2.pdf"))
    assert [outcome.document.chapter_code for outcome in outcomes] == ["ch2", "ch2"]


def test_reservation_is_released_when_the_import_fails(store: AppStore, tmp_path: Path) -> None:
    importer = _importer(store, tmp_path / "files", FakePdfReader(probe_error="OPEN_FAILED"), parties=1)
    with pytest.raises(DocumentImportError):
        importer("hong", filename="Chuong-2.pdf")
    assert len(importer.runtime.chapter_reservations) == 0
    ok = Importer(store, importer.runtime, FakePdfReader(page_count=1), importer.start)
    assert ok("lanh", filename="Chuong-2.pdf").document.chapter_code == "ch2", "chỗ của lần lỗi đã được trả"


def test_reimport_releases_its_reservation(store: AppStore, tmp_path: Path) -> None:
    importer = _importer(store, tmp_path / "files", FakePdfReader(page_count=1), parties=1)
    first = importer("lai", filename="Chuong-2.pdf")
    again = importer("lai", filename="Chuong-2.pdf")
    assert first.created and not again.created and again.document.chapter_code == "ch2"
    assert len(importer.runtime.chapter_reservations) == 0


def test_empty_chapter_code_at_the_service_means_automatic(store: AppStore, tmp_path: Path) -> None:
    importer = _importer(store, tmp_path / "files", FakePdfReader(page_count=1), parties=1)
    outcome = importer("rong", filename="Chuong-4.pdf", chapter_code="")
    assert (outcome.document.chapter_code, outcome.document.chapter_source.code) == ("ch4", "file_name")


def test_subject_lock_is_released_before_the_document_lock_is_taken(store: AppStore, tmp_path: Path) -> None:
    """Thứ tự khoá cố định: khoá môn trước, nhả rồi mới lấy khoá tài liệu (không giữ khoá môn khi giữ khoá tài liệu)."""
    events: list[str] = []

    class RecordingLocks(KeyedLocks):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

        @contextlib.contextmanager
        def hold(self, key: str) -> Iterator[None]:
            events.append(f"+{self.name}")
            with super().hold(key):
                yield
            events.append(f"-{self.name}")

    runtime = ImportRuntime(
        files=OriginalFileStore(tmp_path / "files"),
        queue=HoldingQueue(),
        locks=RecordingLocks("doc"),
        subject_locks=RecordingLocks("subject"),
    )
    Importer(store, runtime, FakePdfReader(page_count=1), threading.Barrier(1))("thu-tu")
    assert events == ["+subject", "-subject", "+doc", "-doc"]
