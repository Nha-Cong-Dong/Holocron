"""Ingestion Gate: phát hiện VLM đọc sai bảng số liệu / LaTeX và cho người dùng sửa.

Năm tín hiệu độc lập, mỗi tín hiệu sinh GateIssue gắn toạ độ ô (row, col):
  S1. Dual-pass disagreement: hai lượt VLM (prompt/nhiệt độ khác nhau) đọc ra giá trị khác.
  S2. Low confidence: confidence ô < CELL_CONFIDENCE_MIN.
  S3. Schema/type: cột không khớp schema của topic, giá trị không parse được, ngoài miền hợp lệ.
  S4. Text-layer cross-check: nếu trang có text layer, mọi số trong bảng phải xuất hiện trong text.
  S5. Self-solve mismatch: AI tự giải không khớp phương án nào -> nghi bảng bị đọc sai.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain.ingestion import (
    ExtractedCell,
    ExtractedTable,
    GateIssue,
    GateReport,
    GateStatus,
    PatchAudit,
    Severity,
)
from ..domain.schemas import (
    MAX_PROCESSES,
    CPUSchedulingProblem,
    OptionKey,
    ProcessItem,
    QuestionTemplate,
    SchedulingAlgorithm,
    SelfSolveResult,
)
from ..sandbox.sandbox import SandboxStatus, run_template

# Model dữ liệu của Gate đã chuyển về `domain/ingestion.py`; tên cũ vẫn import được từ module này.
__all__ = [
    "CELL_CONFIDENCE_MIN",
    "TABLE_SCHEMAS",
    "TOPIC_ALGORITHMS",
    "CellPatch",
    "ColumnSpec",
    "ExtractedCell",
    "ExtractedTable",
    "GateIssue",
    "GateReport",
    "GateStatus",
    "PatchAudit",
    "Severity",
    "apply_patches",
    "check_table",
    "gate_status",
    "latex_issues",
    "rerun_self_solve",
    "table_to_params",
    "table_to_problem",
]

CELL_CONFIDENCE_MIN = 0.85


class ColumnSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    header_aliases: tuple[str, ...]
    kind: Literal["label", "int"]
    min_value: int | None = None
    max_value: int | None = None


TABLE_SCHEMAS: dict[str, tuple[ColumnSpec, ...]] = {
    "os.cpu_sched.rr.avg_waiting_time": (
        ColumnSpec(header_aliases=("process", "tiến trình", "tien trinh", "pid"), kind="label"),
        ColumnSpec(
            header_aliases=("arrival", "arrival time", "thời điểm đến", "thoi diem den", "at"),
            kind="int",
            min_value=0,
            max_value=200,
        ),
        ColumnSpec(
            header_aliases=("burst", "burst time", "thời gian thực thi", "cpu burst", "bt"),
            kind="int",
            min_value=1,
            max_value=100,
        ),
    ),
}


class CellPatch(BaseModel):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    new_text: str = Field(max_length=64)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _parse_int(text: str) -> int | None:
    cleaned = (
        text.strip().replace("O", "0").replace("o", "0") if re.fullmatch(r"[0-9Oo]+", text.strip()) else text.strip()
    )
    return int(cleaned) if re.fullmatch(r"-?[0-9]+", cleaned) else None


def latex_issues(stem: str) -> list[GateIssue]:
    issues: list[GateIssue] = []
    unescaped_dollars = len(re.findall(r"(?<!\\)\$", stem))
    if unescaped_dollars % 2:
        issues.append(
            GateIssue(
                code="LATEX_UNBALANCED",
                severity=Severity.BLOCKING,
                message_vi="Số ký tự $ lẻ: có công thức inline chưa đóng.",
            )
        )
    depth = 0
    for ch in re.sub(r"\\[{}]", "", stem):
        depth += 1 if ch == "{" else -1 if ch == "}" else 0
        if depth < 0:
            break
    if depth != 0:
        issues.append(
            GateIssue(
                code="LATEX_UNBALANCED",
                severity=Severity.BLOCKING,
                message_vi="Ngoặc nhọn {} không cân bằng trong LaTeX.",
            )
        )
    return issues


def check_table(
    topic_key: str,
    primary: ExtractedTable,
    secondary: ExtractedTable | None,
    page_text_layer: str | None,
    self_solve: SelfSolveResult | None,
) -> list[GateIssue]:
    issues: list[GateIssue] = []
    schema = TABLE_SCHEMAS.get(topic_key)

    if schema is not None:
        if len(primary.headers) != len(schema):
            issues.append(
                GateIssue(
                    code="HEADER_MISMATCH",
                    severity=Severity.BLOCKING,
                    message_vi=f"Bảng có {len(primary.headers)} cột, schema cần {len(schema)}.",
                )
            )
        for col, (header, spec) in enumerate(zip(primary.headers, schema)):
            if _norm(header) not in spec.header_aliases:
                issues.append(
                    GateIssue(
                        code="HEADER_MISMATCH",
                        severity=Severity.WARNING,
                        col=col,
                        message_vi=f"Header '{header}' không khớp {spec.header_aliases[0]}.",
                    )
                )

    if schema is not None and primary.n_rows > MAX_PROCESSES:
        issues.append(
            GateIssue(
                code="OUT_OF_RANGE",
                severity=Severity.BLOCKING,
                row=MAX_PROCESSES,
                message_vi=f"Bảng có {primary.n_rows} dòng, tối đa {MAX_PROCESSES} tiến trình.",
            )
        )

    labels_seen: dict[str, int] = {}
    for row in range(primary.n_rows):
        for col in range(len(primary.headers)):
            cell = primary.cell(row, col)
            if cell is None:
                issues.append(
                    GateIssue(
                        code="MISSING_CELL",
                        severity=Severity.BLOCKING,
                        row=row,
                        col=col,
                        message_vi="VLM không đọc được ô này.",
                    )
                )
                continue
            if not cell.verified_by_human and cell.confidence < CELL_CONFIDENCE_MIN:
                issues.append(
                    GateIssue(
                        code="LOW_CONFIDENCE",
                        severity=Severity.WARNING,
                        row=row,
                        col=col,
                        message_vi=f"Độ tin cậy {cell.confidence:.2f} < {CELL_CONFIDENCE_MIN}.",
                    )
                )
            if secondary is not None and not cell.verified_by_human:
                other = secondary.cell(row, col)
                if other is None or _norm(other.text) != _norm(cell.text):
                    issues.append(
                        GateIssue(
                            code="DUAL_PASS_MISMATCH",
                            severity=Severity.BLOCKING,
                            row=row,
                            col=col,
                            message_vi="Hai lượt đọc VLM cho kết quả khác nhau.",
                            alternatives=sorted({cell.text, other.text if other else ""} - {""}),
                        )
                    )
            if schema is not None and col < len(schema):
                spec = schema[col]
                if spec.kind == "label":
                    key = _norm(cell.text)
                    if key in labels_seen:
                        issues.append(
                            GateIssue(
                                code="DUPLICATE_LABEL",
                                severity=Severity.BLOCKING,
                                row=row,
                                col=col,
                                message_vi=f"Nhãn '{cell.text}' trùng với dòng {labels_seen[key]}.",
                            )
                        )
                    labels_seen[key] = row
                else:
                    value = _parse_int(cell.text)
                    if value is None:
                        issues.append(
                            GateIssue(
                                code="NOT_A_NUMBER",
                                severity=Severity.BLOCKING,
                                row=row,
                                col=col,
                                message_vi=f"'{cell.text}' không phải số nguyên.",
                            )
                        )
                    elif (spec.min_value is not None and value < spec.min_value) or (
                        spec.max_value is not None and value > spec.max_value
                    ):
                        issues.append(
                            GateIssue(
                                code="OUT_OF_RANGE",
                                severity=Severity.BLOCKING,
                                row=row,
                                col=col,
                                message_vi=f"{value} ngoài miền [{spec.min_value}, {spec.max_value}].",
                            )
                        )
                    elif page_text_layer is not None and not re.search(rf"(?<![0-9]){value}(?![0-9])", page_text_layer):
                        issues.append(
                            GateIssue(
                                code="NOT_IN_TEXT_LAYER",
                                severity=Severity.WARNING,
                                row=row,
                                col=col,
                                message_vi=f"Số {value} không xuất hiện trong text layer của trang.",
                            )
                        )

    if self_solve is not None and self_solve.status in ("NO_MATCH", "AMBIGUOUS"):
        issues.append(
            GateIssue(
                code="SELF_SOLVE_MISMATCH",
                severity=Severity.BLOCKING,
                message_vi=(
                    f"AI tự giải ra {self_solve.solved_answer_display or '?'} nhưng không khớp đúng một phương án "
                    f"({self_solve.status}). Nhiều khả năng bảng số liệu bị đọc sai — hãy kiểm tra các ô được tô."
                ),
            )
        )
    return issues


def gate_status(issues: list[GateIssue]) -> GateStatus:
    if any(i.severity is Severity.BLOCKING for i in issues):
        return GateStatus.BLOCKED
    if any(i.severity is Severity.WARNING for i in issues):
        return GateStatus.NEEDS_REVIEW
    return GateStatus.AUTO_PASS


def apply_patches(table: ExtractedTable, patches: list[CellPatch], editor_id: str) -> tuple[ExtractedTable, PatchAudit]:
    """Sửa ô do người dùng nhập: confidence=1.0 (human-verified), tăng version, ghi audit."""
    by_coord = {(c.row, c.col): c for c in table.cells}
    changes: list[dict[str, Any]] = []
    for p in patches:
        if p.col >= len(table.headers):
            raise ValueError(f"cột {p.col} không tồn tại")
        old = by_coord.get((p.row, p.col))
        by_coord[(p.row, p.col)] = ExtractedCell(
            row=p.row, col=p.col, text=p.new_text, confidence=1.0, verified_by_human=True
        )
        changes.append({"row": p.row, "col": p.col, "old": old.text if old else None, "new": p.new_text})
    new_table = table.model_copy(
        update={
            "version": table.version + 1,
            "cells": sorted(by_coord.values(), key=lambda c: (c.row, c.col)),
        }
    )
    audit = PatchAudit(
        table_id=table.table_id,
        from_version=table.version,
        to_version=new_table.version,
        editor_id=editor_id,
        changes=changes,
    )
    return new_table, audit


# --------------------------------------------------------------------------- #
# Self-solve lại sau khi người dùng sửa bảng
# --------------------------------------------------------------------------- #


TOPIC_ALGORITHMS: dict[str, SchedulingAlgorithm] = {
    "os.cpu_sched.rr.avg_waiting_time": SchedulingAlgorithm.RR,
}


def table_to_problem(topic_key: str, table: ExtractedTable, quantum: int | None) -> CPUSchedulingProblem:
    """Chuyển bảng đã qua gate thành `CPUSchedulingProblem`. Ném ValueError nếu bảng hoặc tham số còn lỗi."""
    algorithm = TOPIC_ALGORITHMS.get(topic_key)
    if algorithm is None:
        raise ValueError(f"chưa có bộ chuyển đổi bảng cho topic {topic_key}")
    processes: list[ProcessItem] = []
    for row in range(table.n_rows):
        name_cell, at_cell, bt_cell = (table.cell(row, c) for c in range(3))
        if name_cell is None or at_cell is None or bt_cell is None:
            raise ValueError(f"dòng {row} thiếu ô")
        at, bt = _parse_int(at_cell.text), _parse_int(bt_cell.text)
        if at is None or bt is None:
            raise ValueError(f"dòng {row} có ô không phải số")
        processes.append(ProcessItem(name=name_cell.text, arrival_time=at, burst_time=bt))
    return CPUSchedulingProblem(algorithm=algorithm, processes=processes, quantum=quantum)


def table_to_params(topic_key: str, table: ExtractedTable, extra: dict[str, Any]) -> dict[str, Any]:
    """Chuyển bảng đã qua gate thành params cho solve(). Ném ValueError nếu bảng còn lỗi."""
    quantum = extra.get("quantum")
    problem = table_to_problem(topic_key, table, int(quantum) if quantum is not None else None)
    return dict(problem.to_template_params())


def _first_number(text: str) -> float | None:
    m = re.search(r"-?[0-9]+(?:[.,][0-9]+)?", text)
    return float(m.group(0).replace(",", ".")) if m else None


def rerun_self_solve(
    solver: QuestionTemplate, params: dict[str, Any], options: dict[OptionKey, str]
) -> SelfSolveResult:
    result = run_template(solver.generator_source, seed=0, params_override=params)
    if result.status is not SandboxStatus.OK or result.solution is None:
        return SelfSolveResult(status="SKIPPED", solver_confidence=0.0, solver_template_id=solver.template_id)
    value = float(result.solution["value"])
    tol = max(solver.answer.tolerance_abs, 10 ** (-solver.answer.decimals) / 2)
    hits = [k for k, t in options.items() if (n := _first_number(t)) is not None and abs(n - value) <= tol]
    display = result.solution["display"]
    if len(hits) == 1:
        return SelfSolveResult(
            status="MATCHED",
            solved_answer_display=display,
            matched_option_key=hits[0],
            solver_confidence=1.0,
            solver_template_id=solver.template_id,
        )
    if len(hits) > 1:
        return SelfSolveResult(
            status="AMBIGUOUS",
            solved_answer_display=display,
            solver_confidence=0.5,
            solver_template_id=solver.template_id,
        )
    return SelfSolveResult(
        status="NO_MATCH", solved_answer_display=display, solver_confidence=1.0, solver_template_id=solver.template_id
    )
