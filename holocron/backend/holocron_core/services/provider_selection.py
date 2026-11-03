"""Nơi DUY NHẤT chọn nhà cung cấp AI theo vai, thứ tự và trần chi tiêu (spec §5.16; ADR-009 mục 12.3, 12.5).

`ProviderSelection` cài `ProviderSelector` và `UsageRecorder`: nguồn nhà cung cấp và sổ chi tiêu là tham số
(`infrastructure/ai_runtime.py` cấp bản env + RAM ở V02). Thứ tự: `priority` tăng dần (env = 0), hoà thì theo id.
Đủ điều kiện cho vai: có model cho vai, có khoá hoặc là loopback, và nhóm bộ đếm còn chỗ cho chi phí TỐI ĐA của lời gọi
(`CostEstimate`). Kiểm trần và giữ chỗ là MỘT thao tác nguyên tử của sổ (`SpendLedger.try_reserve`), nên các lời gọi
song song không vượt trần; `record` nhả chỗ và cộng chi phí thật, `release` chỉ nhả chỗ (lỗi trước khi gửi); `peek`
xem trước mà không giữ chỗ.
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


def ordered_candidates(candidates: Sequence[ProviderCandidate]) -> list[ProviderCandidate]:
    return sorted(candidates, key=lambda candidate: (candidate.priority, candidate.provider_id))


class ProviderSelection:
    def __init__(self, providers: ProviderSource, ledger: SpendLedger) -> None:
        self._providers = providers
        self._ledger = ledger
        self._lock = threading.Lock()
        # Chỗ giữ đang mở theo id (thuộc tính instance): `record` tính chi phí theo đúng bảng giá lúc giữ chỗ.
        self._reservations: dict[str, _Reservation] = {}

    def select(self, role: AiRole, estimate: CostEstimate) -> SelectedProvider | None:
        for candidate in ordered_candidates(self._providers()):
            model = candidate.model_for(role)
            if model is None or not self._usable(candidate, role):
                continue
            amount = estimate_cost_usd(candidate.prices, estimate)
            if not self._ledger.try_reserve(candidate.budget_group, amount, candidate.budget_usd):
                continue
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
        return None

    def peek(self, role: AiRole) -> ProviderPreview | None:
        for candidate in ordered_candidates(self._providers()):
            if not self._usable(candidate, role):
                continue
            if self._ledger.spent_usd(candidate.budget_group) >= candidate.budget_usd:
                continue
            return ProviderPreview(provider_id=candidate.provider_id, label=candidate.label, local=candidate.local)
        return None

    @staticmethod
    def _usable(candidate: ProviderCandidate, role: AiRole) -> bool:
        return candidate.model_for(role) is not None and (candidate.api_key is not None or candidate.local)

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
        configured = [candidate for candidate in self._providers() if self._usable(candidate, role)]
        return bool(configured) and all(
            self._ledger.spent_usd(candidate.budget_group) >= candidate.budget_usd for candidate in configured
        )
