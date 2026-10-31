"""Job nền đề xuất cặp khái niệm (spec §5.8 "Job nền đề xuất cặp", §6.6; ADR-007 mục 3).

- **Chạy:** chunk của job chia thành lô ≤ `job_batch_chunks`; các lô vào MỘT hàng đợi FIFO của runtime và chạy trên
  tối đa `job_max_parallel` luồng cho cả app, nên job tạo trước chạy trước. Mỗi lô gọi `run_pointer_batch` với cùng
  `LlmTaskLimits` như endpoint đồng bộ và một client LLM mới (cận tổng thời gian của client tính từ lúc dựng). Lỗi
  một lô (LLM lỗi, hết hạn, JSON hỏng sau mọi lần thử, hay lỗi bất ngờ) chỉ tăng `batches_failed`, không làm hỏng
  lô khác.
- **Huỷ:** đặt `cancel_requested`; lô chưa bắt đầu bị bỏ khỏi hàng đợi; lô đang chạy chạy hết và cặp của nó vẫn được
  lưu; job thành `cancelled` khi không còn lô nào chạy. Huỷ job đã kết thúc không đổi gì.
- **Trạng thái:** `succeeded` = mọi lô xong không lỗi; `partial` = có lô lỗi và có lô xong; `failed` = mọi lô lỗi;
  `cancelled` = người dùng huỷ trước khi hết lô.
- **Lưu:** chỉ trong bộ nhớ của runtime, do `lifespan` của app tạo và đóng (như `ImportRuntime`) rồi cấp cho router
  qua `Depends`; tắt app thì mất job, cặp đã lưu vẫn còn. Giữ tối đa `job_keep_finished` job đã kết thúc (cũ nhất bị
  bỏ trước). Hàng đợi không có trần số job (spec §5.8).
- **Cấu hình:** một nguồn duy nhất là `ConceptProposalSettings` (`config.llm.proposal`): runtime nhận nó lúc tạo
  (song song, số job giữ lại), mỗi job nhận bản của request (cỡ lô, tích sẵn, số lần ghi lại nhãn).
- **Lỗi khi cộng kết quả một lô:** chỉ lô đó tính là lô lỗi; job chạy tiếp các lô còn lại; luồng chạy không chết.
  Job kết thúc mà không bị huỷ thì `batches_done + batches_failed = batches_total`.
- **Tắt app:** `close` ngừng nhận job, bỏ lô chưa chạy và chờ các luồng trong MỘT hạn chung; lô còn chạy sau khi kho
  đóng gặp lỗi kho và được tính là lô lỗi (không ghi được gì vào kho đã đóng).

Job không gọi mạng khi tạo; LLM KHÔNG BAO GIỜ xác nhận cặp.
"""

from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

from ..domain.llm import ChatCompletionClient
from ..domain.repositories import VaultRepository
from ..domain.schemas import KnowledgeChunk
from ..domain.vault import ProposalJob, ProposalJobStatus
from ..infrastructure.llm_config import ConceptProposalSettings, LlmTaskLimits
from .concept_proposal_gates import PageSource
from .concept_proposal_pointer import BatchOutcome, run_pointer_batch

logger = logging.getLogger(__name__)

ClientFactory = Callable[[], ChatCompletionClient]
TERMINAL_STATUSES: frozenset[ProposalJobStatus] = frozenset({"succeeded", "partial", "failed", "cancelled"})


class ProposalStore(VaultRepository, PageSource, Protocol):
    """Kho mà job dùng: Vault (chunk, cặp) và trang nguồn cho cổng tích sẵn."""


def derive_status(job: ProposalJob) -> ProposalJobStatus:
    """Trạng thái kết thúc của job khi không còn lô nào chạy hay chờ."""
    if job.cancel_requested:
        return "cancelled"
    if job.batches_failed == 0:
        return "succeeded"
    if job.batches_done == 0:
        return "failed"
    return "partial"


@dataclass
class _JobState:
    job: ProposalJob
    store: ProposalStore
    client_factory: ClientFactory
    limits: LlmTaskLimits
    preselect_enabled: bool
    label_retries: int
    running: int = 0


@dataclass
class _Batch:
    job_id: str
    chunk_ids: list[str]


@dataclass
class _Shared:
    """Trạng thái dùng chung của runtime, chỉ đọc/ghi khi giữ `condition`."""

    jobs: dict[str, _JobState] = field(default_factory=dict)
    queue: deque[_Batch] = field(default_factory=deque)
    finished: deque[str] = field(default_factory=deque)
    workers: list[threading.Thread] = field(default_factory=list)
    closed: bool = False


class ProposalJobRuntime:
    """Hàng đợi và luồng chạy job đề xuất cặp của một app; tạo một lần, cấp cho router qua `Depends`."""

    def __init__(
        self,
        settings: ConceptProposalSettings | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings or ConceptProposalSettings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic
        self._condition = threading.Condition()
        self._shared = _Shared()

    @property
    def settings(self) -> ConceptProposalSettings:
        return self._settings

    # ------------------------------------------------------------------ #
    # API cho router
    # ------------------------------------------------------------------ #

    def submit(
        self,
        *,
        subject_code: str,
        chunk_ids: Sequence[str],
        document_id: str | None,
        store: ProposalStore,
        client_factory: ClientFactory,
        limits: LlmTaskLimits,
        now: datetime,
        settings: ConceptProposalSettings | None = None,
        trigger: Literal["document_confirmed", "manual"] = "manual",
    ) -> ProposalJob:
        """Xếp job vào hàng đợi (không gọi mạng); `chunk_ids` đã được kiểm, lặp chỉ tính một lần. `settings` là
        `config.llm.proposal` của request (mặc định: cấu hình của runtime)."""
        unique = list(dict.fromkeys(chunk_ids))
        if not unique:
            raise ValueError("job cần ít nhất một chunk")
        job_settings = settings or self._settings
        size = job_settings.job_batch_chunks
        job = ProposalJob(
            job_id=f"pjob_{uuid.uuid4().hex[:16]}",
            subject_code=subject_code,
            trigger=trigger,
            document_id=document_id,
            status="queued",
            chunk_count=len(unique),
            batch_size=size,
            batches_total=math.ceil(len(unique) / size),
            batches_done=0,
            batches_failed=0,
            proposed_count=0,
            pre_selected_count=0,
            rejected_count=0,
            exercise_chunks=0,
            created_at=now,
        )
        state = _JobState(
            job=job,
            store=store,
            client_factory=client_factory,
            limits=limits,
            preselect_enabled=job_settings.pair_preselect_enabled,
            label_retries=job_settings.label_write_retries,
        )
        with self._condition:
            if self._shared.closed:
                raise RuntimeError("runtime job đề xuất cặp đã đóng")
            self._shared.jobs[job.job_id] = state
            for start in range(0, len(unique), size):
                self._shared.queue.append(_Batch(job.job_id, unique[start : start + size]))
            self._ensure_workers()
            self._condition.notify_all()
        return job.model_copy(deep=True)

    def get(self, job_id: str) -> ProposalJob | None:
        with self._condition:
            state = self._shared.jobs.get(job_id)
            return None if state is None else state.job.model_copy(deep=True)

    def list_jobs(self, subject_code: str) -> list[ProposalJob]:
        """Job của môn, mới nhất trước (theo `created_at`, hoà thì theo thứ tự tạo ngược)."""
        with self._condition:
            states = [state.job for state in self._shared.jobs.values() if state.job.subject_code == subject_code]
        ordered = list(reversed(states))
        ordered.sort(key=lambda job: job.created_at, reverse=True)
        return [job.model_copy(deep=True) for job in ordered]

    def cancel(self, job_id: str) -> ProposalJob | None:
        """Đặt `cancel_requested`; bỏ lô chưa chạy; không còn lô chạy thì kết thúc ngay. Job đã kết thúc giữ nguyên."""
        with self._condition:
            state = self._shared.jobs.get(job_id)
            if state is None:
                return None
            if state.job.status in TERMINAL_STATUSES:
                return state.job.model_copy(deep=True)
            state.job = state.job.model_copy(update={"cancel_requested": True})
            self._shared.queue = deque(batch for batch in self._shared.queue if batch.job_id != job_id)
            if state.running == 0:
                self._finish(state)
            return state.job.model_copy(deep=True)

    def wait_idle(self, timeout: float) -> bool:
        """Chờ tới khi hàng đợi rỗng và không lô nào chạy (dùng cho test và lúc tắt app)."""
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._shared.queue or any(state.running for state in self._shared.jobs.values()):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
        return True

    def close(self, timeout: float = 5.0) -> None:
        """Ngừng nhận job, bỏ lô chưa chạy, chờ mọi luồng chạy xong lô đang dở trong TỔNG `timeout` giây."""
        with self._condition:
            self._shared.closed = True
            self._shared.queue.clear()
            workers = list(self._shared.workers)
            self._condition.notify_all()
        deadline = time.monotonic() + timeout
        for worker in workers:
            worker.join(max(0.0, deadline - time.monotonic()))

    # ------------------------------------------------------------------ #
    # Luồng chạy
    # ------------------------------------------------------------------ #

    def _ensure_workers(self) -> None:
        """Gọi khi giữ khoá: dựng đủ `job_max_parallel` luồng (lười, lần đầu có job)."""
        alive = [worker for worker in self._shared.workers if worker.is_alive()]
        while len(alive) < self._settings.job_max_parallel:
            worker = threading.Thread(target=self._work, name=f"concept-proposal-job-{len(alive)}", daemon=True)
            alive.append(worker)
            worker.start()
        self._shared.workers = alive

    def _next(self) -> tuple[_Batch, _JobState] | None:
        with self._condition:
            while not self._shared.queue and not self._shared.closed:
                self._condition.wait()
            if self._shared.closed:
                return None
            batch = self._shared.queue.popleft()
            state = self._shared.jobs[batch.job_id]
            state.running += 1
            if state.job.status == "queued":
                state.job = state.job.model_copy(update={"status": "running", "started_at": self._clock()})
            return batch, state

    def _work(self) -> None:
        while True:
            taken = self._next()
            if taken is None:
                return
            batch, state = taken
            outcome = self._run_batch(batch, state)
            with self._condition:
                state.running -= 1
                try:
                    self._record(state, outcome)
                except Exception:  # chỉ lô này tính là lỗi; job chạy tiếp, luồng chạy không được chết
                    logger.exception("không cộng được kết quả lô vào job %s", state.job.job_id)
                    state.job = state.job.model_copy(update={"batches_failed": state.job.batches_failed + 1})
                remaining = any(queued.job_id == batch.job_id for queued in self._shared.queue)
                if not remaining and state.running == 0:
                    self._finish(state)
                self._condition.notify_all()

    def _run_batch(self, batch: _Batch, state: _JobState) -> BatchOutcome | None:
        """Kết quả của lô; `None` khi lô lỗi ngoài ba loại lỗi LLM (vẫn chỉ tính là một lô lỗi)."""
        try:
            chunks: list[KnowledgeChunk] = []
            for chunk_id in batch.chunk_ids:
                chunk = state.store.get_chunk(chunk_id)
                if chunk is not None and chunk.subject_code == state.job.subject_code:
                    chunks.append(chunk)
            if not chunks:
                return BatchOutcome()
            return run_pointer_batch(
                state.store,
                state.client_factory(),
                chunks=chunks,
                now=self._clock(),
                limits=state.limits,
                monotonic=self._monotonic,
                pages=state.store,
                preselect_enabled=state.preselect_enabled,
                label_retries=state.label_retries,
            )
        except Exception:  # một lô lỗi không được làm chết luồng hay hỏng lô khác
            logger.exception("lô đề xuất cặp của job %s lỗi ngoài lỗi LLM", state.job.job_id)
            return None

    def _record(self, state: _JobState, outcome: BatchOutcome | None) -> None:
        """Gọi khi giữ khoá: cộng kết quả một lô vào job."""
        job = state.job
        updates: dict[str, object] = {}
        if outcome is None or outcome.error is not None:
            updates["batches_failed"] = job.batches_failed + 1
            if outcome is not None and outcome.error is not None:
                errors = dict(job.batch_errors)
                errors[outcome.error] = errors.get(outcome.error, 0) + 1
                updates["batch_errors"] = errors
        else:
            updates["batches_done"] = job.batches_done + 1
        if outcome is not None:
            reasons = dict(job.rejected_by_reason)
            for reason, count in outcome.rejected_by_reason.items():
                reasons[reason] = reasons.get(reason, 0) + count
            updates["rejected_by_reason"] = reasons
            updates["rejected_count"] = sum(reasons.values())
            updates["proposed_count"] = job.proposed_count + len(outcome.pairs)
            updates["pre_selected_count"] = job.pre_selected_count + sum(pair.pre_selected for pair in outcome.pairs)
            updates["exercise_chunks"] = job.exercise_chunks + outcome.exercise_chunks
        state.job = ProposalJob.model_validate({**job.model_dump(), **updates})

    def _finish(self, state: _JobState) -> None:
        """Gọi khi giữ khoá: chốt trạng thái, rồi giữ tối đa `job_keep_finished` job đã kết thúc."""
        if state.job.status in TERMINAL_STATUSES:
            return
        state.job = state.job.model_copy(update={"status": derive_status(state.job), "finished_at": self._clock()})
        self._shared.finished.append(state.job.job_id)
        while len(self._shared.finished) > self._settings.job_keep_finished:
            self._shared.jobs.pop(self._shared.finished.popleft(), None)
        self._condition.notify_all()
