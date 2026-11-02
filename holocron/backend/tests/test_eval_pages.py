"""Phần tính chỉ số của script đo GĐ0 `scripts/eval_pages.py` (không dùng eval_data, không đọc PDF)."""

from __future__ import annotations

from pathlib import Path

from scripts.eval_pages import (
    NA,
    PageRow,
    gt_tokens,
    is_nav_topic,
    main,
    make_config,
    metrics,
    must_read_report,
    trap_report,
)


def _row(
    page: int, topic: str | None, *, role: str = "content", dropped: bool | None = False, **extra: object
) -> PageRow:
    data: dict[str, object] = {
        "set_name": "arm_ch2",
        "page": page,
        "topic": topic,
        "status": "ok",
        "page_role": role,
        "dropped": dropped,
        "review_reasons": [],
        "has_figure": False,
        "tier_used": "text_layer",
        "failure_reason": None,
        **extra,
    }
    return PageRow(**data)


def test_nav_topics() -> None:
    assert is_nav_topic("bia") and is_nav_topic("muc-luc-chuong") and is_nav_topic("muc-luc-mon")
    assert not is_nav_topic("bai-tap") and not is_nav_topic("on-tap") and not is_nav_topic(None)


def test_gt_tokens_skip_header_and_page_number() -> None:
    blocks = [
        {"type": "header", "text": "Tieu de lap"},
        {"type": "list", "text": "Noi dung Mot"},
        {"type": "page_number", "text": "12"},
    ]
    assert gt_tokens(blocks) == {"noi", "dung", "mot"}


def test_metrics_precision_recall_and_lists() -> None:
    rows = [
        _row(1, "bia", role="cover_or_course_nav", dropped=True),
        _row(2, "muc-luc-chuong"),
        _row(3, "kien-truc", role="chapter_nav", dropped=True),
        _row(4, "kien-truc", status="needs_review"),
        _row(5, "kien-truc", role="build_step", dropped=True, gt_build_subset=1.0),
        _row(6, "kien-truc", role="build_step", dropped=True, gt_build_subset=0.5),
        _row(7, "bai-tap", has_figure=True),
    ]
    result = metrics(rows)
    assert result["pages"] == 7 and result["needs_review"] == 1
    assert result["nav_precision"] == 0.5 and result["nav_recall"] == 0.5
    assert result["build_step_count"] == 2 and result["build_step_precision"] == 0.5
    assert result["content_recall"] == 2 / 5
    assert result["dropped_wrongly"] == [("arm_ch2", 3), ("arm_ch2", 5), ("arm_ch2", 6)]
    assert result["nav_missed"] == [("arm_ch2", 2)]
    assert result["has_figure"] == 1
    assert result["dropped_by_role"] == {"cover_or_course_nav": 1, "chapter_nav": 1, "build_step": 2}


def test_metrics_on_code_without_roles_print_na() -> None:
    rows = [_row(1, "bia", dropped=None), _row(2, "x", dropped=None, status="needs_review")]
    result = metrics(rows)
    assert result["nav_precision"] == NA and result["content_recall"] == NA and result["dropped_by_role"] == NA
    assert result["needs_review"] == 1


def test_trap_and_must_read_reports_use_only_codes_and_numbers() -> None:
    rows = [
        _row(24, "x", line_flags={"LOW_TEXT_CONTRAST": 1}, hidden_chars_dropped=3),
        PageRow(
            set_name="arm_ch4",
            page=22,
            topic="x",
            status="failed",
            page_role="content",
            dropped=False,
            review_reasons=[],
            has_figure=True,
            tier_used=None,
            failure_reason="OCR_ENGINE_MISSING",
            ocr_reason="image_covers_page",
            content_image_area_ratio=0.98,
        ),
    ]
    traps = trap_report(rows)
    assert traps["hidden"] == [
        {
            "page": "arm_ch2:24",
            "status": "ok",
            "line_flags": {"LOW_TEXT_CONTRAST": 1},
            "hidden_chars_dropped": 3,
            "reasons": [],
        }
    ]
    must = must_read_report(rows)
    assert must == [
        {
            "page": "arm_ch4:22",
            "caught": True,
            "ocr_reason": "image_covers_page",
            "image_needs_reading": False,
            "content_image_area_ratio": 0.98,
        }
    ]


def test_make_config_sets_template_ratio() -> None:
    assert make_config(0.4).template_min_page_ratio == 0.4


def test_script_refuses_to_write_inside_repo(tmp_path: Path) -> None:
    inside = Path(__file__).resolve().parents[1] / "eval_out_should_not_exist"
    assert main([str(inside), "--eval-root", str(tmp_path)]) == 2
    assert not inside.exists()
