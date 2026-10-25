"""Ước tính chi phí của script opt-in `scripts/eval_concept_proposal.py` là CẬN TRÊN (CLAUDE.md §2.2). Không gọi LLM.

Chỉ kiểm phần tính toán và `--dry-run`; script không chạy với LLM thật trong pytest.
"""

import math
from pathlib import Path

import pytest

from holocron_core.domain.llm import ChatMessage
from holocron_core.infrastructure.llm_client import HTTP_RETRY_MAX
from holocron_core.infrastructure.llm_config import LlmTaskLimits, load_runtime_config
from holocron_core.services.concept_proposal_prompt import MAX_ECHOED_RESPONSE_CHARS, RETRY_NOTE, build_messages
from scripts.eval_concept_proposal import (
    MAX_UTF8_BYTES_PER_CHAR,
    MESSAGE_OVERHEAD_TOKENS,
    _parser,
    cost_upper_bound_usd,
    input_tokens_upper_bound,
    load_fixture,
    max_new_calls,
    run,
    worst_call_input_tokens,
)

FIXTURE = Path(__file__).resolve().parent.parent / "scripts" / "fixtures" / "concept_chunks_sample.json"


def test_input_bound_counts_utf8_bytes_not_characters() -> None:
    messages = [ChatMessage(role="user", content="Ý thức"), ChatMessage(role="system", content="abc")]
    bytes_total = len("Ý thức".encode()) + 3
    assert len("Ý thức") < len("Ý thức".encode())
    assert input_tokens_upper_bound(messages) == bytes_total + 2 * MESSAGE_OVERHEAD_TOKENS


def test_worst_call_includes_the_echoed_response_and_the_retry_note() -> None:
    _, _, chunks = load_fixture(FIXTURE)
    messages = build_messages(chunks)
    first = input_tokens_upper_bound(messages)
    worst = worst_call_input_tokens(messages)
    longest_echo = "ệ" * MAX_ECHOED_RESPONSE_CHARS
    retry = [
        *messages,
        ChatMessage(role="assistant", content=longest_echo),
        ChatMessage(role="user", content=RETRY_NOTE),
    ]
    assert worst >= input_tokens_upper_bound(retry) > first
    assert worst - first == MAX_ECHOED_RESPONSE_CHARS * MAX_UTF8_BYTES_PER_CHAR + len(RETRY_NOTE.encode()) + 32


@pytest.mark.parametrize(
    ("max_calls", "retries", "cached", "expected"),
    [(2, 1, False, 2), (5, 1, False, 2), (1, 1, False, 1), (2, 1, True, 1), (0, 1, False, 0), (5, 3, True, 3)],
)
def test_max_new_calls(max_calls: int, retries: int, cached: bool, expected: int) -> None:
    assert max_new_calls(max_calls, retries, first_call_cached=cached) == expected


def test_cost_bound_is_calls_times_input_and_output_rounded_up() -> None:
    exact = 2 * (1000 * 1.0 + 4096 * 5.0) / 1_000_000
    bound = cost_upper_bound_usd(
        calls=2, input_tokens=1000, max_tokens=4096, price_in_per_mtok=1.0, price_out_per_mtok=5.0
    )
    assert bound >= exact and math.isclose(bound, exact, abs_tol=1e-6)
    tiny = cost_upper_bound_usd(calls=1, input_tokens=1, max_tokens=0, price_in_per_mtok=0.1, price_out_per_mtok=0.0)
    assert tiny == 1e-6  # 1e-7 USD làm tròn LÊN, không về 0
    assert cost_upper_bound_usd(calls=0, input_tokens=10, max_tokens=10, price_in_per_mtok=1, price_out_per_mtok=1) == 0


def test_dry_run_prints_the_upper_bound_without_calling_or_needing_a_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse() -> None:
        raise AssertionError("dry-run không được tạo client LLM")

    args = _parser().parse_args(
        [
            str(FIXTURE),
            "--output-dir",
            str(tmp_path / "out"),
            "--price-input-per-mtok",
            "1",
            "--price-output-per-mtok",
            "5",
            "--dry-run",
        ]
    )
    assert run(args, load_runtime_config({}), connect=refuse) == 0
    _, _, chunks = load_fixture(FIXTURE)
    calls = 1 + LlmTaskLimits().json_retry_max
    requests = calls * (1 + HTTP_RETRY_MAX)
    expected = cost_upper_bound_usd(
        calls=requests,
        input_tokens=worst_call_input_tokens(build_messages(chunks)),
        max_tokens=args.max_tokens,
        price_in_per_mtok=1.0,
        price_out_per_mtok=5.0,
    )
    out = capsys.readouterr().out
    assert f"tối đa {calls} lượt gọi mới" in out and f"⇒ {requests} request" in out
    assert f"≤ {expected:.6f} USD" in out
    assert not (tmp_path / "out").exists()
