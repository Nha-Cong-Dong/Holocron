"""Giao thức con trỏ của job đề xuất cặp (`services/concept_proposal_pointer.py`; spec §5.8 "Giao thức con trỏ",
ADR-007 mục 3). LLM là stub; KHÔNG gọi mạng.

Bất biến được kiểm: I3 (trích dẫn là lát cắt nguyên văn của `raw_text` và vẫn qua kiểm nguyên văn), mốc phải duy nhất
(xin mốc dài hơn đúng MỘT lần rồi loại), `term_exact` phải nằm trong span của nó và cùng chunk với định nghĩa, mọi lý do
loại được đếm (tổng = `rejected_count`), nhãn bài tập thiếu/null/xung đột ⇒ cổng `EXERCISE` trượt, LLM không bao giờ
xác nhận cặp.
"""

import unicodedata
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from holocron_core.domain.llm import ChatMessage, LlmUpstreamError
from holocron_core.domain.repositories import AppStore, MissingReferenceError
from holocron_core.domain.schemas import ChunkCategory, KnowledgeChunk, quote_in_text
from holocron_core.domain.vault import ConceptPairStatus, VaultEdit
from holocron_core.infrastructure.llm_config import LlmTaskLimits
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.services import concept_proposal_pointer as pointer_module
from holocron_core.services.concept_proposal import MAX_TERM_CHARS
from holocron_core.services.concept_proposal_pointer import (
    MAX_LIST_ITEMS,
    BatchOutcome,
    build_spans,
    cut_quote,
    run_pointer_batch,
    write_exercise_label,
)
from holocron_core.services.concept_proposal_prompt import POINTER_SYSTEM_PROMPT, build_pointer_messages
from holocron_core.services.vault import update_chunk_labels
from tests.proposal_support import (
    DEF_PROCESS,
    DEF_THREAD,
    EXERCISE_LINE,
    SUBJECT,
    StubClient,
    as_json,
    label,
    pointer,
    reply,
    seed,
    span_of,
)
from tests.vault_support import NOW, STORE_KINDS, open_store


@pytest.fixture
def store() -> AppStore:
    """Kho in-memory cho phần lớn test; test ghi/đọc kho dùng `any_store` (mọi loại kho)."""
    return IngestionStore()


@pytest.fixture(params=STORE_KINDS)
def any_store(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppStore]:
    yield from open_store(str(request.param), tmp_path, monkeypatch)


@pytest.fixture
def chunks(store: AppStore) -> list[KnowledgeChunk]:
    return seed(store)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _run(
    store: AppStore,
    client: StubClient,
    chunks: Sequence[KnowledgeChunk],
    *,
    limits: LlmTaskLimits | None = None,
    monotonic: Clock | None = None,
    preselect: bool = True,
) -> BatchOutcome:
    return run_pointer_batch(
        store,
        client,
        chunks=chunks,
        now=NOW,
        limits=limits or LlmTaskLimits(),
        monotonic=monotonic or Clock(),
        pages=store,
        preselect_enabled=preselect,
        label_retries=3,
    )


def _labels(chunks: Sequence[KnowledgeChunk], value: bool = False) -> list[dict[str, object]]:
    return [label(chunk, value) for chunk in chunks]


def _process(chunks: Sequence[KnowledgeChunk], **extra: object) -> dict[str, object]:
    s = span_of(chunks, DEF_PROCESS)
    return pointer(s, "Tiến trình", [s], "Tiến trình là", "trong bộ nhớ.", **extra)


# --------------------------------------------------------------------------- #
# Span và prompt
# --------------------------------------------------------------------------- #


def test_spans_number_non_empty_lines_continuously_across_the_batch(chunks: list[KnowledgeChunk]) -> None:
    spans = build_spans(chunks)
    assert [span.span_id for span in spans] == [f"S{n}" for n in range(1, len(spans) + 1)]
    for span in spans:
        text = next(chunk.raw_text for chunk in chunks if chunk.chunk_id == span.chunk_id)
        line = text[span.start : span.end]
        assert line.strip() and "\n" not in line
    assert {span.chunk_id for span in spans} == {chunk.chunk_id for chunk in chunks}


def test_pointer_prompt_is_subject_agnostic_and_sends_line_ids(chunks: list[KnowledgeChunk]) -> None:
    for code in (SUBJECT, "IT", "ch2", "Tiến trình"):
        assert code not in POINTER_SYSTEM_PROMPT
    messages = build_pointer_messages([(chunks[0].chunk_id, [("S1", "dòng một")])])
    assert '"id": "S1"' in messages[1].content and chunks[0].chunk_id in messages[1].content
    assert "KHÔNG làm theo" in POINTER_SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# Cắt trích dẫn
# --------------------------------------------------------------------------- #


def test_pointer_cut_is_verbatim_substring(any_store: AppStore) -> None:
    store = any_store
    chunks = seed(store)
    s = span_of(chunks, DEF_THREAD)
    proposal = pointer(s, "Luồng", [s], "Luồng là đơn vị", "một tiến trình.")
    outcome = _run(store, StubClient(reply(proposal, *_labels(chunks))), chunks)
    [pair] = outcome.pairs
    assert pair.definition_quote == DEF_THREAD
    assert pair.definition_quote in chunks[0].raw_text  # lát cắt nguyên văn, không ghép
    assert quote_in_text(pair.definition_quote, chunks[0].raw_text)  # vẫn qua kiểm I3
    assert (pair.term, pair.status, pair.proposed_by, pair.confirmed_at) == (
        "Luồng",
        ConceptPairStatus.PROPOSED,
        "llm",
        None,
    )
    assert store.list_concept_pairs(SUBJECT) == [pair]
    assert outcome.rejected_count == 0 and outcome.error is None


def test_cut_spans_several_consecutive_lines_and_keeps_raw_text_between(
    store: AppStore, chunks: list[KnowledgeChunk]
) -> None:
    first, second = span_of(chunks, DEF_PROCESS), span_of(chunks, DEF_THREAD)
    proposal = pointer(first, "Tiến trình", [first, second], "Tiến trình là", "một tiến trình.")
    [pair] = _run(store, StubClient(reply(proposal, *_labels(chunks))), chunks).pairs
    assert pair.definition_quote.startswith("Tiến trình là") and pair.definition_quote.endswith("một tiến trình.")
    assert quote_in_text(pair.definition_quote, chunks[0].raw_text)


def test_anchor_whitespace_matches_any_whitespace_and_nfd_text_is_cut_from_raw_text() -> None:
    window = unicodedata.normalize("NFD", "Luồng  là\tđơn vị lập lịch nhỏ nhất.")
    span = cut_quote(window, "Luồng là", "lịch nhỏ nhất.")
    assert not isinstance(span, str)
    begin, end = span
    assert (begin, end) == (0, len(window))
    assert cut_quote(window, "  ", "nhất.") == "anchor_not_found"
    assert cut_quote("ab ab cd", "ab", "cd") == "anchor_ambiguous"
    assert cut_quote("ab cd cd", "ab", "cd") == "anchor_ambiguous"
    assert cut_quote("ab cd", "ab", "xy") == "anchor_not_found"
    assert cut_quote("ab cd", "cd", "ab") == "anchor_not_found"  # mốc cuối phải sau mốc đầu


def test_pointer_ambiguous_anchor_rejected_after_one_retry(store: AppStore, chunks: list[KnowledgeChunk]) -> None:
    s = span_of(chunks, DEF_THREAD)
    ambiguous = pointer(s, "Luồng", [s], "nh", "tiến trình.")  # "nh" có hai lần trong dòng ("nhỏ nhất")
    still_ambiguous = as_json({"index": 0, "def_start_words": "nh", "def_end_words": "tiến trình."})
    client = StubClient(reply(ambiguous, _process(chunks), *_labels(chunks)), still_ambiguous)
    outcome = _run(store, client, chunks)
    assert len(client.calls) == 2  # đúng MỘT lần xin mốc dài hơn
    assert "index" in client.calls[1][-1].content and "ambiguous" in client.calls[1][-1].content
    assert outcome.rejected_by_reason == {"anchor_ambiguous": 1}
    assert [pair.term for pair in outcome.pairs] == ["Tiến trình"]
    assert outcome.error is None


def test_longer_anchor_after_the_retry_saves_the_pair(store: AppStore, chunks: list[KnowledgeChunk]) -> None:
    s = span_of(chunks, DEF_THREAD)
    missing = pointer(s, "Luồng", [s], "không có ở đây", "tiến trình.")
    fixed = as_json({"index": 0, "def_start_words": "Luồng là đơn vị", "def_end_words": "tiến trình."})
    client = StubClient(reply(missing, *_labels(chunks)), fixed)
    outcome = _run(store, client, chunks)
    assert [pair.definition_quote for pair in outcome.pairs] == [DEF_THREAD]
    assert "not_found" in client.calls[1][-1].content
    assert outcome.rejected_count == 0


def test_anchor_still_missing_after_the_retry_is_rejected(store: AppStore, chunks: list[KnowledgeChunk]) -> None:
    s = span_of(chunks, DEF_THREAD)
    missing = pointer(s, "Luồng", [s], "không có", "tiến trình.")
    client = StubClient(reply(missing, *_labels(chunks)), as_json())  # LLM không trả mốc mới
    outcome = _run(store, client, chunks)
    assert outcome.pairs == [] and outcome.rejected_by_reason == {"anchor_not_found": 1}
    assert outcome.error is None


def test_anchor_retry_past_the_deadline_fails_the_batch_but_keeps_first_pass_pairs(
    store: AppStore, chunks: list[KnowledgeChunk]
) -> None:
    clock = Clock()
    s = span_of(chunks, DEF_THREAD)
    missing = pointer(s, "Luồng", [s], "không có", "tiến trình.")

    def slow_first(messages: Sequence[ChatMessage]) -> str:
        clock.now = 50.0
        return reply(missing, _process(chunks), *_labels(chunks))

    def late_fix(messages: Sequence[ChatMessage]) -> str:
        clock.now = 101.0
        return as_json({"index": 0, "def_start_words": "Luồng là", "def_end_words": "tiến trình."})

    client = StubClient(slow_first, late_fix)
    outcome = _run(store, client, chunks, limits=LlmTaskLimits(deadline_s=100.0), monotonic=clock)
    assert outcome.error == "deadline" and outcome.stats.deadline_exceeded
    assert [pair.term for pair in outcome.pairs] == ["Tiến trình"]  # cặp hợp lệ của lượt đầu vẫn lưu
    assert outcome.rejected_by_reason == {"anchor_not_found": 1}


def test_anchor_retry_is_not_started_once_the_deadline_is_reached(
    store: AppStore, chunks: list[KnowledgeChunk]
) -> None:
    clock = Clock()
    s = span_of(chunks, DEF_THREAD)

    def at_deadline(messages: Sequence[ChatMessage]) -> str:
        clock.now = 100.0
        return reply(pointer(s, "Luồng", [s], "không có", "tiến trình."), *_labels(chunks))

    client = StubClient(at_deadline)
    outcome = _run(store, client, chunks, limits=LlmTaskLimits(deadline_s=100.0), monotonic=clock)
    assert len(client.calls) == 1 and outcome.error == "deadline"


def test_anchor_retry_shares_the_json_retry_budget_of_the_batch(store: AppStore, chunks: list[KnowledgeChunk]) -> None:
    s = span_of(chunks, DEF_THREAD)
    missing = pointer(s, "Luồng", [s], "không có", "tiến trình.")
    # Ngân sách 1 lần gọi lại JSON đã dùng ở lượt đầu, nên phản hồi mốc hỏng không được gọi lại nữa.
    client = StubClient("hỏng", reply(missing, _process(chunks), *_labels(chunks)), "vẫn hỏng")
    outcome = _run(store, client, chunks, limits=LlmTaskLimits(json_retry_max=1))
    assert len(client.calls) == 3 and outcome.error == "format"
    assert [pair.term for pair in outcome.pairs] == ["Tiến trình"]
    assert outcome.rejected_by_reason == {"anchor_not_found": 1}


def test_anchor_retry_upstream_error_keeps_first_pass_pairs(store: AppStore, chunks: list[KnowledgeChunk]) -> None:
    s = span_of(chunks, DEF_THREAD)
    missing = pointer(s, "Luồng", [s], "không có", "tiến trình.")
    client = StubClient(reply(missing, _process(chunks), *_labels(chunks)), LlmUpstreamError("lỗi", attempts=2))
    outcome = _run(store, client, chunks)
    assert outcome.error == "llm_upstream" and [pair.term for pair in outcome.pairs] == ["Tiến trình"]
    assert outcome.stats.http_attempts == 3


# --------------------------------------------------------------------------- #
# Span và term
# --------------------------------------------------------------------------- #


def test_term_exact_must_be_in_span(store: AppStore, chunks: list[KnowledgeChunk]) -> None:
    s = span_of(chunks, DEF_THREAD)
    heading = span_of(chunks, "Khái niệm cơ bản")
    outside = pointer(s, "Tiến trình con", [s], "Luồng là", "tiến trình.")  # không có trong dòng S
    other_line = pointer(heading, "Luồng", [s], "Luồng là", "tiến trình.")  # có trong chunk, không có trong span
    nfd = pointer(s, unicodedata.normalize("NFD", "Luồng"), [s], "Luồng là", "tiến trình.")
    outcome = _run(store, StubClient(reply(outside, other_line, nfd, *_labels(chunks))), chunks)
    assert outcome.rejected_by_reason == {"term_not_in_span": 2}
    [pair] = outcome.pairs
    assert pair.term == "Luồng"  # NFD vẫn khớp sau NFC


def test_term_span_cross_chunk_rejected(store: AppStore, chunks: list[KnowledgeChunk]) -> None:
    s = span_of(chunks, DEF_THREAD)
    exercise = span_of(chunks, EXERCISE_LINE)
    proposal = pointer(exercise, "luồng", [s], "Luồng là", "tiến trình.")
    outcome = _run(store, StubClient(reply(proposal, *_labels(chunks))), chunks)
    assert outcome.pairs == [] and outcome.rejected_by_reason == {"term_span_cross_chunk": 1}


def test_unknown_and_non_contiguous_spans_are_rejected(store: AppStore, chunks: list[KnowledgeChunk]) -> None:
    process, thread = span_of(chunks, DEF_PROCESS), span_of(chunks, DEF_THREAD)
    heading, exercise = span_of(chunks, "Khái niệm cơ bản"), span_of(chunks, EXERCISE_LINE)
    items = [
        pointer("S999", "Luồng", [thread], "Luồng là", "tiến trình."),
        pointer(thread, "Luồng", ["S999"], "Luồng là", "tiến trình."),
        pointer(thread, "Luồng", [], "Luồng là", "tiến trình."),
        pointer(heading, "Khái niệm", [heading, thread], "Khái", "tiến trình."),  # bỏ cách một dòng
        pointer(thread, "Luồng", [thread, process], "Luồng", "bộ nhớ."),  # giảm dần
        pointer(thread, "Luồng", [thread, span_of(chunks, "Bài tập")], "Luồng", "Bài tập"),  # khác chunk
        pointer(exercise, "luồng", [exercise], "Hãy nêu", "cụ thể."),  # hợp lệ
    ]
    outcome = _run(store, StubClient(reply(*items, *_labels(chunks))), chunks)
    assert outcome.rejected_by_reason == {"unknown_span": 3, "span_cross_chunk": 3}
    assert [pair.term for pair in outcome.pairs] == ["luồng"]


def test_rejected_by_reason_counts_every_code_and_sums_to_rejected_count(
    store: AppStore, chunks: list[KnowledgeChunk]
) -> None:
    s = span_of(chunks, DEF_THREAD)
    good = pointer(s, "Luồng", [s], "Luồng là", "tiến trình.")
    short = pointer(s, "Luồng", [s], "Luồng", "Luồng")  # trích dẫn < 8 ký tự
    items = [good, good, short, *[_process(chunks)] * 3]
    limits = LlmTaskLimits(max_proposals_per_chunk=2)  # trần 4 phần tử cho 2 chunk
    outcome = _run(store, StubClient(reply(*items, *_labels(chunks))), chunks, limits=limits)
    assert outcome.rejected_by_reason == {"proposal_limit": 2, "duplicate": 1, "invalid_quote": 1}
    assert outcome.rejected_count == 4 and len(outcome.pairs) == 2


def test_a_malformed_element_makes_the_whole_response_a_format_error(
    store: AppStore, chunks: list[KnowledgeChunk]
) -> None:
    bad = {"term_span_id": "S1", "term_exact": "x"}  # thiếu field
    outcome = _run(store, StubClient(reply(bad), reply(bad)), chunks, limits=LlmTaskLimits(json_retry_max=1))
    assert outcome.error == "format" and outcome.pairs == [] and outcome.stats.json_errors == 2


def test_llm_status_field_is_ignored_and_pairs_are_never_confirmed(
    store: AppStore, chunks: list[KnowledgeChunk]
) -> None:
    proposal = _process(chunks, status="confirmed", confirmed_at="2026-10-31T00:00:00Z")
    [pair] = _run(store, StubClient(reply(proposal, *_labels(chunks))), chunks).pairs
    assert pair.status is ConceptPairStatus.PROPOSED and pair.confirmed_at is None


def test_pairs_whose_chunk_disappears_before_saving_are_counted(
    store: AppStore, chunks: list[KnowledgeChunk], monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(*args: object, **kwargs: object) -> None:
        raise MissingReferenceError("không tìm thấy chunk")

    monkeypatch.setattr(store, "add_concept_pair", missing)
    outcome = _run(store, StubClient(reply(_process(chunks), *_labels(chunks))), chunks)
    assert outcome.pairs == [] and outcome.rejected_by_reason == {"unknown_chunk": 1}


# --------------------------------------------------------------------------- #
# Nhãn bài tập
# --------------------------------------------------------------------------- #


def test_exercise_labels_are_written_and_counted(any_store: AppStore) -> None:
    store = any_store
    chunks = seed(store)
    theory, exercise = chunks
    response = reply(_process(chunks), label(theory, False), label(exercise, True))
    outcome = _run(store, StubClient(response), chunks)
    stored_theory, stored_exercise = (store.get_chunk(chunk.chunk_id) for chunk in (theory, exercise))
    assert stored_theory is not None and stored_theory.is_exercise is False
    assert stored_exercise is not None and stored_exercise.is_exercise is True
    assert outcome.exercise_chunks == 1
    for chunk in chunks:
        current = store.get_chunk(chunk.chunk_id)
        assert current is not None and current.version == chunk.version + 1
        assert current.label_status == chunk.label_status and current.category == chunk.category
    [pair] = outcome.pairs
    assert "EXERCISE" not in pair.failed_gates


@pytest.mark.parametrize(
    "labels",
    [
        pytest.param(lambda theory: [], id="missing"),
        pytest.param(lambda theory: [label(theory, None)], id="null"),
        pytest.param(lambda theory: [label(theory, False), label(theory, True)], id="conflict"),
        pytest.param(lambda theory: [label(theory, False), label(theory, None)], id="null-and-value"),
    ],
)
def test_missing_null_or_conflicting_exercise_label_fails_the_exercise_gate(
    store: AppStore, chunks: list[KnowledgeChunk], labels: object
) -> None:
    theory = chunks[0]
    assert callable(labels)
    outcome = _run(store, StubClient(reply(_process(chunks), *labels(theory))), chunks)
    [pair] = outcome.pairs
    assert "EXERCISE" in pair.failed_gates and not pair.pre_selected
    current = store.get_chunk(theory.chunk_id)
    assert current is not None and current.is_exercise is None and current.version == theory.version


def test_undetermined_label_overwrites_an_older_label_with_null(any_store: AppStore) -> None:
    """Spec §5.8: thiếu/null/xung đột ⇒ LƯU null, kể cả khi chunk đang có nhãn false từ job trước."""
    store = any_store
    chunks = seed(store)
    theory = chunks[0]
    _run(store, StubClient(reply(*_labels(chunks))), chunks)
    labelled = store.get_chunk(theory.chunk_id)
    assert labelled is not None and labelled.is_exercise is False
    [pair] = _run(store, StubClient(reply(_process(chunks), label(theory, False), label(theory, True))), chunks).pairs
    current = store.get_chunk(theory.chunk_id)
    assert current is not None and current.is_exercise is None and current.version == labelled.version + 1
    assert "EXERCISE" in pair.failed_gates


def test_is_exercise_on_a_pair_element_is_not_a_label(store: AppStore, chunks: list[KnowledgeChunk]) -> None:
    """Chỉ `chunks` mang nhãn: `is_exercise` trên phần tử của `pairs` bị bỏ qua (không bỏ phiếu cho chunk)."""
    theory, exercise = chunks
    response = reply(_process(chunks, is_exercise=False), label(exercise, False))
    [pair] = _run(store, StubClient(response), chunks).pairs
    assert "EXERCISE" in pair.failed_gates  # chunk lý thuyết không có nhãn trong `chunks`
    current = store.get_chunk(theory.chunk_id)
    assert current is not None and current.is_exercise is None


def test_labels_for_chunks_outside_the_batch_are_ignored(store: AppStore, chunks: list[KnowledgeChunk]) -> None:
    outcome = _run(
        store, StubClient(reply(*_labels(chunks), {"chunk_id": "chk_ngoai_lo", "is_exercise": True})), chunks
    )
    assert outcome.exercise_chunks == 0 and store.get_chunk("chk_ngoai_lo") is None


def test_unchanged_label_is_not_rewritten(store: AppStore, chunks: list[KnowledgeChunk]) -> None:
    _run(store, StubClient(reply(*_labels(chunks))), chunks)
    versions = [store.get_chunk(chunk.chunk_id) for chunk in chunks]
    _run(store, StubClient(reply(*_labels(chunks))), chunks)
    assert [store.get_chunk(chunk.chunk_id) for chunk in chunks] == versions  # cùng nhãn ⇒ không tăng version


def test_exercise_chunk_fails_the_exercise_gate(store: AppStore, chunks: list[KnowledgeChunk]) -> None:
    theory, exercise = chunks
    s = span_of(chunks, EXERCISE_LINE)
    proposal = pointer(s, "luồng", [s], "Hãy nêu", "cụ thể.")
    outcome = _run(store, StubClient(reply(proposal, label(theory, False), label(exercise, True))), chunks)
    [pair] = outcome.pairs
    assert "EXERCISE" in pair.failed_gates and not pair.pre_selected


class RacingVault:
    """Vault mà người dùng sửa nhãn (PATCH …/labels) ngay trước lần ghi nhãn bài tập đầu tiên."""

    def __init__(self, store: AppStore, races: int) -> None:
        self.store = store
        self.races = races

    def get_chunk(self, chunk_id: str) -> KnowledgeChunk | None:
        return self.store.get_chunk(chunk_id)

    def set_chunk_exercise(self, chunk_id: str, is_exercise: bool | None, expected_version: int) -> KnowledgeChunk:
        if self.races > 0:
            self.races -= 1
            current = self.store.get_chunk(chunk_id)
            assert current is not None
            update_chunk_labels(
                self.store,
                chunk_id,
                expected_version=current.version,
                chapter_code=None,
                category=ChunkCategory.PRACTICE,
                topic_keys=None,
                edit=VaultEdit(editor_id="nguoi_dung", at=NOW),
            )
        return self.store.set_chunk_exercise(chunk_id, is_exercise, expected_version)


def test_exercise_label_write_rereads_after_a_concurrent_label_patch(any_store: AppStore) -> None:
    store = any_store
    chunks = seed(store)
    theory = chunks[0]
    racing = RacingVault(store, races=1)
    assert write_exercise_label(racing, theory.chunk_id, True, retries=3)
    current = store.get_chunk(theory.chunk_id)
    assert current is not None and current.is_exercise is True
    assert (
        current.category is ChunkCategory.PRACTICE and current.label_status == "confirmed"
    )  # nhãn người sửa giữ nguyên
    assert current.version == theory.version + 2


def test_exercise_label_write_gives_up_after_the_retry_limit(store: AppStore, chunks: list[KnowledgeChunk]) -> None:
    theory = chunks[0]
    racing = RacingVault(store, races=10)
    assert not write_exercise_label(racing, theory.chunk_id, True, retries=2)
    current = store.get_chunk(theory.chunk_id)
    assert current is not None and current.is_exercise is None and current.category is ChunkCategory.PRACTICE


def test_label_that_cannot_be_written_fails_the_exercise_gate(
    store: AppStore, chunks: list[KnowledgeChunk], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pointer_module, "write_exercise_label", lambda *args, **kwargs: False)
    [pair] = _run(store, StubClient(reply(_process(chunks), *_labels(chunks))), chunks).pairs
    assert "EXERCISE" in pair.failed_gates


def test_exercise_label_of_a_missing_chunk_is_not_written(store: AppStore) -> None:
    assert not write_exercise_label(store, "chk_khong_co", True, retries=1)


# --------------------------------------------------------------------------- #
# Định dạng phản hồi và biên (đột biến còn sống ở /finish-run)
# --------------------------------------------------------------------------- #


def test_response_must_be_an_object_with_chunks_and_pairs(store: AppStore, chunks: list[KnowledgeChunk]) -> None:
    mixed_array = as_json(_process(chunks), *_labels(chunks))  # dạng mảng trộn cũ: không còn hợp lệ
    outcome = _run(store, StubClient(mixed_array, mixed_array), chunks, limits=LlmTaskLimits(json_retry_max=1))
    assert outcome.error == "format" and outcome.stats.json_errors == 2
    wrong_label = reply(_process(chunks), {"chunk_id": chunks[0].chunk_id, "is_exercise": "có"})
    assert _run(store, StubClient(wrong_label), chunks, limits=LlmTaskLimits(json_retry_max=0)).error == "format"
    assert _run(store, StubClient('```json\n{"chunks": [], "pairs": []}\n```'), chunks).error is None


def test_whitespace_only_lines_are_not_spans() -> None:
    store = IngestionStore()
    [chunk] = seed(store)[:1]
    spaced = chunk.model_copy(update={"raw_text": "Dòng một\n   \t\nDòng hai"})
    spans = build_spans([spaced])
    assert [spaced.raw_text[span.start : span.end] for span in spans] == ["Dòng một", "Dòng hai"]
    assert [span.span_id for span in spans] == ["S1", "S2"]


def test_def_span_list_length_is_bounded(store: AppStore, chunks: list[KnowledgeChunk]) -> None:
    s = span_of(chunks, DEF_THREAD)
    at_limit = pointer(s, "Luồng", [s] * MAX_LIST_ITEMS, "Luồng là", "tiến trình.")
    outcome = _run(store, StubClient(reply(at_limit, *_labels(chunks))), chunks)
    assert outcome.error is None and outcome.rejected_by_reason == {"span_cross_chunk": 1}
    over = pointer(s, "Luồng", [s] * (MAX_LIST_ITEMS + 1), "Luồng là", "tiến trình.")
    limits = LlmTaskLimits(json_retry_max=0)
    assert _run(store, StubClient(reply(over, *_labels(chunks))), chunks, limits=limits).error == "format"


def test_anchor_given_in_nfd_matches_an_nfc_window() -> None:
    window = "Luồng là đơn vị lập lịch nhỏ nhất."
    nfd = unicodedata.normalize("NFD", "Luồng là")
    assert nfd != "Luồng là"
    assert cut_quote(window, nfd, unicodedata.normalize("NFD", "nhỏ nhất.")) == (0, len(window))


def test_anchor_ending_inside_a_combining_cluster_is_not_cut() -> None:
    window = "ab x\u0301 cd"  # "x" + dấu sắc kết hợp: NFC không gộp được, cụm dài hai điểm mã
    assert cut_quote(window, "ab", "x") == "anchor_not_found"
    assert cut_quote(window, "ab", "cd") == (0, len(window))


def test_end_anchor_starting_before_the_start_anchor_does_not_count() -> None:
    assert cut_quote("abcd xyz", "bc", "abcd") == "anchor_not_found"
    assert cut_quote("abcd xyz", "bc", "cd") == (1, 4)


def test_term_length_limit_is_inclusive(store: AppStore) -> None:
    longest = "T" * MAX_TERM_CHARS
    chunks = seed(store)
    line = f"{longest}x là một khái niệm dài để kiểm giới hạn độ dài."
    theory = chunks[0].model_copy(update={"raw_text": line})
    batch = [theory]
    s = span_of(batch, line)
    ok = pointer(s, longest, [s], "là một", "độ dài.")
    too_long = pointer(s, longest + "x", [s], "là một", "độ dài.")
    response = reply(ok, too_long, label(theory, False))
    outcome = run_pointer_batch(
        _QuoteTrustingVault(store),
        StubClient(response),
        chunks=batch,
        now=NOW,
        limits=LlmTaskLimits(),
        monotonic=Clock(),
        pages=store,
        preselect_enabled=False,
        label_retries=0,
    )
    assert outcome.rejected_by_reason == {"invalid_term": 1}
    assert [len(pair.term) for pair in outcome.pairs] == [MAX_TERM_CHARS]


class _QuoteTrustingVault:
    """Vault giả cho test biên `term`: chunk dựng trong bộ nhớ (không ghi kho), ghi cặp vào danh sách."""

    def __init__(self, store: AppStore) -> None:
        self.store = store
        self.saved: list[object] = []

    def get_chunk(self, chunk_id: str) -> KnowledgeChunk | None:
        return self.store.get_chunk(chunk_id)

    def set_chunk_exercise(self, chunk_id: str, is_exercise: bool | None, expected_version: int) -> KnowledgeChunk:
        return self.store.set_chunk_exercise(chunk_id, is_exercise, expected_version)

    def list_concept_pairs(self, subject_code: str) -> list[object]:
        return []

    def add_concept_pair(self, pair: object) -> object:
        self.saved.append(pair)
        return pair


def test_exactly_cap_proposals_has_no_proposal_limit(store: AppStore, chunks: list[KnowledgeChunk]) -> None:
    s = span_of(chunks, DEF_THREAD)
    items = [pointer(s, "Luồng", [s], "Luồng là", "tiến trình.")] * 2  # trần = 1 × 2 chunk
    outcome = _run(
        store, StubClient(reply(*items, *_labels(chunks))), chunks, limits=LlmTaskLimits(max_proposals_per_chunk=1)
    )
    assert "proposal_limit" not in outcome.rejected_by_reason
    assert outcome.rejected_by_reason == {"duplicate": 1}


def test_upstream_failure_on_the_first_call_counts_the_call(store: AppStore, chunks: list[KnowledgeChunk]) -> None:
    outcome = _run(store, StubClient(LlmUpstreamError("lỗi", attempts=3)), chunks)
    assert outcome.error == "llm_upstream"
    assert (outcome.stats.llm_calls, outcome.stats.http_attempts) == (1, 3)
