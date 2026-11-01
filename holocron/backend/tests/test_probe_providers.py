"""Phần thuần của `scripts/probe_providers.py`: đọc biến, từ chối ghi trong repo, trần request, dry-run, che khoá.

Không gọi mạng: opener giả thay cho `urllib`.
"""

from __future__ import annotations

import io
import json
import random
import string
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

import pytest

from scripts import probe_providers as pp

KEY_A = "sk-AAAA1111bbbb2222"
KEY_B = "sk-ZZZZ9999yyyy8888"


def _env(**extra: str) -> dict[str, str]:
    return {
        "HOLOCRON_LLM_BASE_URL": "https://llm.example.test/v1",
        "HOLOCRON_LLM_API_KEY": KEY_A,
        "HOLOCRON_LLM_MODEL_EXTRACT": "m-extract",
        "HOLOCRON_LLM_MODEL_AUTHOR": "m-author",
        "HOLOCRON_VLM_BASE_URL": "https://vlm.example.test/v1",
        "HOLOCRON_VLM_API_KEY": KEY_B,
        "HOLOCRON_VLM_MODEL": "m-vlm",
        **extra,
    }


class _Response:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = body
        self.headers = Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value

    def read(self, size: int = -1, /) -> bytes:
        return self._body

    def close(self) -> None:
        return None


class _FakeOpener:
    """Thay `OpenerDirector`: ghi lại URL đã gọi, trả lần lượt các phản hồi cấp sẵn (lặp lại cái cuối)."""

    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def open(self, fullurl: urllib.request.Request, *, timeout: float) -> _Response:
        request = fullurl
        self.urls.append(request.full_url)
        response = self.responses[min(len(self.urls), len(self.responses)) - 1]
        if response.status >= 400:
            raise urllib.error.HTTPError(
                request.full_url, response.status, "err", response.headers, io.BytesIO(response._body)
            )
        return response


def _sender(opener: _FakeOpener, cap: int) -> pp.CountingSender:
    return pp.CountingSender(cap, opener=opener)


def _ok(content: str) -> _Response:
    body = {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}
    return _Response(200, json.dumps(body).encode())


def test_load_providers_maps_existing_variables() -> None:
    providers, skipped = pp.load_providers(_env())
    assert [p.models for p in providers] == [("m-extract", "m-author"), ("m-vlm",)]
    assert [p.gateway for p in providers] == [True, False]
    assert {s.prefix for s in skipped} == {"HOLOCRON_PROBE_GEMINI", "HOLOCRON_PROBE_CUSTOM"}
    assert skipped[0].missing == ("HOLOCRON_PROBE_GEMINI_BASE_URL", "HOLOCRON_PROBE_GEMINI_API_KEY")


def test_models_variable_wins_and_same_endpoint_merges() -> None:
    env = _env(
        HOLOCRON_VLM_BASE_URL="https://llm.example.test/v1/",
        HOLOCRON_VLM_API_KEY=KEY_A,
        HOLOCRON_LLM_MODELS="x, y",
        HOLOCRON_VLM_MODELS="y,z",
    )
    providers, _ = pp.load_providers(env)
    assert len(providers) == 1
    assert providers[0].prefixes == ("HOLOCRON_LLM", "HOLOCRON_VLM")
    assert providers[0].models == ("x", "y", "z")


def test_invalid_base_url_is_skipped_without_printing_value() -> None:
    providers, skipped = pp.load_providers(
        _env(HOLOCRON_PROBE_CUSTOM_BASE_URL="http://evil.example.test", HOLOCRON_PROBE_CUSTOM_API_KEY=KEY_A)
    )
    assert len(providers) == 2
    custom = next(s for s in skipped if s.prefix == "HOLOCRON_PROBE_CUSTOM")
    assert custom.missing == ("HOLOCRON_PROBE_CUSTOM_BASE_URL (không hợp lệ)",)
    assert "evil" not in repr(skipped)


def test_provider_repr_hides_key_and_url() -> None:
    providers, _ = pp.load_providers(_env())
    assert KEY_A not in repr(providers) and "example.test" not in repr(providers)


def test_plan_order_is_image_then_text_then_list_then_big_and_gateway_skips_big() -> None:
    env = _env(
        HOLOCRON_PROBE_CUSTOM_BASE_URL="https://free.example.test/v1",
        HOLOCRON_PROBE_CUSTOM_API_KEY="k-custom-1234",
        HOLOCRON_PROBE_CUSTOM_MODELS="c1",
    )
    providers, _ = pp.load_providers(env)
    plan = pp.build_plan(providers)
    kinds = [t.kind for t in plan]
    assert kinds == sorted(kinds, key=["image", "text", "list", "big"].index)
    assert [t for t in plan if t.kind == "big"] == [pp.Task("big", 1, "m-vlm"), pp.Task("big", 2, "c1")]


def test_refuses_to_write_inside_repo(capsys: pytest.CaptureFixture[str]) -> None:
    code = pp.main(["--live", "--out", str(pp.REPO_ROOT / "somewhere")], env=_env())
    assert code == 2
    assert "Từ chối" in capsys.readouterr().err


def test_dry_run_never_touches_network(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    class _Boom(pp.CountingSender):
        def __call__(self, request: urllib.request.Request, timeout_s: float) -> tuple[int, bytes]:
            raise AssertionError("dry-run không được gọi mạng")

    code = pp.main(["--dry-run", "--out", str(tmp_path)], env=_env(), sender=_Boom(10))
    out = capsys.readouterr().out
    assert code == 0
    assert "plan: (c) ảnh nhỏ" in out
    assert KEY_A not in out and "https://" not in out and "/v1" not in out
    assert not list(tmp_path.iterdir())


def test_request_cap_counts_every_request_including_list() -> None:
    opener = _FakeOpener([_ok("5")])
    sender = _sender(opener, 3)
    providers, _ = pp.load_providers(_env())
    small = pp.SmallImage(b"png", "Hóa đơn 4721", True)
    prober = pp.Prober(providers, sender, small=small)
    prober.run(pp.build_plan(providers))
    assert sender.count == 3
    assert len(opener.urls) == 3
    assert any(r.not_run for r in prober.rows.values())


def test_cap_stops_text_client_retries() -> None:
    opener = _FakeOpener([_Response(429, b'{"error":{"message":"slow down"}}', {"retry-after": "7"})])
    sender = _sender(opener, 5)
    providers, _ = pp.load_providers(_env())
    prober = pp.Prober(providers, sender, small=pp.SmallImage(b"png", "x 1", True))
    prober.run([pp.Task("text", 0, "m-extract")])
    row = prober.row(0, "m-extract")
    assert sender.count == 1  # retry_max=0: không thử lại
    assert row.http_429 == 1 and row.retry_after == ["7"]
    assert row.text_ok is False and "429" in (row.text_error or "")


def test_image_classification_and_vision_recommendation() -> None:
    truth = "Hóa đơn 4721"
    assert pp.classify_copy("  Hóa  đơn 4721. ", truth) == "exact"
    assert pp.classify_copy("Hoa don 4721", truth) == "digits"
    assert pp.classify_copy("xyz", truth) == "no"
    opener = _FakeOpener([_ok(truth)])
    providers, _ = pp.load_providers(_env())
    prober = pp.Prober(providers, _sender(opener, 10), small=pp.SmallImage(b"png", truth, True))
    prober.run([pp.Task("image", 1, "m-vlm"), pp.Task("text", 0, "m-extract")])
    assert prober.row(1, "m-vlm").image_match == "exact"
    assert prober.row(1, "m-vlm").image_tokens == (7, 3)
    assert pp.recommend(list(prober.rows.values()))["vlm"].startswith("vlm/m-vlm")


def test_big_image_is_not_sent_to_gateway_and_small_image_has_text() -> None:
    providers, _ = pp.load_providers(
        _env(HOLOCRON_VLM_BASE_URL="https://llm.example.test/v1", HOLOCRON_VLM_API_KEY=KEY_A)
    )
    assert providers[0].gateway
    assert all(t.kind != "big" for t in pp.build_plan(providers))
    image = pp.make_small_image()
    assert image.png.startswith(b"\x89PNG") and image.truth


def test_redact_hides_every_long_substring_of_random_keys() -> None:
    rng = random.Random(7)
    for _ in range(120):
        key = "".join(rng.choices(string.ascii_uppercase + string.digits, k=rng.randint(8, 40)))
        message = f"invalid api key provided: {key}. tail {key[-4:]} and middle {key[3:9]}; see https://x.test/{key}"
        out = pp.shown(message, [key])
        for start in range(len(key) - 3):
            assert key[start : start + 4] not in out
        assert "https://" not in out


def test_redact_runs_before_truncation() -> None:
    key = "KEY" + "Q" * 30
    text = "x" * (pp.MAX_SHOWN - 10) + key
    assert pp.shown(text, [key]).endswith("***")
    assert "QQQQ" not in pp.shown(text, [key])


def test_live_output_never_contains_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    echo = _Response(401, json.dumps({"error": {"message": f"Incorrect API key provided: {KEY_A}"}}).encode())
    code = pp.main(["--live", "--out", str(tmp_path)], env=_env(), sender=_sender(_FakeOpener([echo]), 4))
    assert code == 0
    blob = capsys.readouterr().out + "".join(f.read_text(encoding="utf-8") for f in tmp_path.iterdir())
    for key in (KEY_A, KEY_B):
        for start in range(len(key) - 3):
            assert key[start : start + 4] not in blob
    assert (tmp_path / "results.json").is_file() and (tmp_path / "results.md").is_file()
