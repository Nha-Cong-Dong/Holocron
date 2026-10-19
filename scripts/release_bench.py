"""Đo các ngưỡng phát hành (không gọi mạng ra ngoài; dùng template pre-computed làm author).

Chạy từ gốc repo: python scripts/release_bench.py [--json bench.json]
In PASS/FAIL cho từng chỉ số, thoát mã 1 nếu có chỉ số FAIL. KHÔNG ĐƯỢC hạ ngưỡng ở file này để làm xanh kết quả.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import math
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from holocron_core.domain.schemas import BlueprintSlot, KnowledgeChunk, QuestionTemplate, TemplateProvenance
from holocron_core.infrastructure.template_library import (
    InMemoryTemplateRegistry,
    load_template_source,
    rr_avg_waiting_template,
)
from holocron_core.sandbox.sandbox import SandboxResult, SandboxStatus, run_template
from holocron_core.services.pipeline import produce_twin

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "holocron" / "backend"
WALKTHROUGH = BACKEND / "scripts" / "run_walkthrough.py"

# Ngưỡng phát hành (xem docs/releases/RELEASE_CHECKLIST.md).
COLD_START_MAX_S = 5.0
TWIN_P95_MAX_S = 3.0
GATE_PATCH_P95_MAX_S = 0.5
SEED_ACCEPT_MIN = 0.10
ABNORMAL_MAX = 0
TWIN_SAMPLES = 30
GATE_PATCH_SAMPLES = 20
SEED_SAMPLES = 200
DETERMINISM_SEEDS = 25
ABNORMAL_STATUSES = frozenset(
    {
        SandboxStatus.CRASHED,
        SandboxStatus.TIMEOUT,
        SandboxStatus.MEMORY_EXCEEDED,
        SandboxStatus.RUNTIME_ERROR,
        SandboxStatus.CONTRACT_ERROR,
        SandboxStatus.AST_REJECTED,
    }
)
RR_TOPIC = "os.cpu_sched.rr.avg_waiting_time"
ROWS = [("P1", "0", "2"), ("P2", "2", "9"), ("P3", "5", "5"), ("P4", "8", "2"), ("P5", "11", "4")]


class PrecomputedAuthor:
    """Author không gọi LLM: luôn trả template pre-computed đã kiểm chứng."""

    def __init__(self, template: QuestionTemplate) -> None:
        self._template = template

    def author(self, slot: BlueprintSlot, feedback: list[str]) -> QuestionTemplate:
        return self._template


def p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http(method: str, url: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # chỉ gọi 127.0.0.1
            text = response.read().decode("utf-8")
            return response.status, json.loads(text) if text.lstrip().startswith(("{", "[")) else None
    except urllib.error.HTTPError as exc:
        return exc.code, None


def _item_body() -> dict[str, Any]:
    cells = [
        {"row": r, "col": c, "text": t, "confidence": 0.97} for r, row in enumerate(ROWS) for c, t in enumerate(row)
    ]
    table = {
        "table_id": "tbl_bench",
        "page_number": 1,
        "headers": ["Tiến trình", "Thời điểm đến", "Burst time"],
        "cells": cells,
    }
    return {
        "source_doc_id": "doc_bench",
        "page_number": 1,
        "topic_key": RR_TOPIC,
        "stem_latex": r"Round Robin với $q = 4$ ms. Tính $\overline{WT}$.",
        "options": {"A": "6.00", "B": "4.20", "C": "2.60", "D": "10.40"},
        "table_primary": table,
        "table_secondary": table,
        "solver_inputs": {"quantum": 4},
    }


def bench_api() -> dict[str, Any]:
    """Cold start uvicorn tới `/docs` và p95 của PATCH bảng Gate (qua HTTP thật, 127.0.0.1)."""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="holocron_bench_") as log_dir:
        log_path = Path(log_dir) / "uvicorn.log"
        with log_path.open("wb") as log:
            started = time.perf_counter()
            server = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "holocron_core.routers.api:app", "--host", "127.0.0.1"]
                + ["--port", str(port), "--log-level", "warning"],
                cwd=BACKEND,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                cold_start: float | None = None
                while time.perf_counter() - started < 30:
                    if server.poll() is not None:
                        break
                    try:
                        status, _ = _http("GET", f"{base}/docs")
                    except OSError:
                        time.sleep(0.05)
                        continue
                    if status == 200:
                        cold_start = time.perf_counter() - started
                        break
                if cold_start is None:
                    tail = log_path.read_text(encoding="utf-8", errors="replace")[-500:]
                    raise RuntimeError(f"uvicorn không khởi động được trong 30 s: {tail}")

                status, item = _http("POST", f"{base}/v1/ingestion/items", _item_body())
                if status != 201:
                    raise RuntimeError(f"tạo item thất bại: HTTP {status}")
                version = int(item["table_primary"]["version"])
                durations: list[float] = []
                for _ in range(GATE_PATCH_SAMPLES):
                    patch = {
                        "editor_id": "bench",
                        "expected_version": version,
                        "patches": [{"row": 0, "col": 2, "new_text": "2"}],
                    }
                    t0 = time.perf_counter()
                    status, item = _http("PATCH", f"{base}/v1/ingestion/items/{item['item_id']}/table", patch)
                    durations.append(time.perf_counter() - t0)
                    if status != 200:
                        raise RuntimeError(f"PATCH thất bại: HTTP {status}")
                    version = int(item["table_primary"]["version"])
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
    return {"cold_start_s": cold_start, "gate_patch_p95_s": p95(durations)}


def _load_fixtures() -> tuple[BlueprintSlot, KnowledgeChunk]:
    """Lấy SLOT và THEORY_CHUNK từ walkthrough của backend (package `scripts` của backend trùng tên với thư mục này)."""
    spec = importlib.util.spec_from_file_location("holocron_walkthrough", WALKTHROUGH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"không nạp được {WALKTHROUGH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    slot: BlueprintSlot = module.SLOT
    chunk: KnowledgeChunk = module.THEORY_CHUNK
    return slot, chunk


def bench_twin() -> float:
    """p95 thời gian sinh một Twin (template → sandbox → TwinQuestion), không tính LLM."""
    author = PrecomputedAuthor(rr_avg_waiting_template(TemplateProvenance.PRECOMPUTED_VERIFIED))
    registry = InMemoryTemplateRegistry()
    slot, chunk = _load_fixtures()
    durations: list[float] = []
    for i in range(TWIN_SAMPLES):
        t0 = time.perf_counter()
        outcome = produce_twin(
            slot,
            blueprint_id="bp_bench",
            citation=chunk.to_citation(),
            author=author,
            registry=registry,
            base_seed=1_000 + i,
        )
        durations.append(time.perf_counter() - t0)
        if outcome.twin is None:
            raise RuntimeError(f"không sinh được Twin ở mẫu {i}: {outcome.ticket}")
    return p95(durations)


def _stable(result: SandboxResult) -> dict[str, Any]:
    return result.model_dump(exclude={"duration_ms"})


def bench_sandbox() -> dict[str, Any]:
    """Tỉ lệ seed được nhận, số lần sandbox thoát bất thường và tính tất định (I4)."""
    source = load_template_source("os_rr_avg_waiting.py")
    results = [run_template(source, seed) for seed in range(SEED_SAMPLES)]
    accepted = sum(1 for r in results if r.status is SandboxStatus.OK)
    abnormal = sum(1 for r in results if r.status in ABNORMAL_STATUSES)
    mismatched = [
        seed for seed in range(DETERMINISM_SEEDS) if _stable(results[seed]) != _stable(run_template(source, seed))
    ]
    return {
        "seed_accept_rate": accepted / SEED_SAMPLES,
        "abnormal_exits": abnormal,
        "determinism_mismatched_seeds": mismatched,
    }


def _row(name: str, value: str, threshold: str, ok: bool) -> dict[str, Any]:
    return {"metric": name, "value": value, "threshold": threshold, "pass": ok}


def run() -> list[dict[str, Any]]:
    api = bench_api()
    twin_p95 = bench_twin()
    sandbox = bench_sandbox()
    return [
        _row(
            "cold start uvicorn → /docs",
            f"{api['cold_start_s']:.2f} s",
            f"≤ {COLD_START_MAX_S} s",
            api["cold_start_s"] <= COLD_START_MAX_S,
        ),
        _row(f"Twin p95 (n={TWIN_SAMPLES})", f"{twin_p95:.2f} s", f"≤ {TWIN_P95_MAX_S} s", twin_p95 <= TWIN_P95_MAX_S),
        _row(
            f"Gate PATCH p95 (n={GATE_PATCH_SAMPLES})",
            f"{api['gate_patch_p95_s'] * 1000:.0f} ms",
            f"≤ {GATE_PATCH_P95_MAX_S * 1000:.0f} ms",
            api["gate_patch_p95_s"] <= GATE_PATCH_P95_MAX_S,
        ),
        _row(
            f"seed accept (n={SEED_SAMPLES})",
            f"{sandbox['seed_accept_rate']:.1%}",
            f"≥ {SEED_ACCEPT_MIN:.0%}",
            sandbox["seed_accept_rate"] >= SEED_ACCEPT_MIN,
        ),
        _row(
            f"sandbox thoát bất thường (n={SEED_SAMPLES})",
            f"{sandbox['abnormal_exits']}",
            f"= {ABNORMAL_MAX}",
            sandbox["abnormal_exits"] <= ABNORMAL_MAX,
        ),
        _row(
            f"I4 tất định ({DETERMINISM_SEEDS} seed × 2 lần)",
            f"{len(sandbox['determinism_mismatched_seeds'])} seed lệch",
            "= 0",
            not sandbox["determinism_mismatched_seeds"],
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # console Windows mặc định cp1252, không in được tiếng Việt
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", dest="json_path", help="ghi kết quả ra file JSON")
    args = parser.parse_args(argv)

    rows = run()
    for row in rows:
        print(f"{'PASS' if row['pass'] else 'FAIL'}  {row['metric']}: {row['value']} (ngưỡng {row['threshold']})")
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps({"passed": all(r["pass"] for r in rows), "results": rows}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return 0 if all(r["pass"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
