"""Dựng `ProviderList` cho trang Cài đặt "Kết nối AI" (spec §5.16, §6.11; ADR-009 mục 12.1–12.3).

Chỉ ghép dữ liệu đã có: nhà cung cấp đã hợp nhất (env + người dùng, kèm nhóm bộ đếm) từ bộ chọn, mục người dùng từ kho
(gồm cả mục có khoá không đọc được) và số đã tiêu của từng nhóm. Không có khoá đầy đủ hay endpoint env trong kết quả;
`key_last4` chỉ khi khoá dài ≥ 12 ký tự. Chi phí USD là số vận hành, ngoài I1.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ..domain.ai import (
    ProviderCandidate,
    ProviderConfig,
    ProviderList,
    WriteProtection,
    key_last4,
)
from ..infrastructure.provider_store import UserProviderEntry
from .provider_selection import group_budgets


def _env_config(
    candidate: ProviderCandidate, *, spent: float, group_budget: float, shared_with: list[str]
) -> ProviderConfig:
    key = None if candidate.api_key is None else candidate.api_key.get_secret_value()
    return ProviderConfig(
        id=candidate.provider_id,
        kind="openai_compatible",
        source="env",
        template=candidate.template,
        label=candidate.label,
        base_url=None,
        local=candidate.local,
        models=candidate.models,
        key_set=key is not None,
        key_last4=key_last4(key),
        key_storage="none" if key is None else "env",
        key_unreadable=False,
        enabled=True,
        priority=candidate.priority,
        budget_usd=candidate.budget_usd,
        spent_usd=spent,
        budget_exhausted=spent >= group_budget,
        budget_shared_with=shared_with,
        prices=candidate.prices,
    )


def _user_config(
    entry: UserProviderEntry, *, spent: float, group_budget: float, shared_with: list[str]
) -> ProviderConfig:
    return ProviderConfig(
        id=entry.id,
        kind="openai_compatible",
        source="user",
        template=entry.template,
        label=entry.label,
        base_url=entry.base_url,
        local=entry.local,
        models=entry.models,
        key_set=entry.key_set,
        key_last4=entry.key_last4,
        key_storage=entry.key_storage,
        key_unreadable=entry.key_unreadable,
        enabled=entry.enabled,
        priority=entry.priority,
        budget_usd=entry.budget_usd,
        spent_usd=spent,
        budget_exhausted=spent >= group_budget,
        budget_shared_with=shared_with,
        prices=entry.prices,
    )


def build_provider_list(
    merged: Sequence[ProviderCandidate],
    user_entries: Sequence[UserProviderEntry],
    spent_usd: Callable[[str], float],
    *,
    settings_persistent: bool,
    write_protection: WriteProtection,
) -> ProviderList:
    """Env trước, rồi người dùng theo `priority`, hoà theo id. Mục người dùng không có ứng viên (khoá không đọc được)
    giữ nhóm riêng theo id của nó."""
    user_ids = {entry.id for entry in user_entries}
    groups = {candidate.provider_id: candidate.budget_group for candidate in merged}
    budgets = group_budgets(merged)
    members: dict[str, list[str]] = {}
    for candidate in merged:
        members.setdefault(candidate.budget_group, []).append(candidate.provider_id)

    def numbers(provider_id: str, own_budget: float) -> tuple[float, float, list[str]]:
        group = groups.get(provider_id, provider_id)
        others = [other for other in members.get(group, []) if other != provider_id]
        return spent_usd(group), budgets.get(group, own_budget), others

    configs: list[ProviderConfig] = []
    env = sorted(
        (candidate for candidate in merged if candidate.provider_id not in user_ids),
        key=lambda candidate: (candidate.priority, candidate.provider_id),
    )
    for candidate in env:
        spent, budget, others = numbers(candidate.provider_id, candidate.budget_usd)
        configs.append(_env_config(candidate, spent=spent, group_budget=budget, shared_with=others))
    for entry in sorted(user_entries, key=lambda entry: (entry.priority, entry.id)):
        spent, budget, others = numbers(entry.id, entry.budget_usd)
        configs.append(_user_config(entry, spent=spent, group_budget=budget, shared_with=others))
    return ProviderList(providers=configs, settings_persistent=settings_persistent, write_protection=write_protection)
