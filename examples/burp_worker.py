"""Offline CPython companion: one normalized JSON batch in, one result out per line."""

from __future__ import annotations

import json
import sys

from sad import Analyzer, HTTPExchange


def main() -> int:
    analyzer = Analyzer()
    while True:
        line = sys.stdin.buffer.readline(8_000_001)
        if not line:
            return 0
        if len(line) > 8_000_000:
            print(json.dumps({"error": "batch exceeds 8 MB"}), flush=True)
            return 2
        try:
            batch = json.loads(line)
            if not isinstance(batch, list) or len(batch) > 1000:
                raise ValueError("expected a batch of at most 1000 exchanges")
            result = analyzer.analyze_exchanges([HTTPExchange(**item) for item in batch])
            print(json.dumps(result.to_dict()), flush=True)
        except (TypeError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
