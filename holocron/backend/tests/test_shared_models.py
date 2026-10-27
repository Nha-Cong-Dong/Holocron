"""Model dùng chung nằm ở `domain/` và tên cũ ở `services/`, `routers/` vẫn trỏ đúng cùng một đối tượng."""

import importlib

import pytest

from holocron_core.domain import ingestion, twins

# (module cũ, tên) -> module `domain` mới chứa định nghĩa
MOVED: list[tuple[str, str, str]] = [
    ("holocron_core.services.ingestion_gate", "ExtractedCell", "holocron_core.domain.ingestion"),
    ("holocron_core.services.ingestion_gate", "ExtractedTable", "holocron_core.domain.ingestion"),
    ("holocron_core.services.ingestion_gate", "GateIssue", "holocron_core.domain.ingestion"),
    ("holocron_core.services.ingestion_gate", "GateReport", "holocron_core.domain.ingestion"),
    ("holocron_core.services.ingestion_gate", "GateStatus", "holocron_core.domain.ingestion"),
    ("holocron_core.services.ingestion_gate", "PatchAudit", "holocron_core.domain.ingestion"),
    ("holocron_core.services.ingestion_gate", "Severity", "holocron_core.domain.ingestion"),
    ("holocron_core.services.extraction", "MediaType", "holocron_core.domain.ingestion"),
    ("holocron_core.services.extraction", "UploadedDocument", "holocron_core.domain.ingestion"),
    ("holocron_core.routers.api", "IngestionItemCreate", "holocron_core.domain.ingestion"),
    ("holocron_core.routers.api", "IngestionItem", "holocron_core.domain.ingestion"),
    ("holocron_core.routers.api", "IdempotencyRecord", "holocron_core.domain.twins"),
    ("holocron_core.services.pipeline", "AttemptLog", "holocron_core.domain.twins"),
    ("holocron_core.services.pipeline", "ReviewTicket", "holocron_core.domain.twins"),
    ("holocron_core.services.twin_batch", "SEED_SPACE", "holocron_core.domain.twins"),
    ("holocron_core.services.twin_batch", "TwinBatchRequest", "holocron_core.domain.twins"),
    ("holocron_core.services.twin_batch", "TwinBatch", "holocron_core.domain.twins"),
]


@pytest.mark.parametrize(("old_module", "name", "new_module"), MOVED)
def test_old_import_path_is_the_same_object_as_domain(old_module: str, name: str, new_module: str) -> None:
    old = getattr(importlib.import_module(old_module), name)
    new = getattr(importlib.import_module(new_module), name)
    assert old is new


@pytest.mark.parametrize(
    "model",
    [
        ingestion.UploadedDocument,
        ingestion.ExtractedCell,
        ingestion.ExtractedTable,
        ingestion.GateIssue,
        ingestion.GateReport,
        ingestion.PatchAudit,
        ingestion.IngestionItemCreate,
        ingestion.IngestionItem,
        twins.AttemptLog,
        twins.ReviewTicket,
        twins.TwinBatchRequest,
        twins.TwinBatch,
        twins.IdempotencyRecord,
    ],
)
def test_models_are_defined_in_domain(model: type) -> None:
    """Định nghĩa thật nằm ở `domain/` để repository ở `infrastructure/` import được mà không vi phạm phân tầng."""
    assert model.__module__.startswith("holocron_core.domain.")


def test_twin_batch_request_bounds_follow_seed_space() -> None:
    assert twins.SEED_SPACE == 2**63
    twins.TwinBatchRequest(base_seed=twins.SEED_SPACE - 1)
    with pytest.raises(ValueError):
        twins.TwinBatchRequest(base_seed=twins.SEED_SPACE)
