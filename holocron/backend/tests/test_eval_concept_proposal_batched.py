"""Chế độ lô `legacy`/`pointer` của script đánh giá opt-in (`scripts/eval_concept_proposal.py`). LLM là stub;
KHÔNG gọi mạng. Dữ liệu là file mẫu tự tạo trong thư mục tạm (dạng bản chép tay của `eval_data`)."""

import json
from pathlib import Path

import pytest

from holocron_core.infrastructure.llm_config import load_runtime_config
from scripts.eval_concept_proposal import EXIT_BAD_FIXTURE, _parser, load_fixture, run, transcript_pages
from tests.proposal_support import StubClient

CONFIGURED_ENV = {
    "HOLOCRON_LLM_API_KEY": "sk-test-eval",
    "HOLOCRON_LLM_BASE_URL": "https://gateway.example.test/v1",
    "HOLOCRON_LLM_MODEL_EXTRACT": "model-x",
}
DEFINITION = "Thanh ghi là ô nhớ tốc độ cao nằm bên trong bộ xử lý."


def _transcript(path: Path) -> Path:
    data = {
        "document": "mau.pdf",
        "page_count": 2,
        "pages": [
            {
                "page": 1,
                "source": "x",
                "topic": "y",
                "blocks": [
                    {"order": 2, "type": "paragraph", "text": DEFINITION},
                    {"order": 1, "type": "title", "text": "Thanh ghi"},
                    {"order": 0, "type": "header", "text": "Đầu trang"},
                ],
            },
            {"page": 2, "source": "x", "topic": "y", "blocks": [{"order": 0, "type": "page_number", "text": "2"}]},
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _args(fixture: Path, out: Path, mode: str, *extra: str) -> list[str]:
    return [
        str(fixture),
        "--output-dir",
        str(out),
        "--price-input-per-mtok",
        "1",
        "--price-output-per-mtok",
        "5",
        "--mode",
        mode,
        "--subject-code",
        "IT3070",
        "--chapter-code",
        "ch1",
        *extra,
    ]


def test_transcript_pages_skip_headers_and_page_numbers(tmp_path: Path) -> None:
    data = json.loads(_transcript(tmp_path / "t.json").read_text(encoding="utf-8"))
    assert transcript_pages(data) == [{"page_number": 1, "lines": ["Thanh ghi", "", DEFINITION]}]
    _, subject, chunks = load_fixture(tmp_path / "t.json", "IT3070", "ch1")
    assert subject == "IT3070" and [chunk.chapter_code for chunk in chunks] == ["ch1"]


@pytest.mark.parametrize("mode", ["legacy", "pointer"])
def test_batched_dry_run_needs_no_key(tmp_path: Path, mode: str, capsys: pytest.CaptureFixture[str]) -> None:
    def refuse() -> StubClient:
        raise AssertionError("dry-run không được tạo client LLM")

    args = _parser().parse_args(_args(_transcript(tmp_path / "t.json"), tmp_path / "out", mode, "--dry-run"))
    assert run(args, load_runtime_config({}), connect=refuse) == 0
    assert f"chế độ {mode}" in capsys.readouterr().out and not (tmp_path / "out").exists()


def test_batched_run_writes_counts_for_both_protocols(tmp_path: Path) -> None:
    fixture = _transcript(tmp_path / "t.json")
    _, _, [chunk] = load_fixture(fixture, "IT3070", "ch1")
    legacy = json.dumps([{"term": "Thanh ghi", "definition_quote": DEFINITION, "chunk_id": chunk.chunk_id}])
    pointer = json.dumps(
        {
            "chunks": [{"chunk_id": chunk.chunk_id, "is_exercise": False}],
            "pairs": [
                {
                    "term_span_id": "S2",
                    "term_exact": "Thanh ghi",
                    "def_span_ids": ["S2"],
                    "def_start_words": "Thanh ghi là",
                    "def_end_words": "bộ xử lý.",
                }
            ],
        }
    )
    config = load_runtime_config(CONFIGURED_ENV)
    for mode, response in (("legacy", legacy), ("pointer", pointer)):
        out = tmp_path / mode
        assert (
            run(_parser().parse_args(_args(fixture, out, mode)), config, connect=lambda r=response: StubClient(r)) == 0
        )
        [result] = out.glob("concept_proposal-*.json")
        summary = json.loads(result.read_text(encoding="utf-8"))
        assert (summary["mode"], summary["proposed"], summary["rejected"], summary["batches"]) == (mode, 1, 0, 1)


def test_batched_mode_checks_batch_size_and_configuration(tmp_path: Path) -> None:
    fixture = _transcript(tmp_path / "t.json")
    bad = _parser().parse_args(_args(fixture, tmp_path / "o", "pointer", "--batch-chunks", "21"))
    assert run(bad, load_runtime_config({})) == 2
    unconfigured = _parser().parse_args(_args(fixture, tmp_path / "o", "pointer"))
    assert run(unconfigured, load_runtime_config({})) == 2


@pytest.mark.parametrize("mode", ["single", "pointer"])
def test_transcript_without_subject_or_chapter_is_a_usage_error(
    tmp_path: Path, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Không có mã môn hay chương mặc định: file bản chép tay bắt buộc truyền cả hai."""
    fixture = _transcript(tmp_path / "t.json")
    args = _parser().parse_args(
        [
            str(fixture),
            "--output-dir",
            str(tmp_path / "o"),
            "--price-input-per-mtok",
            "1",
            "--price-output-per-mtok",
            "5",
        ]
        + ["--mode", mode, "--subject-code", "IT3070", "--dry-run"]
    )
    assert run(args, load_runtime_config({})) == 2
    assert "--chapter-code" in capsys.readouterr().err
    with pytest.raises(ValueError):
        load_fixture(fixture)


@pytest.mark.parametrize("content", ["{không phải json", '{"subject_code": "IT3070"}'])
def test_broken_fixture_is_a_data_error_not_a_usage_error(
    tmp_path: Path, content: str, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = tmp_path / "hong.json"
    fixture.write_text(content, encoding="utf-8")
    for mode in ("single", "pointer"):
        args = _parser().parse_args(_args(fixture, tmp_path / "o", mode, "--dry-run"))
        assert run(args, load_runtime_config({})) == EXIT_BAD_FIXTURE
    assert "File chunk không hợp lệ" in capsys.readouterr().err


SECRET = "NOI-DUNG-BI-MAT-KHONG-DUOC-IN"


def _transcript_with(tmp_path: Path, page: object) -> Path:
    data = {
        "document": "mau.pdf",
        "pages": [{"page": page, "blocks": [{"order": 0, "type": "paragraph", "text": SECRET}]}],
    }
    path = tmp_path / "sai-kieu.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("mode", ["single", "pointer"])
def test_wrong_typed_fixture_value_is_a_data_error_without_echoing_content(
    tmp_path: Path, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = _transcript_with(tmp_path, f"p-{SECRET}")
    args = _parser().parse_args(_args(fixture, tmp_path / "o", mode, "--dry-run"))
    assert run(args, load_runtime_config({})) == EXIT_BAD_FIXTURE
    err = capsys.readouterr().err
    assert "File chunk không hợp lệ" in err and "ValueError" in err
    assert SECRET not in err and "p-" not in err


@pytest.mark.parametrize("mode", ["single", "pointer"])
def test_non_utf8_fixture_is_a_data_error_without_echoing_content(
    tmp_path: Path, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = tmp_path / "latin1.json"
    fixture.write_bytes(f'{{"subject_code": "{SECRET}", "chunks": "'.encode() + b"\xff\xfe\x80" + b'"}')
    args = _parser().parse_args(_args(fixture, tmp_path / "o", mode, "--dry-run"))
    assert run(args, load_runtime_config({})) == EXIT_BAD_FIXTURE
    err = capsys.readouterr().err
    assert "File chunk không hợp lệ" in err and "UnicodeDecodeError" in err
    assert SECRET not in err


@pytest.mark.parametrize("mode", ["single", "pointer"])
@pytest.mark.parametrize("bad", ["it3070", "IT", "IT3070x", "I3070", ""])
def test_malformed_subject_code_is_a_usage_error(
    tmp_path: Path, mode: str, bad: str, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = _transcript(tmp_path / "t.json")
    args = _parser().parse_args(_args(fixture, tmp_path / "o", mode, "--dry-run"))
    args.subject_code = bad
    assert run(args, load_runtime_config({})) == 2
    assert "--subject-code" in capsys.readouterr().err
