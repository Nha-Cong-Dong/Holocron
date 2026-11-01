"""Phần thuần của `scripts/probe_providers.py`: đọc biến, từ chối ghi trong repo, trần request, dry-run, che khoá.

Không gọi mạng: opener giả thay cho `urllib`.
"""

from __future__ import annotations

import http.client
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
        self._pos = 0
        self.headers = Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value

    def read(self, size: int = -1, /) -> bytes:
        chunk = self._body[self._pos : self._pos + size] if size >= 0 else self._body[self._pos :]
        self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        self._pos = 0


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
    assert [p.gateway for p in providers] == [True, True]
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


def test_plan_order_is_image_then_text_then_list_then_big() -> None:
    env = _env(
        HOLOCRON_VLM_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai",
        HOLOCRON_PROBE_CUSTOM_BASE_URL="https://free.example.test/v1",
        HOLOCRON_PROBE_CUSTOM_API_KEY="k-custom-1234",
        HOLOCRON_PROBE_CUSTOM_MODELS="c1",
    )
    providers, _ = pp.load_providers(env)
    plan = pp.build_plan(providers)
    kinds = [t.kind for t in plan]
    assert kinds == sorted(kinds, key=["image", "text", "list", "big"].index)
    # host công khai đã biết được gửi ảnh lớn; gateway và host lạ thì không (mặc định an toàn)
    assert [t for t in plan if t.kind == "big"] == [pp.Task("big", 1, "m-vlm")]


def test_unknown_host_is_gateway_unless_allowed_by_flag_and_same_host_as_llm_is_blocked() -> None:
    env = _env(
        HOLOCRON_PROBE_CUSTOM_BASE_URL="https://FREE.example.test/v1/",
        HOLOCRON_PROBE_CUSTOM_API_KEY="k-custom-1234",
        HOLOCRON_PROBE_CUSTOM_MODELS="c1",
    )
    providers, _ = pp.load_providers(env)
    assert all(p.gateway for p in providers)
    allowed, _ = pp.load_providers(env, ["free.EXAMPLE.test"])
    assert [p.gateway for p in allowed] == [True, True, False]
    # cùng host với HOLOCRON_LLM_BASE_URL (khác khoá, khác đường dẫn) vẫn là gateway dù là host công khai
    same_host = _env(
        HOLOCRON_LLM_BASE_URL="https://api.openai.com/v1",
        HOLOCRON_PROBE_CUSTOM_BASE_URL="https://API.openai.com/other",
        HOLOCRON_PROBE_CUSTOM_API_KEY="k-custom-1234",
    )
    assert pp.load_providers(same_host)[0][-1].gateway
    loopback = _env(
        HOLOCRON_PROBE_CUSTOM_BASE_URL="http://127.0.0.1:9/v1", HOLOCRON_PROBE_CUSTOM_API_KEY="k-custom-1234"
    )
    assert not pp.load_providers(loopback)[0][-1].gateway


def test_merge_compares_normalized_urls() -> None:
    env = _env(HOLOCRON_VLM_BASE_URL="HTTPS://LLM.Example.TEST/v1/", HOLOCRON_VLM_API_KEY=KEY_A)
    assert len(pp.load_providers(env)[0]) == 1


def test_invalid_or_too_long_key_is_skipped() -> None:
    env = _env(HOLOCRON_VLM_API_KEY="has space", HOLOCRON_PROBE_CUSTOM_BASE_URL="https://x.test/v1")
    env["HOLOCRON_PROBE_CUSTOM_API_KEY"] = "k" * (pp.MAX_KEY_LENGTH + 1)
    providers, skipped = pp.load_providers(env)
    assert len(providers) == 1
    reasons = {s.prefix: s.missing for s in skipped}
    assert reasons["HOLOCRON_VLM"] == ("HOLOCRON_VLM_API_KEY (không hợp lệ)",)
    assert reasons["HOLOCRON_PROBE_CUSTOM"] == ("HOLOCRON_PROBE_CUSTOM_API_KEY (không hợp lệ)",)


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


def _decode_gray_png(png: bytes) -> bytes:
    """Giải PNG xám 8 bit không lọc do `encode_png_gray` tạo; trả các điểm ảnh."""
    import struct
    import zlib

    pos, idat, width = 8, b"", 0
    while pos < len(png):
        (length,) = struct.unpack(">I", png[pos : pos + 4])
        kind, data = png[pos + 4 : pos + 8], png[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            (width,) = struct.unpack(">I", data[:4])
        if kind == b"IDAT":
            idat += data
        pos += 12 + length
    raw = zlib.decompress(idat)
    return b"".join(raw[i + 1 : i + 1 + width] for i in range(0, len(raw), width + 1))


def test_small_image_has_ink_with_and_without_font(tmp_path: Path) -> None:
    junk = tmp_path / "junk.ttf"
    junk.write_bytes(b"not a font")
    for paths in ((), (str(junk),), pp.FONT_CANDIDATES):
        image = pp.make_small_image(paths)
        assert min(_decode_gray_png(image.png)) < pp.INK_THRESHOLD
        assert image.truth == (pp.TRUTH_TEXT if image.accents else pp.ASCII_TRUTH_TEXT)
    assert not pp.make_small_image(()).accents


def test_big_image_is_square_png() -> None:
    png = pp.make_big_image(512)
    assert png.startswith(b"\x89PNG") and len(png) > 100_000
    assert len(_decode_gray_png(png)) == 512 * 512


def test_models_listing_shapes() -> None:
    assert pp._models_listing(json.dumps({"data": [{"id": "a"}, {"id": 3}, {"name": "b"}]}).encode()) == ["a", "b"]
    assert pp._models_listing(json.dumps({"models": [{"name": "models/g"}]}).encode()) == ["models/g"]
    assert pp._models_listing(b"not json") == []
    assert pp._models_listing(b'{"x": 1}') == []


def test_big_image_row_records_status_and_size() -> None:
    providers, _ = pp.load_providers(
        _env(HOLOCRON_VLM_BASE_URL="https://api.openai.com/v1", HOLOCRON_VLM_API_KEY=KEY_B)
    )
    opener = _FakeOpener([_Response(413, b'{"error":{"message":"too large"}}')])
    prober = pp.Prober(providers, _sender(opener, 3), small=pp.SmallImage(b"png", "x 1", True), big=lambda: b"B" * 2048)
    prober.run([pp.Task("big", 1, "m-vlm")])
    row = prober.row(1, "m-vlm")
    assert row.big_bytes == 2048 and row.big_status is not None and "413" in row.big_status


@pytest.mark.parametrize("error", [OSError("boom"), ValueError("bad"), http.client.IncompleteRead(b"x")])
def test_network_errors_are_recorded_not_raised(error: Exception) -> None:
    class _Raises(_FakeOpener):
        def open(self, fullurl: urllib.request.Request, *, timeout: float) -> _Response:
            raise error

    providers, _ = pp.load_providers(_env())
    prober = pp.Prober(providers, _sender(_Raises([]), 5), small=pp.SmallImage(b"png", "x 1", True))
    prober.run([pp.Task("image", 0, "m-extract"), pp.Task("list", 0, None), pp.Task("big", 0, "m-extract")])
    row = prober.row(0, "m-extract")
    assert row.image_error and row.big_status
    assert prober.listings[0].error


def test_200_with_wrong_shape_is_not_a_match() -> None:
    providers, _ = pp.load_providers(_env())
    opener = _FakeOpener([_Response(200, b"<html>hi</html>")])
    prober = pp.Prober(providers, _sender(opener, 5), small=pp.SmallImage(b"png", "Hóa 1", True))
    prober.run([pp.Task("image", 0, "m-extract"), pp.Task("text", 0, "m-extract")])
    row = prober.row(0, "m-extract")
    assert row.image_accepted is True and row.image_match == "no"
    assert row.text_ok is False and row.text_error


def test_text_answer_needs_the_token_five() -> None:
    providers, _ = pp.load_providers(_env())
    for answer, expected in (("5", True), ("The answer is 5.", True), ("15", False), ("25 or 50", False)):
        prober = pp.Prober(providers, _sender(_FakeOpener([_ok(answer)]), 5), small=pp.SmallImage(b"png", "x 1", True))
        prober.run([pp.Task("text", 0, "m-extract")])
        assert prober.row(0, "m-extract").text_ok is expected


def test_sender_resets_state_and_counts_429_per_request() -> None:
    opener = _FakeOpener([_Response(429, b"{}", {"retry-after": "3"}), _ok("5")])
    sender = _sender(opener, 5)
    request = urllib.request.Request("https://x.test/v1/models")
    assert sender(request, 5)[0] == 429 and sender.last_retry_after == "3"
    assert sender(request, 5)[0] == 200
    assert sender.last_retry_after is None and sender.rate_limited == 1


def test_sender_read_deadline() -> None:
    ticks = iter([0.0, 1.0, 99.0, 99.0])
    sender = pp.CountingSender(3, opener=_FakeOpener([_ok("5")]), monotonic=lambda: next(ticks))
    with pytest.raises(TimeoutError):
        sender(urllib.request.Request("https://x.test/v1/models"), 5.0)


def test_cap_marks_skipped_list_request_as_not_run() -> None:
    providers, _ = pp.load_providers(_env())
    prober = pp.Prober(providers, _sender(_FakeOpener([_ok("5")]), 1), small=pp.SmallImage(b"png", "x 1", True))
    prober.run(pp.build_plan(providers))
    assert prober.listings[0].error == "chưa chạy: hết trần request"


def test_main_validation_errors(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert pp.main(["--max-requests", "0", "--out", str(tmp_path)], env=_env()) == 2
    with pytest.raises(SystemExit) as exit_info:
        pp.main([], env=_env())
    assert exit_info.value.code == 2


def test_random_keys_never_reach_results_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rng = random.Random(11)
    for round_number in range(8):
        key = "".join(rng.choices(string.ascii_uppercase + string.digits, k=rng.randint(8, 40)))
        env = _env(HOLOCRON_LLM_API_KEY=key, HOLOCRON_VLM_API_KEY=key)
        message = f"Incorrect API key {key}; ends {key[-4:]}; https://x.test/{key}"
        body = json.dumps({"error": {"message": message}}).encode()
        out = tmp_path / str(round_number)
        opener = _FakeOpener([_Response(401, body, {"retry-after": key[:6]})])
        assert pp.main(["--live", "--out", str(out)], env=env, sender=_sender(opener, 6)) == 0
        blob = capsys.readouterr().out + "".join(f.read_text(encoding="utf-8") for f in out.iterdir())
        for start in range(len(key) - 3):
            assert key[start : start + 4] not in blob


def test_results_are_written_even_when_run_dies(tmp_path: Path) -> None:
    class _Dies(pp.CountingSender):
        def __call__(self, request: urllib.request.Request, timeout_s: float) -> tuple[int, bytes]:
            raise RuntimeError("unexpected")

    with pytest.raises(RuntimeError):
        pp.main(["--live", "--out", str(tmp_path)], env=_env(), sender=_Dies(3))
    assert "chạy dở" in (tmp_path / "results.md").read_text(encoding="utf-8")
