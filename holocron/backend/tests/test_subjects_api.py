"""Môn học (SPECIFICATION §5.14 `Subject`, §6.8; §5.13 bảng `subjects`; brief B5a mục 3).

- Bốn route đúng bảng §6.8 (status, lỗi, `Location`, optimistic locking) trên ba loại kho.
- Migration `0003_subjects.sql`: môn của dữ liệu tạo trước đó được liệt kê, và `GET` không ghi gì.
- Tạo ngầm (`display_name = subject_code`, `exam_format = mixed`) khi `POST /v1/documents` hay `POST /v1/blueprints`
  gặp môn chưa có, chỉ khi request thành công; request lỗi (4xx/5xx) không để lại môn nào.
"""

import sqlite3
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from holocron_core.domain.repositories import AppStore
from holocron_core.infrastructure.storage import SQLiteStore
from holocron_core.infrastructure.storage.connection import connect
from holocron_core.infrastructure.storage.migrator import current_version, load_migrations, migrate
from holocron_core.routers.api import app, get_clock, get_store
from holocron_core.services.document_import import SynchronousImportQueue
from tests.document_import_support import FakePdfReader, ImportEnv, install, pdf_bytes
from tests.test_document_import_failures import RefusingQueue
from tests.test_theory_twins import STORE_KINDS, Clock, close_kind, open_kind

SUBJECT = "ET4010"
OTHER = "SSH1110"


@pytest.fixture(params=STORE_KINDS)
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[AppStore]:
    opened = open_kind(str(request.param), tmp_path)
    app.dependency_overrides[get_store] = lambda: opened
    yield opened
    app.dependency_overrides.clear()
    close_kind(opened)


@pytest.fixture
def clock() -> Clock:
    fake = Clock()
    app.dependency_overrides[get_clock] = lambda: fake
    return fake


def _create(client: TestClient, **body: Any) -> Any:
    return client.post("/v1/subjects", json={"subject_code": SUBJECT, **body})


def _patch(client: TestClient, subject_code: str = SUBJECT, **body: Any) -> Any:
    return client.patch(f"/v1/subjects/{subject_code}", json={"editor_id": "local_user", **body})


# --------------------------------------------------------------------------- #
# Bốn route của §6.8
# --------------------------------------------------------------------------- #


def test_create_returns_201_with_location_and_defaults(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    response = _create(client)
    assert response.status_code == 201, response.text
    assert response.headers["Location"] == f"/v1/subjects/{SUBJECT}"
    body = response.json()
    assert body == {
        "subject_code": SUBJECT,
        "display_name": SUBJECT,
        "exam_format": "mixed",
        "version": 1,
        "created_at": body["created_at"],
        "updated_at": body["created_at"],
    }
    assert body["created_at"].startswith("2026-10-28T08:00:00")
    assert client.get(f"/v1/subjects/{SUBJECT}").json() == body


def test_create_keeps_the_given_display_name_and_exam_format(store: AppStore, clock: Clock) -> None:
    response = _create(TestClient(app), display_name="Vi xử lý", exam_format="essay")
    assert response.status_code == 201
    assert (response.json()["display_name"], response.json()["exam_format"]) == ("Vi xử lý", "essay")


def test_list_is_sorted_by_subject_code(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    assert client.get("/v1/subjects").json() == []
    for code in ("SSH1110", "ET4010", "IT3070"):
        assert client.post("/v1/subjects", json={"subject_code": code}).status_code == 201
    assert [subject["subject_code"] for subject in client.get("/v1/subjects").json()] == [
        "ET4010",
        "IT3070",
        "SSH1110",
    ]


def test_creating_an_existing_subject_is_409(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    first = _create(client, display_name="Vi xử lý")
    second = _create(client, display_name="Tên khác")
    assert second.status_code == 409
    assert client.get(f"/v1/subjects/{SUBJECT}").json() == first.json()


def test_creating_an_implicitly_created_subject_is_409(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    created = client.post("/v1/blueprints", json={"subject_code": SUBJECT, "theory_slots": [{"chapter_code": "ch1"}]})
    assert created.status_code == 201
    assert _create(client, display_name="Vi xử lý").status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"subject_code": "et4010"},
        {"subject_code": "ET4010", "display_name": ""},
        {"subject_code": "ET4010", "display_name": "x" * 201},
        {"subject_code": "ET4010", "exam_format": "oral"},
        {"subject_code": "ET4010", "version": 3},
        {},
    ],
)
def test_invalid_create_body_is_422_and_writes_nothing(store: AppStore, clock: Clock, body: dict[str, Any]) -> None:
    client = TestClient(app)
    assert client.post("/v1/subjects", json=body).status_code == 422
    assert client.get("/v1/subjects").json() == []


def test_get_unknown_subject_is_404_and_bad_code_is_422(store: AppStore) -> None:
    client = TestClient(app)
    assert client.get(f"/v1/subjects/{SUBJECT}").status_code == 404
    assert client.get("/v1/subjects/et-4010").status_code == 422


def test_patch_updates_fields_and_bumps_the_version(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    created = _create(client).json()
    clock.now += timedelta(hours=2)
    renamed = _patch(client, expected_version=1, display_name="Vi xử lý")
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["display_name"] == "Vi xử lý" and renamed.json()["exam_format"] == "mixed"
    assert renamed.json()["version"] == 2
    assert renamed.json()["created_at"] == created["created_at"]
    assert renamed.json()["updated_at"].startswith("2026-10-28T10:00:00")
    reformatted = _patch(client, expected_version=2, exam_format="essay")
    assert reformatted.status_code == 200
    assert (reformatted.json()["display_name"], reformatted.json()["exam_format"]) == ("Vi xử lý", "essay")
    assert reformatted.json()["version"] == 3
    assert client.get(f"/v1/subjects/{SUBJECT}").json() == reformatted.json()


def test_patch_with_a_stale_version_is_409_and_changes_nothing(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    _create(client)
    current = _patch(client, expected_version=1, exam_format="mcq").json()
    stale = _patch(client, expected_version=1, display_name="Ghi đè")
    assert stale.status_code == 409
    assert stale.json()["detail"] == "version lệch: hiện tại 2"
    assert client.get(f"/v1/subjects/{SUBJECT}").json() == current


@pytest.mark.parametrize(
    "body",
    [
        {"expected_version": 1},
        {"expected_version": 0, "display_name": "Vi xử lý"},
        {"display_name": "Vi xử lý"},
        {"expected_version": 1, "display_name": ""},
        {"expected_version": 1, "exam_format": "oral"},
        {"expected_version": 1, "display_name": "Vi xử lý", "version": 9},
    ],
)
def test_invalid_patch_is_422(store: AppStore, clock: Clock, body: dict[str, Any]) -> None:
    client = TestClient(app)
    created = _create(client).json()
    assert _patch(client, **body).status_code == 422
    assert client.patch(f"/v1/subjects/{SUBJECT}", json={"expected_version": 1, "exam_format": "mcq"}).status_code == (
        422
    ), "thiếu editor_id"
    assert client.get(f"/v1/subjects/{SUBJECT}").json() == created


def test_patch_unknown_subject_is_404_and_bad_code_is_422(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    assert _patch(client, expected_version=1, exam_format="mcq").status_code == 404
    assert _patch(client, "et-4010", expected_version=1, exam_format="mcq").status_code == 422


# --------------------------------------------------------------------------- #
# Migration 0003: môn của dữ liệu cũ
# --------------------------------------------------------------------------- #


def _database_at_version_2(path: Path) -> None:
    """DB tạo trước migration 0003, có `subject_code` ở `documents`, `chunks` và `blueprints`."""
    conn = connect(path)
    try:
        migrate(conn, load_migrations()[:2])
        assert current_version(conn) == 2
        stamp = "2026-10-20T08:00:00.000000+00:00"
        conn.executemany(
            "INSERT INTO documents (document_id, sha256, filename, media_type, size_bytes, subject_code, "
            "uploaded_at) VALUES (?, ?, 'a.pdf', 'application/pdf', 10, ?, ?)",
            [
                ("doc_aaaaaaaaaaaaaaaa", "a" * 64, "ET4010", stamp),
                ("doc_bbbbbbbbbbbbbbbb", "b" * 64, "ET4010", stamp),
                ("doc_cccccccccccccccc", "c" * 64, None, stamp),
            ],
        )
        conn.execute(
            "INSERT INTO chunks (chunk_id, document_id, subject_code, chapter_code, category, page_number, raw_text, "
            "verbatim_quote, tags_json, linked_chunk_ids_json, token_count) "
            "VALUES ('chk_1', 'doc_aaaaaaaaaaaaaaaa', 'SSH1110', 'ch1', 'THEORY', 1, 'x', 'x', '[]', '[]', 1)"
        )
        conn.execute(
            "INSERT INTO blueprints (blueprint_id, subject_code, blueprint_json, created_at) "
            "VALUES ('bp_old', 'IT3070', '{}', ?)",
            (stamp,),
        )
    finally:
        conn.close()


def _subject_rows(path: Path) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(path)
    try:
        return [tuple(row) for row in conn.execute("SELECT * FROM subjects ORDER BY subject_code")]
    finally:
        conn.close()


def test_migration_lists_subjects_of_existing_data_and_get_does_not_write(tmp_path: Path) -> None:
    path = tmp_path / "holocron.sqlite3"
    _database_at_version_2(path)
    opened = SQLiteStore.open(path)
    app.dependency_overrides[get_store] = lambda: opened
    try:
        after_migration = _subject_rows(path)
        listed = TestClient(app).get("/v1/subjects")
        assert listed.status_code == 200
        assert [
            (subject["subject_code"], subject["display_name"], subject["exam_format"], subject["version"])
            for subject in listed.json()
        ] == [("ET4010", "ET4010", "mixed", 1), ("IT3070", "IT3070", "mixed", 1), ("SSH1110", "SSH1110", "mixed", 1)]
        assert TestClient(app).get("/v1/subjects/IT3070").status_code == 200
        assert _subject_rows(path) == after_migration, "GET không được ghi"
    finally:
        app.dependency_overrides.clear()
        opened.close()


# --------------------------------------------------------------------------- #
# Tạo ngầm: chỉ khi request thành công
# --------------------------------------------------------------------------- #


def test_blueprint_errors_create_no_subject(store: AppStore, clock: Clock) -> None:
    client = TestClient(app)
    bad = [
        {"subject_code": SUBJECT, "theory_slots": [{"chapter_code": "chuong1"}]},
        {"subject_code": SUBJECT, "item_ids": ["ing_a"], "theory_slots": [{"chapter_code": "ch1"}]},
        {"subject_code": SUBJECT, "theory_slots": [{"chapter_code": "ch1", "source_chunk_id": "chk_khong_co"}]},
        {"subject_code": SUBJECT, "theory_slots": [{"chapter_code": "ch1", "format": "ESSAY"}]},
    ]
    assert [client.post("/v1/blueprints", json=body).status_code for body in bad] == [422, 422, 422, 409]
    assert client.get("/v1/subjects").json() == []


@pytest.fixture(params=STORE_KINDS)
def env(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ImportEnv]:
    opened = open_kind(str(request.param), tmp_path)
    yield install(opened, tmp_path / "files", SynchronousImportQueue(), FakePdfReader())
    app.dependency_overrides.clear()
    close_kind(opened)


def test_document_import_creates_the_subject_implicitly(env: ImportEnv) -> None:
    response = env.post(pdf_bytes("mon-moi"), subject_code=OTHER)
    assert response.status_code == 202, response.text
    subject = env.store.get_subject(OTHER)
    assert subject is not None
    assert (subject.display_name, subject.exam_format.value, subject.version) == (OTHER, "mixed", 1)
    assert subject.created_at == env.store.list_imported_documents(OTHER)[0].meta.uploaded_at


def test_document_import_keeps_an_existing_subject(env: ImportEnv) -> None:
    created = env.client.post(
        "/v1/subjects", json={"subject_code": OTHER, "display_name": "Triết", "exam_format": "essay"}
    )
    assert created.status_code == 201
    assert env.post(pdf_bytes("mon-cu"), subject_code=OTHER).status_code == 202
    assert env.client.get(f"/v1/subjects/{OTHER}").json() == created.json()


def test_document_import_errors_create_no_subject(env: ImportEnv) -> None:
    assert env.post(b"khong phai pdf", subject_code=OTHER).status_code == 415
    assert env.post(b"", subject_code=OTHER).status_code in (400, 422)
    assert env.post(pdf_bytes("sai-chuong"), subject_code=OTHER, chapter_code="chuong3").status_code == 422
    assert env.store.list_subjects() == []


def test_rejected_queue_5xx_removes_the_implicit_subject(env: ImportEnv) -> None:
    refusing = install(env.store, env.files.root, env.queue, env.reader, runtime_queue=RefusingQueue())
    response = refusing.post(pdf_bytes("hang-doi-dong"), subject_code=OTHER)
    assert response.status_code >= 500
    assert env.store.list_subjects() == [], "request lỗi không để lại môn tạo ngầm"
    assert env.store.list_imported_documents(OTHER) == []


def test_rejected_queue_keeps_a_subject_that_existed_before(env: ImportEnv) -> None:
    assert env.post(pdf_bytes("tai-lieu-dau"), subject_code=OTHER).status_code == 202
    before = env.store.list_subjects()
    refusing = install(env.store, env.files.root, env.queue, env.reader, runtime_queue=RefusingQueue())
    assert refusing.post(pdf_bytes("tai-lieu-hai"), subject_code=OTHER).status_code >= 500
    assert env.store.list_subjects() == before
