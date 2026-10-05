"""Deterministic offline synthetic bundle benchmark. Never executes JavaScript."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from sad import Analyzer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operations", type=int, default=5000)
    parser.add_argument("--output", type=Path, default=Path("artifacts/performance.json"))
    args = parser.parse_args()
    source = ";".join(f'fetch("/benchmark/{index}")' for index in range(args.operations))
    analyzer = Analyzer(max_steps=max(200_000, args.operations * 30))
    samples = []
    for _ in range(3):
        start = perf_counter()
        result = analyzer.analyze_js(source)
        samples.append(perf_counter() - start)
        if len(result.endpoints) != args.operations or result.analysis_incomplete:
            raise AssertionError("benchmark missed operations or exhausted its budget")
    report = {
        "operations": args.operations,
        "seconds": samples,
        "median_seconds": median(samples),
        "parser_misses": analyzer.cache.misses,
        "parser_hits": analyzer.cache.hits,
        "source_bytes": len(source.encode()),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
