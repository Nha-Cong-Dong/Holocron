"""Nơi DUY NHẤT chọn nhà cung cấp AI theo vai, thứ tự và trần chi tiêu (spec §5.16; ADR-009 mục 12.3, 12.5).

`ProviderSelection` cài `ProviderSelector` và `UsageRecorder`: nguồn nhà cung cấp và sổ chi tiêu là tham số
(`infrastructure/ai_runtime.py` cấp bản env + RAM ở V02). Thứ tự: `priority` tăng dần (env = 0), hoà thì theo id.
Đủ điều kiện cho vai: có model cho vai, có khoá hoặc là loopback, và nhóm bộ đếm còn chỗ cho chi phí TỐI ĐA của lời gọi
(`CostEstimate`). Nhà cung cấp người dùng đã tắt không được chọn. Trần của một nhóm bộ đếm dùng chung (cùng base URL
chuẩn hoá và cùng khoá, gán ở `infrastructure/provider_store.merge_providers`) là trần NHỎ NHẤT trong nhóm. Kiểm trần và giữ chỗ là MỘT thao tác nguyên tử của sổ (`SpendLedger.try_reserve`), nên các lời gọi
song song không vượt trần; `record` nhả chỗ và cộng chi phí thật, `release` chỉ nhả chỗ (lỗi trước khi gửi); `peek`
xem trước mà không giữ chỗ; `reserve` giữ chỗ cho MỘT nhà cung cấp chỉ định (kiểm thử `POST …/test`).
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..domain.ai import (
    AiRole,
    AiUsage,
    CostEstimate,
    ProviderCandidate,
    ProviderPreview,
    SelectedProvider,
    SpendLedger,
    estimate_cost_usd,
    usage_cost_usd,
)

ProviderSource = Callable[[], Sequence[ProviderCandidate]]


@dataclass(frozen=True)
class _Reservation:
    group: str
    amount_usd: float
    candidate: ProviderCandidate


def group_budgets(candidates: Sequence[ProviderCandidate]) -> dict[str, float]:
    """Trần của từng nhóm bộ đếm = trần nhỏ nhất trong nhóm (ADR-009 mục 12.2)."""
    budgets: dict[str, float] = {}
    for candidate in candidates:
        current = budgets.get(candidate.budget_group)
        budgets[candidate.budget_group] = (
            candidate.budget_usd if current is None else min(current, candidate.budget_usd)
        )
    return budgets


def ordered_candidates(candidates: Sequence[ProviderCandidate]) -> list[ProviderCandidate]:
    return sorted(candidates, key=lambda candidate: (candidate.priority, candidate.provider_id))


class ProviderSelection:
    def __init__(self, providers: ProviderSource, ledger: SpendLedger) -> None:
        self._providers = providers
        self._ledger = ledger
        self._lock = threading.Lock()
        # Chỗ giữ đang mở theo id (thuộc tính instance): `record` tính chi phí theo đúng bảng giá lúc giữ chỗ.
        self._reservations: dict[str, _Reservation] = {}

    def candidates(self) -> list[ProviderCandidate]:
        """Mọi nhà cung cấp của nguồn theo thứ tự chọn (kể cả đã tắt); dùng cho trang cài đặt."""
        return ordered_candidates(self._providers())

    def select(self, role: AiRole, estimate: CostEstimate) -> SelectedProvider | None:
        candidates = ordered_candidates(self._providers())
        budgets = group_budgets(candidates)
        for candidate in candidates:
            model = candidate.model_for(role)
            if model is None or not self._usable(candidate, role):
                continue
            selected = self._reserve(candidate, model, estimate, budgets[candidate.budget_group])
            if selected is not None:
                return selected
        return None

    def reserve(self, provider_id: str, model: str, estimate: CostEstimate) -> SelectedProvider | None:
        """Giữ chỗ `estimate` cho đúng nhà cung cấp `provider_id` (kể cả đã tắt), không xét vai; nhóm không còn chỗ
        hoặc không có nhà cung cấp đó trong nguồn ⇒ `None`."""
        candidates = self._providers()
        budgets = group_budgets(candidates)
        for candidate in candidates:
            if candidate.provider_id == provider_id:
                return self._reserve(candidate, model, estimate, budgets[candidate.budget_group])
        return None

    def _reserve(
        self, candidate: ProviderCandidate, model: str, estimate: CostEstimate, budget_usd: float
    ) -> SelectedProvider | None:
        amount = estimate_cost_usd(candidate.prices, estimate)
        if not self._ledger.try_reserve(candidate.budget_group, amount, budget_usd):
            return None
        reservation_id = secrets.token_hex(16)
        with self._lock:
            self._reservations[reservation_id] = _Reservation(candidate.budget_group, amount, candidate)
        return SelectedProvider(
            provider_id=candidate.provider_id,
            base_url=candidate.base_url,
            api_key=candidate.api_key,
            model=model,
            local=candidate.local,
            ip_policy=candidate.ip_policy,
            reservation_id=reservation_id,
        )

    def peek(self, role: AiRole) -> ProviderPreview | None:
        candidates = ordered_candidates(self._providers())
        budgets = group_budgets(candidates)
        for candidate in candidates:
            if not self._usable(candidate, role):
                continue
            if self._ledger.spent_usd(candidate.budget_group) >= budgets[candidate.budget_group]:
                continue
            return ProviderPreview(provider_id=candidate.provider_id, label=candidate.label, local=candidate.local)
        return None

    def spent_usd(self, group: str) -> float:
        return self._ledger.spent_usd(group)

    @staticmethod
    def _usable(candidate: ProviderCandidate, role: AiRole) -> bool:
        return (
            candidate.enabled
            and candidate.model_for(role) is not None
            and (candidate.api_key is not None or candidate.local)
        )

    def _take(self, reservation_id: str) -> _Reservation | None:
        with self._lock:
            return self._reservations.pop(reservation_id, None)

    def record(self, reservation_id: str, usage: AiUsage) -> None:
        reservation = self._take(reservation_id)
        if reservation is None:
            return
        actual = usage_cost_usd(reservation.candidate.prices, usage)
        self._ledger.settle(reservation.group, reservation.amount_usd, actual)

    def release(self, reservation_id: str) -> None:
        reservation = self._take(reservation_id)
        if reservation is not None:
            self._ledger.settle(reservation.group, reservation.amount_usd, 0.0)

    def budget_exhausted(self, role: AiRole) -> bool:
        """Mọi nhà cung cấp đủ cấu hình của vai đã tiêu hết trần (dùng cho trạng thái; không giữ chỗ)."""
        candidates = self._providers()
        budgets = group_budgets(candidates)
        configured = [candidate for candidate in candidates if self._usable(candidate, role)]
        return bool(configured) and all(
            self._ledger.spent_usd(candidate.budget_group) >= budgets[candidate.budget_group]
            for candidate in configured
        )
