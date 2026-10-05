"""Offline corpus metrics and independent line/branch coverage gates."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from time import perf_counter

# The corpus is intentionally a development artifact, not part of the installed library.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sad import Analyzer, Dataset, Resource  # noqa: E402
from tests.corpus import CASES  # noqa: E402


def report(coverage_file: Path) -> dict:
    categories: dict[str, dict] = defaultdict(
        lambda: {
            "cases": 0,
            "expected_operations": 0,
            "detected_expected": 0,
            "false_positives": 0,
            "unresolved_cases": 0,
            "seconds": 0.0,
        }
    )
    failures = []
    for case in CASES:
        start = perf_counter()
        result = Analyzer(path_mappings=case.path_mappings).analyze_dataset(
            Dataset(resources=[Resource(name, source) for name, source in case.files.items()])
        )
        elapsed = perf_counter() - start
        actual = {
            (endpoint.method, endpoint.url, endpoint.protocol) for endpoint in result.endpoints
        }
        expected = set(case.expected)
        stats = categories[case.category]
        stats["cases"] += 1
        stats["expected_operations"] += len(expected)
        stats["detected_expected"] += len(actual & expected)
        stats["false_positives"] += len(actual - expected)
        stats["unresolved_cases"] += int(result.analysis_incomplete)
        stats["seconds"] += elapsed
        if actual != expected or not case.issue_codes <= {issue.code for issue in result.issues}:
            failures.append(
                {
                    "case": case.name,
                    "missing": sorted(expected - actual),
                    "unexpected": sorted(actual - expected),
                    "diagnostics": result.diagnostics,
                }
            )
    totals = json.loads(coverage_file.read_text())["totals"]
    lines = 100 * totals["covered_lines"] / totals["num_statements"]
    branches = 100 * totals["covered_branches"] / totals["num_branches"]
    for stats in categories.values():
        stats["recall"] = (
            stats["detected_expected"] / stats["expected_operations"]
            if stats["expected_operations"]
            else None
        )
    return {
        "corpus_cases": len(CASES),
        "multi_file_cases": sum(len(c.files) > 1 for c in CASES),
        "zero_endpoint_negative_cases": sum(
            c.category == "negative" and not c.expected for c in CASES
        ),
        "line_coverage": lines,
        "branch_coverage": branches,
        "categories": dict(categories),
        "failures": failures,
        "passed": not failures
        and lines >= 95
        and branches >= 90
        and len(CASES) >= 400
        and sum(len(c.files) > 1 for c in CASES) >= 60
        and sum(c.category == "negative" and not c.expected for c in CASES) >= 80,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=Path("artifacts/coverage.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/reliability.json"))
    args = parser.parse_args()
    result = report(args.coverage)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        f"{result['corpus_cases']} corpus cases; lines {result['line_coverage']:.2f}%; "
        f"branches {result['branch_coverage']:.2f}%; {len(result['failures'])} failures"
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
