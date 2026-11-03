"""Nơi DUY NHẤT chọn nhà cung cấp AI theo vai, thứ tự và trần chi tiêu (spec §5.16; ADR-009 mục 12.3, 12.5).

`ProviderSelection` cài `ProviderSelector` và `UsageRecorder`: nguồn nhà cung cấp và sổ chi tiêu là tham số
(`infrastructure/ai_runtime.py` cấp bản env + RAM ở V02). Thứ tự: `priority` tăng dần (env = 0), hoà thì theo id.
Đủ điều kiện cho vai: có model cho vai, có khoá hoặc là loopback, và nhóm bộ đếm còn chỗ cho chi phí TỐI ĐA của lời gọi
(`CostEstimate`). Kiểm trần và giữ chỗ là MỘT thao tác nguyên tử của sổ (`SpendLedger.try_reserve`), nên các lời gọi
song song không vượt trần; `record` trả phần giữ chỗ và cộng chi phí thật.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence

from ..domain.ai import (
    AiRole,
    AiUsage,
    CostEstimate,
    ProviderCandidate,
    SelectedProvider,
    SpendLedger,
    estimate_cost_usd,
    usage_cost_usd,
)

ProviderSource = Callable[[], Sequence[ProviderCandidate]]


def ordered_candidates(candidates: Sequence[ProviderCandidate]) -> list[ProviderCandidate]:
    return sorted(candidates, key=lambda candidate: (candidate.priority, candidate.provider_id))


class ProviderSelection:
    def __init__(self, providers: ProviderSource, ledger: SpendLedger) -> None:
        self._providers = providers
        self._ledger = ledger
        self._lock = threading.Lock()
        # Nhà cung cấp đã chọn gần nhất theo id: `record` tính chi phí theo đúng bảng giá lúc giữ chỗ.
        self._selected: dict[str, ProviderCandidate] = {}

    def select(self, role: AiRole, estimate: CostEstimate) -> SelectedProvider | None:
        for candidate in ordered_candidates(self._providers()):
            model = candidate.model_for(role)
            if model is None or (candidate.api_key is None and not candidate.local):
                continue
            amount = estimate_cost_usd(candidate.prices, estimate)
            if not self._ledger.try_reserve(candidate.budget_group, amount, candidate.budget_usd):
                continue
            with self._lock:
                self._selected[candidate.provider_id] = candidate
            return SelectedProvider(
                provider_id=candidate.provider_id,
                base_url=candidate.base_url,
                api_key=candidate.api_key,
                model=model,
                local=candidate.local,
                ip_policy=candidate.ip_policy,
            )
        return None

    def record(self, provider_id: str, usage: AiUsage, *, reserved: CostEstimate | None = None) -> None:
        with self._lock:
            candidate = self._selected.get(provider_id)
        if candidate is None:
            candidate = next((item for item in self._providers() if item.provider_id == provider_id), None)
        if candidate is None:
            return
        reserved_usd = 0.0 if reserved is None else estimate_cost_usd(candidate.prices, reserved)
        self._ledger.settle(candidate.budget_group, reserved_usd, usage_cost_usd(candidate.prices, usage))

    def budget_exhausted(self, role: AiRole) -> bool:
        """Mọi nhà cung cấp đủ cấu hình của vai đã tiêu hết trần (dùng cho trạng thái; không giữ chỗ)."""
        configured = [
            candidate
            for candidate in self._providers()
            if candidate.model_for(role) is not None and (candidate.api_key is not None or candidate.local)
        ]
        return bool(configured) and all(
            self._ledger.spent_usd(candidate.budget_group) >= candidate.budget_usd for candidate in configured
        )
