"""Đo tra cứu khối offline (ADR-010 mục 18) trên bộ truy vấn tự tạo `tests/fixtures/search_queries.json`.

Không gọi mạng, không cần API key. Đo trên ba đường (in-memory/Python, SQLite có FTS5 + trigram, SQLite không FTS5):
tỉ lệ top-3 đúng và độ trễ p50/p95 mỗi truy vấn. Ngưỡng nghiệm thu: top-3 ≥ 0,90 và p95 < 100 ms (ngưỡng thời gian
chỉ kiểm ở đây, không ở pytest vì phụ thuộc máy).

    python -m scripts.eval_search [--repeat 3] [--out ket_qua.json]

Thoát 0 khi mọi đường đạt cả hai ngưỡng, 1 nếu không.
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

from holocron_core.domain.repositories import AppStore
from holocron_core.infrastructure.memory_store import IngestionStore
from holocron_core.infrastructure.storage import SQLiteStore, sqlite_store
from holocron_core.services.search_index import search_blocks
from tests.test_offline_search import SUBJECT, build_corpus, load_fixture

TOP3_MIN = 0.90
P95_MAX_MS = 100.0


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def measure(store: AppStore, ids: dict[str, str], repeat: int) -> dict[str, Any]:
    queries = load_fixture()["queries"]
    hits = 0
    by_kind: dict[str, list[int]] = {}
    latencies: list[float] = []
    engine = "python"
    for query in queries:
        best = float("inf")
        for _ in range(repeat):
            started = time.perf_counter()
            page = search_blocks(store, subject_code=SUBJECT, query=query["q"], limit=3)
            best = min(best, (time.perf_counter() - started) * 1000)
        latencies.append(best)
        engine = page.engine
        ok = ids[query["expect"]] in [hit.block.block_id for hit in page.hits]
        hits += int(ok)
        by_kind.setdefault(query["kind"], []).append(int(ok))
    return {
        "queries": len(queries),
        "top3": round(hits / len(queries), 4),
        "top3_by_kind": {kind: round(sum(v) / len(v), 4) for kind, v in sorted(by_kind.items())},
        "p50_ms": round(statistics.median(latencies), 3),
        "p95_ms": round(percentile(latencies, 0.95), 3),
        "last_engine": engine,
    }


def main(argv: list[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=3, help="số lần đo mỗi truy vấn (lấy lần nhanh nhất)")
    parser.add_argument("--out", type=Path, default=None, help="ghi kết quả JSON ra file này")
    args = parser.parse_args(argv)
    report: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        stores: dict[str, AppStore] = {"memory": IngestionStore()}
        stores["sqlite_fts5"] = SQLiteStore.open(Path(tmp) / "fts.sqlite3")
        with patch.object(sqlite_store, "fts5_available", lambda conn: False):
            stores["sqlite_python"] = SQLiteStore.open(Path(tmp) / "plain.sqlite3")
        try:
            for name, store in stores.items():
                report[name] = measure(store, build_corpus(store), args.repeat)
        finally:
            for store in stores.values():
                if isinstance(store, SQLiteStore):
                    store.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = all(r["top3"] >= TOP3_MIN and r["p95_ms"] < P95_MAX_MS for r in report.values())
    print("DAT" if passed else f"KHONG DAT (top-3 >= {TOP3_MIN}, p95 < {P95_MAX_MS} ms)")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
