# Contributing

Use Python 3.11 or newer and a virtual environment:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
ruff format src tests
ruff check src tests
mypy src/sad
pytest -m "not stress" --cov=sad --cov-branch --cov-report=term-missing --cov-report=json:artifacts/coverage.json
python scripts/reliability_report.py
pytest -m stress
python scripts/benchmark.py
python -m build
python -m twine check dist/*
```

Add a minimal, synthetic fixture for each new behavior, plus a false-positive regression when adding sink recognition. Include expected methods, symbolic URLs, provenance and diagnostics. Never commit customer captures, cookies, authorization headers, secrets, or identifying production source maps.

Keep I/O outside the JavaScript value engine. Network sinks require resolved provenance; matching a method name alone is insufficient. Unknown values must survive as symbolic values or explicit nulls. All new syntax handling must honor the analysis budgets and never execute target code.

Public model changes need a changelog entry, schema update, and compatibility assessment. Before 1.0, incompatible changes require a minor version bump; additive compatible fields may ship in patch releases. Keep `Analyzer`, input adapters, and serialization independent of CLI argument parsing.

Run the complete CI matrix before release. Performance changes should include a representative synthetic bundle measurement and preserve deterministic outputs. See docs/architecture.md for limitations that need contributors.


Normal tests run offline with real socket connections and child-process execution blocked. Do not add xfail to supported corpus cases or reduce independently enforced 95% line/90% branch gates. Keep research cases outside supported recall totals. Upstream fixtures require pinned commits, content hashes, retained licenses and independently reviewed expected operations.
