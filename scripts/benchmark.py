"""Deterministic offline synthetic bundle benchmark. Never executes JavaScript."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from sad import Analyzer, Dataset, HTTPExchange, Resource


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operations", type=int, default=5000)
    parser.add_argument("--output", type=Path, default=Path("artifacts/performance.json"))
    parser.add_argument(
        "--scenario",
        choices=("fetch", "symbolic", "wrappers", "captured", "repeated"),
        default="fetch",
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--padding-bytes", type=int, default=0)
    args = parser.parse_args()
    if args.operations < 1 or args.runs < 1 or args.padding_bytes < 0:
        parser.error("operations and runs must be positive; padding must be nonnegative")
    source = ";".join(f'fetch("/benchmark/{index}")' for index in range(args.operations))
    if args.scenario == "symbolic":
        source = ";".join(
            f"fetch(`/benchmark/{index}/${{id}}`)" for index in range(args.operations)
        )
    elif args.scenario == "wrappers":
        source = ";".join(
            f"function f{index}(id){{fetch(`/benchmark/{index}/${{id}}`)}}"
            for index in range(args.operations)
        )
    if args.scenario == "repeated":
        source = ";".join('fetch("/benchmark/same")' for _ in range(args.operations))
    if args.padding_bytes:
        source += "\n/*" + "x" * args.padding_bytes + "*/"
    dataset = Dataset(resources=[Resource("input.js", source)])
    if args.scenario == "captured":
        dataset.exchanges = [HTTPExchange(f"/benchmark/{i}") for i in range(args.operations)]
    analyzer = Analyzer(
        max_steps=max(200_000, args.operations * 100),
        max_resource_bytes=max(8_000_000, len(source.encode())),
    )
    samples = []
    for _ in range(args.runs):
        start = perf_counter()
        result = analyzer.analyze_dataset(dataset)
        samples.append(perf_counter() - start)
        expected_partial = args.scenario in ("symbolic", "wrappers")
        expected_endpoints = 1 if args.scenario == "repeated" else args.operations
        if (
            len(result.endpoints) != expected_endpoints
            or result.analysis_incomplete != expected_partial
        ):
            raise AssertionError("benchmark missed operations or exhausted its budget")
    report = {
        "scenario": args.scenario,
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
